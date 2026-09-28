"""Code-first resolver tier (D0) for the flowering pool.

stdlib only. No network, no Ollama. Each resolver reads a pool record
(the fixture's excerpts) and returns an answer or ``ESCALATE``; only an
escalated act would ever reach a model. The number that matters is
resolver *coverage* (acts closed by code) and resolver *precision* (a
resolved answer that is wrong is a defect, not a score).

Rework (753B6124, per Loki audit 174F4F2A on D288B449 12704eb):

- The excerpt grammar is now a **closed** set of key/value shapes, not
  "any word before a space or colon." A key only becomes a field when
  its whole segment matches one of two forms: a *token* key
  (``id``, ``dispatch_id``, ``to_app``, ``topic``, ``status``,
  ``asked_count``) followed by exactly one whitespace-delimited token,
  or a *text* key (``summary``, ``title``, ``source_ref``, ``question``)
  followed immediately by ``:``. Prose that merely contains a key word
  (``"title of the PR was wrong"``, ``"to_app is unclear here"``) does
  not match either shape and is ignored, never guessed.
- A text key whose value is followed (in the same excerpt) by a
  ``;``-delimited remainder that does **not** parse as a fresh key is
  marked *suspect* -- the ``;`` most likely fell inside the true value
  and cut it short, so the resolver escalates that field instead of
  trusting a truncated string.
- Two excerpts (or two segments) disagreeing on the same key mark that
  key *conflicting* -- the resolver escalates rather than picking
  either value.
- A malformed fixture (unresolvable shape, non-dict, non-string
  excerpt text) escalates that one row; it no longer aborts the whole
  ``resolve_fixtures_dir`` run.
- ``summarize()`` reports ``resolved_total``/``resolved_correct``
  counting only ``status == resolved`` rows; ``verbatim`` and
  ``flowering_required`` get their own ``*_total``/``*_correct`` pairs
  so a number named "resolved" cannot include something else.
- G5's scorer now checks ``must_cite`` instead of "any cite present";
  G2's scorer prefers an exact match against ``expected.reference_title``
  when given.

D0 follow-ups (Loki audit 7EA73431 on 753B6124 5705519):

- A G4 row with no or conflicting ``to_app`` is a correct refusal, not a
  wrong answer -- ``score_resolution`` now leaves it unscored (like an
  escalate) instead of comparing an empty answer against
  ``expected.builder_seat`` and failing it.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot.deterministic.policy import Policy

STATUS_RESOLVED = "resolved"
STATUS_VERBATIM = "verbatim"
STATUS_FLOWERING_REQUIRED = "flowering_required"
STATUS_ESCALATE = "escalate"

_SCORED_STATUSES = (STATUS_RESOLVED, STATUS_VERBATIM, STATUS_FLOWERING_REQUIRED)

# Ellipsis (ASCII "..." or the single Unicode character) is truncation,
# never a terminal mark, even though the ASCII form ends in ".".
_ELLIPSIS_SUFFIXES = ("...", "…")
_TERMINAL_PUNCTUATION = (".", "?", "!")

# Token keys: bare "key value" -- the value must be exactly one
# whitespace-delimited token, end to end, or the segment does not match
# at all (this is what keeps "to_app is unclear here" from parsing).
_TOKEN_KEYS = ("dispatch_id", "to_app", "topic", "status", "asked_count", "id")
# Text keys: "key: value" -- colon must sit directly against the key.
_TEXT_KEYS = ("summary", "title", "source_ref", "question")

_TOKEN_SEGMENT_RE = re.compile(
    r"^(?:" + "|".join(_TOKEN_KEYS) + r")\s+(\S+)$"
)
_TOKEN_KEY_RE = re.compile(r"^(" + "|".join(_TOKEN_KEYS) + r")\s+\S+$")
_TEXT_SEGMENT_RE = re.compile(
    r"^(" + "|".join(_TEXT_KEYS) + r"):\s*(.+)$"
)

# Hex-id-shaped tokens: 8+ contiguous hex characters, not glued to a
# surrounding alnum character (an adjoining "_" is fine -- ids show up
# as "gap_<id>" in prose). No upper bound, so a 16+ char id is caught
# too -- length alone no longer decides whether something looks like an
# id. Case is normalized (compared lower) so an uppercased real id is
# never mistaken for a stray one.
_HEX_RUN_RE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{8,}(?![0-9a-zA-Z])")
_HEX_LETTERS = set("abcdefABCDEF")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _looks_like_hex_id(token: str) -> bool:
    """A pure-decimal run (a date, a decimal fraction's digits) is never
    an id -- an id-shaped token must carry at least one a-f hex letter."""
    return any(c in _HEX_LETTERS for c in token)


def _find_hex_ids(text: str) -> set[str]:
    return {
        tok for tok in _HEX_RUN_RE.findall(text or "") if _looks_like_hex_id(tok)
    }


def _is_truncated(text: str) -> bool:
    """True if ``text`` looks cut off rather than a complete sentence.

    An ellipsis (ASCII ``...`` or the single-character ``…``) always
    means truncated, even though the ASCII form ends in a period. Past
    that, anything not ending in one of ``.?!`` is truncated -- this
    also covers a bare mid-word cut (no trailing punctuation at all).
    """
    stripped = (text or "").rstrip()
    if not stripped:
        return True
    if stripped.endswith(_ELLIPSIS_SUFFIXES):
        return True
    return not stripped.endswith(_TERMINAL_PUNCTUATION)


def _parse_segment(segment: str) -> tuple[str, str] | None:
    """Match one ``;``-delimited segment against the closed grammar.

    Returns ``(key, value)`` only for an exact token- or text-key shape.
    Anything else -- prose that merely contains a key word, a key with
    the wrong separator, an empty value -- is unparseable and returned
    as ``None``; the caller must never guess past that.
    """
    segment = segment.strip()
    if not segment:
        return None
    m = _TEXT_SEGMENT_RE.match(segment)
    if m:
        key, value = m.group(1), m.group(2).strip()
        return (key, value) if value else None
    m = _TOKEN_KEY_RE.match(segment)
    if m:
        key = m.group(1)
        value_m = _TOKEN_SEGMENT_RE.match(segment)
        value = value_m.group(1).strip() if value_m else ""
        return (key, value) if value else None
    return None


def parse_excerpt_fields(text: str) -> dict[str, str]:
    """Parse one excerpt's ``;``-separated text into a fields dict.

    Kept as a single-excerpt convenience wrapper around the same closed
    grammar `_parse_excerpts` uses; does not carry conflict or suspect
    tracking (there is nothing to conflict with inside one call).
    """
    fields: dict[str, str] = {}
    for raw_segment in (text or "").split(";"):
        parsed = _parse_segment(raw_segment)
        if parsed is None:
            continue
        key, value = parsed
        if key not in fields:
            fields[key] = value
    return fields


class _ParsedExcerpts:
    __slots__ = ("fields", "source", "conflicts", "suspect")

    def __init__(
        self,
        fields: dict[str, str],
        source: dict[str, str],
        conflicts: set[str],
        suspect: set[str],
    ) -> None:
        self.fields = fields
        self.source = source
        self.conflicts = conflicts
        self.suspect = suspect


def _parse_excerpts(excerpts: list) -> _ParsedExcerpts:
    """Merge fields across all excerpts under the closed grammar.

    - ``conflicts``: keys where two segments (same or different excerpt)
      disagree on the value -- the resolver must escalate, never pick.
    - ``suspect``: text keys whose value segment was immediately
      followed by a segment that failed to parse as a fresh key -- most
      likely a ``;`` inside the true value cut it short. The resolver
      must escalate rather than trust the truncated string.
    """
    fields: dict[str, str] = {}
    source: dict[str, str] = {}
    conflicts: set[str] = set()
    suspect: set[str] = set()

    for ex in excerpts or []:
        if not isinstance(ex, dict):
            continue
        eid = ex.get("id", "")
        raw_text = ex.get("text", "")
        if not isinstance(raw_text, str):
            continue
        pending_text_key: str | None = None
        for raw_segment in raw_text.split(";"):
            segment = raw_segment.strip()
            if not segment:
                pending_text_key = None
                continue
            parsed = _parse_segment(segment)
            if parsed is None:
                if pending_text_key is not None:
                    suspect.add(pending_text_key)
                pending_text_key = None
                continue
            key, value = parsed
            if key in fields:
                if fields[key] != value:
                    conflicts.add(key)
            else:
                fields[key] = value
                source[key] = eid
            pending_text_key = key if key in _TEXT_KEYS else None

    return _ParsedExcerpts(fields, source, conflicts, suspect)


def _record(
    fixture_id: str,
    cls: str,
    status: str,
    answer: Any,
    cites: list[str],
    reason: str | None,
) -> dict:
    return {
        "fixture_id": fixture_id,
        "class": cls,
        "status": status,
        "answer": answer,
        "cites": list(cites),
        "reason": reason,
    }


def _resolve_g1(fixture_id: str, parsed: _ParsedExcerpts) -> dict:
    if "to_app" in parsed.conflicts:
        return _record(fixture_id, "G1", STATUS_ESCALATE, None, [], "conflicting_field")
    to_app = parsed.fields.get("to_app")
    if not to_app:
        return _record(fixture_id, "G1", STATUS_ESCALATE, None, [], "no_to_app_field")
    cite = [parsed.source["to_app"]] if "to_app" in parsed.source else []
    return _record(fixture_id, "G1", STATUS_RESOLVED, to_app, cite, None)


def _resolve_g2(fixture_id: str, parsed: _ParsedExcerpts, expected: dict) -> dict:
    if "title" in parsed.conflicts:
        return _record(fixture_id, "G2", STATUS_ESCALATE, None, [], "conflicting_field")
    title = parsed.fields.get("title")
    if title is None:
        return _record(fixture_id, "G2", STATUS_ESCALATE, None, [], "no_title_field")
    if "title" in parsed.suspect:
        return _record(
            fixture_id, "G2", STATUS_ESCALATE, None, [], "value_split_by_semicolon"
        )
    try:
        max_chars = int(expected.get("max_chars", 120)) if expected else 120
    except (TypeError, ValueError):
        max_chars = 120
    if len(title) > max_chars:
        return _record(fixture_id, "G2", STATUS_ESCALATE, None, [], "title_over_limit")
    cite = [parsed.source["title"]] if "title" in parsed.source else []
    return _record(fixture_id, "G2", STATUS_RESOLVED, title, cite, None)


def _resolve_g3(fixture_id: str, parsed: _ParsedExcerpts) -> dict:
    if "id" in parsed.conflicts or "question" in parsed.conflicts:
        return _record(fixture_id, "G3", STATUS_ESCALATE, None, [], "conflicting_field")
    gid = parsed.fields.get("id")
    question = parsed.fields.get("question")
    if not gid or not question:
        return _record(
            fixture_id, "G3", STATUS_ESCALATE, None, [], "missing_id_or_question"
        )
    if "question" in parsed.suspect:
        return _record(
            fixture_id, "G3", STATUS_ESCALATE, None, [], "value_split_by_semicolon"
        )
    if _is_truncated(question):
        return _record(fixture_id, "G3", STATUS_ESCALATE, None, [], "source_truncated")
    answer = f"{gid}: {question}"
    return _record(fixture_id, "G3", STATUS_RESOLVED, answer, [gid], None)


def _resolve_g4(fixture_id: str, parsed: _ParsedExcerpts) -> dict:
    if "to_app" in parsed.conflicts:
        return _record(
            fixture_id, "G4", STATUS_FLOWERING_REQUIRED, {}, [], "conflicting_field"
        )
    answer: dict[str, str] = {}
    if "to_app" in parsed.fields:
        answer["builder_seat"] = parsed.fields["to_app"]
    return _record(fixture_id, "G4", STATUS_FLOWERING_REQUIRED, answer, [], None)


def _resolve_g5(fixture_id: str, excerpts: list) -> dict:
    first = None
    for ex in excerpts or []:
        if isinstance(ex, dict):
            first = ex
            break
    if first is None:
        return _record(fixture_id, "G5", STATUS_ESCALATE, None, [], "no_excerpt")
    eid = first.get("id", "")
    text = first.get("text", "")
    return _record(
        fixture_id,
        "G5",
        STATUS_VERBATIM,
        text,
        [eid] if eid else [],
        "sealed_fact_required_for_answer",
    )


def resolve_fixture(fixture: dict) -> dict:
    """Resolve one fixture dict.

    A malformed fixture (not a dict, missing/odd-typed fields) escalates
    with a ``malformed_fixture`` reason rather than raising -- resolving
    one row is never allowed to abort the run. An unparseable or missing
    excerpt field escalates too; it is never guessed.
    """
    if not isinstance(fixture, dict):
        return _record("", "", STATUS_ESCALATE, None, [], "malformed_fixture")

    fixture_id = fixture.get("id", "")
    cls = fixture.get("class", "")
    excerpts = fixture.get("excerpts") or []
    if not isinstance(excerpts, list):
        excerpts = []
    expected = fixture.get("expected") or {}
    if not isinstance(expected, dict):
        expected = {}
    parsed = _parse_excerpts(excerpts)

    if cls == "G1":
        return _resolve_g1(fixture_id, parsed)
    if cls == "G2":
        return _resolve_g2(fixture_id, parsed, expected)
    if cls == "G3":
        return _resolve_g3(fixture_id, parsed)
    if cls == "G4":
        return _resolve_g4(fixture_id, parsed)
    if cls == "G5":
        return _resolve_g5(fixture_id, excerpts)
    return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "unknown_class")


def _allowed_ids(excerpts: list) -> set[str]:
    """Hex-id-shaped tokens that legitimately appear in this fixture's
    excerpts (the excerpt's own id and any id-shaped token in its text),
    normalized to lowercase so case never decides a match."""
    allowed: set[str] = set()
    for ex in excerpts or []:
        if not isinstance(ex, dict):
            continue
        eid = ex.get("id", "")
        if eid:
            allowed.add(eid.lower())
            if eid.startswith("ex-"):
                allowed.add(eid[3:].lower())
        text = ex.get("text", "")
        if isinstance(text, str):
            allowed |= {tok.lower() for tok in _find_hex_ids(text)}
    return allowed


def score_resolution(fixture: dict, resolution: dict) -> dict:
    """Deterministic scorer against the fixture's ``expected`` block.

    An ``escalate`` row is never scored -- escalating is never a
    failure. Everything else (``resolved``, ``verbatim``,
    ``flowering_required``) is checked against ``expected``, and every
    one of those checks can fail: a ``verbatim`` row fails if it does
    not cite what ``expected.must_cite`` names, and a
    ``flowering_required`` row fails if its ``builder_seat`` is wrong or
    absent.
    """
    status = resolution.get("status")
    if status == STATUS_ESCALATE:
        return {"scored": False, "correct": None, "detail": "escalate_not_scored"}

    if not isinstance(fixture, dict):
        return {"scored": False, "correct": None, "detail": "malformed_fixture"}
    expected = fixture.get("expected") or {}
    if not isinstance(expected, dict):
        expected = {}
    cls = resolution.get("class")
    cites = resolution.get("cites") or []
    answer = resolution.get("answer")

    if cls == "G1":
        must_cite = expected.get("must_cite") or []
        ok = answer == expected.get("to_app") and set(must_cite).issubset(set(cites))
        return {"scored": True, "correct": ok, "detail": "g1_to_app_and_cite"}

    if cls == "G2":
        try:
            max_chars = int(expected.get("max_chars", 120))
        except (TypeError, ValueError):
            max_chars = 120
        must_name = expected.get("must_name") or []
        text = answer or ""
        reference_title = expected.get("reference_title")
        if reference_title:
            ok = text == reference_title
        else:
            ok = len(text) <= max_chars and all(name in text for name in must_name)
        return {"scored": True, "correct": ok, "detail": "g2_title_match"}

    if cls == "G3":
        must_cite = expected.get("must_cite") or []
        ok_cite = set(must_cite).issubset(set(cites))
        text = answer or ""
        allowed = _allowed_ids(fixture.get("excerpts") or [])
        found = {tok.lower() for tok in _find_hex_ids(text)}
        stray = found - allowed
        ok = ok_cite and not stray
        return {"scored": True, "correct": ok, "detail": "g3_cite_and_no_stray_ids"}

    if cls == "G4":
        if not isinstance(answer, dict) or not answer.get("builder_seat"):
            # A G4 row with no or conflicting to_app is a correct refusal
            # -- there is nothing to check it against, so it is never
            # scored, the same as an escalate (Loki 7EA73431 F1).
            return {"scored": False, "correct": None, "detail": "g4_refusal_not_scored"}
        ok = answer.get("builder_seat") == expected.get("builder_seat")
        return {"scored": True, "correct": ok, "detail": "g4_builder_seat"}

    if cls == "G5":
        must_cite = expected.get("must_cite") or []
        ok = bool(cites) and set(must_cite).issubset(set(cites))
        return {"scored": True, "correct": ok, "detail": "g5_must_cite"}

    return {"scored": False, "correct": None, "detail": "unknown_class"}


def _fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_fixture_files(fixtures_dir: Path) -> list[Path]:
    return sorted(Path(fixtures_dir).glob("S-growth-*.json"))


def resolve_fixtures_dir(fixtures_dir: Path) -> list[dict]:
    """Resolve + score every ``S-growth-*.json`` fixture under a dir.

    Returns one row per fixture: the resolution record plus ``scored``,
    ``correct``, ``score_detail``, and ``fixture_sha256``. A fixture that
    cannot be read as JSON, or whose shape blows up the resolver in a
    way `resolve_fixture`'s own guards did not anticipate, becomes a
    single ``escalate`` row with a ``resolver_error``/``fixture_read_error``
    reason -- it never aborts the rest of the run.
    """
    rows: list[dict] = []
    for fp in list_fixture_files(fixtures_dir):
        try:
            fixture = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append(
                {
                    "fixture_id": fp.stem,
                    "class": None,
                    "status": STATUS_ESCALATE,
                    "answer": None,
                    "cites": [],
                    "reason": f"fixture_read_error: {exc}",
                    "scored": False,
                    "correct": None,
                    "score_detail": "fixture_unreadable",
                    "fixture_sha256": None,
                }
            )
            continue

        try:
            resolution = resolve_fixture(fixture)
            score = score_resolution(fixture, resolution)
        except Exception as exc:  # noqa: BLE001 -- one row's defect, not the run's
            fid = fixture.get("id", fp.stem) if isinstance(fixture, dict) else fp.stem
            resolution = _record(
                fid, None, STATUS_ESCALATE, None, [], f"resolver_error: {exc!r}"
            )
            score = {"scored": False, "correct": None, "detail": "resolver_error"}

        row = dict(resolution)
        row["scored"] = score["scored"]
        row["correct"] = score["correct"]
        row["score_detail"] = score["detail"]
        row["fixture_sha256"] = _fixture_sha256(fp)
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    """Counts by status, plus a ``{status}_total``/``{status}_correct``
    pair for each scored status (``resolved``, ``verbatim``,
    ``flowering_required``) -- a number named ``resolved`` never
    includes a verbatim or flowering_required row."""
    n = len(rows)
    by_status: dict[str, int] = {}
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        status = row.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        if row.get("scored"):
            bucket = buckets.setdefault(status, {"total": 0, "correct": 0})
            bucket["total"] += 1
            if row.get("correct"):
                bucket["correct"] += 1
    summary: dict[str, Any] = {"n": n, "by_status": by_status}
    for status in _SCORED_STATUSES:
        bucket = buckets.get(status, {"total": 0, "correct": 0})
        summary[f"{status}_total"] = bucket["total"]
        summary[f"{status}_correct"] = bucket["correct"]
    return summary


def run_resolve(
    policy: Policy, *, fixtures_dir: Path, out_path: Path | None = None
) -> dict:
    """Resolve every fixture in ``fixtures_dir``, write one JSONL row per
    fixture, and return ``{"summary": ..., "rows": ..., "out_path": ...}``.

    Writes under ``policy.runs_dir`` as ``resolve-<UTC stamp>.jsonl``
    unless ``out_path`` is given.
    """
    fixtures_dir = Path(fixtures_dir).resolve()
    rows = resolve_fixtures_dir(fixtures_dir)
    if out_path is None:
        out_path = policy.runs_dir / f"resolve-{_utc_stamp()}.jsonl"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    summary = summarize(rows)
    summary["fixtures_dir"] = str(fixtures_dir)
    summary["out_path"] = str(out_path)
    return {"summary": summary, "rows": rows, "out_path": out_path}
