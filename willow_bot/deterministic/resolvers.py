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

Routing rule (gap 82897b2f507e):

- A G1 act whose brief asks to *route* an item (the brief opens with
  ``Route``) is not a field read. It closes in code to ``willow`` with
  status ``routed``: operator 2026-09-29, "Everything goes through
  Willow. Willow is the orchestrator." (draft pair be0b6a1e). A field
  read ("Name the to_app ...") with no ``to_app`` field still escalates.
- ``routed`` is its own scored bucket. It is scored against the ruling
  (answer is ``willow``, cites cover ``must_cite``), not against a frozen
  ``expected.to_app`` that predates the ruling, so ``resolved``
  precision stays a measurement against the fixture's own gold.

The table act (box-build SPEC ruling R12; SPEC-documented §11 J6, C13):

- A table act closes in code or escalates; a model never rolls, and no
  resolver here draws a number. The classes are namespaced ``table.roll``,
  ``table.rules`` and ``table.state`` (C13: never ``G6``; the operator may
  add classes before a run, not after, and the flowering battery's scorer
  never sees them). Their excerpts are served record rows, one JSON object
  per line (``escalate.act_of`` canons each served row), so every answer
  cites the served table id the row came from.
- ``table.roll: turn N [session S]`` answers ``{"total", "verdict"}`` from
  exactly one served ``verdict`` row for that (session, turn); none is
  ``no_roll_on_record``, two that disagree are ``conflicting_field``, a total
  off its band ``total_band_mismatch``, a brief with two turns or no
  session over two served sessions ``ambiguous_brief`` / ``ambiguous_session``.
- ``table.rules: <bucket>`` answers the band text a served LORE fact gives
  for that bucket (``rule: weak 7-9 ...``, the fact read whole); absent is
  ``no_rule_on_record``, two readings are ``conflicting_field``. The code's
  own bands (``BANDS``, from ``story_session.py``'s strong 10+ / weak 7-9 /
  miss 6-) name the buckets a brief may ask for; they are never the answer,
  which always cites a row.
- ``table.state: tally [session S]`` answers the tally and path of one
  session's served roll rows in turn order; a gap is ``turns_missing``.
- R20: when a table act's D0 escalates, ``escalate.run_escalate`` sends it
  straight to the human card; no model rung ever sees a table act.
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
STATUS_ROUTED = "routed"

_SCORED_STATUSES = (
    STATUS_RESOLVED,
    STATUS_VERBATIM,
    STATUS_FLOWERING_REQUIRED,
    STATUS_ROUTED,
)

ROUTE_TARGET = "willow"
_ROUTE_BRIEF_RE = re.compile(r"^\s*Route\b")

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

# The table act (R12, C13): namespaced classes, never a G number. Its grammar
# is its own (`_RULE_RE` over a lore fact, `_TURN_RE`/`_SESSION_RE` over the
# brief) and never touches the closed G-class grammar above (B3).
TABLE_PREFIX = "table."
TABLE_CLASSES = ("table.roll", "table.rules", "table.state")
#: The buckets a table brief may name, as story_session.py reads a 2d6+stat
#: sum (strong 10+, weak 7-9, miss 6-). Names only: the answer to a rules act
#: is always a served row's text, never this table.
BANDS = {"strong": "10+", "weak": "7-9", "miss": "6-"}
_TURN_RE = re.compile(r"\bturn\s+(\d+)\b", re.IGNORECASE)
_SESSION_RE = re.compile(r"\bsession\s+(\S+)", re.IGNORECASE)
_RULE_RE = re.compile(
    r"^\s*rule:\s*(strong|weak|miss)\b\s*(.*?)\s*$", re.IGNORECASE | re.DOTALL
)

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


def is_route_act(fixture: dict) -> bool:
    """A G1 brief that opens with ``Route`` asks where an item goes, not
    what its ``to_app`` field says."""
    brief = fixture.get("brief") if isinstance(fixture, dict) else None
    return isinstance(brief, str) and bool(_ROUTE_BRIEF_RE.match(brief))


def _excerpt_ids(excerpts: list) -> list[str]:
    return [
        ex["id"]
        for ex in excerpts or []
        if isinstance(ex, dict) and isinstance(ex.get("id"), str) and ex["id"]
    ]


def _route_g1(fixture_id: str, excerpts: list) -> dict:
    cites = _excerpt_ids(excerpts)
    if not cites:
        return _record(fixture_id, "G1", STATUS_ESCALATE, None, [], "no_excerpt")
    return _record(
        fixture_id,
        "G1",
        STATUS_ROUTED,
        ROUTE_TARGET,
        cites,
        "routing_goes_through_willow",
    )


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


# ---------------------------------------------------------------------------
# The table act (R12, C13): code reads served rows; it never rolls
# ---------------------------------------------------------------------------
#
# The table path has its own grammar (W3b audit B3): it never widens the
# closed G-class excerpt grammar above. It reads served rows only -- a JSON
# object per excerpt line -- and takes from them only what code wrote:
#   * a roll: kind `verdict`, `verdict` in BANDS, int `turn` (never a bool),
#     int `total` on the row that agrees with the band (S6: nothing is parsed
#     out of prose, ever), and a `session` string;
#   * a rule: kind `lore`, a `fact` that is `rule: <bucket> <text>` whole
#     (B2: `_RULE_RE` over the fact alone; model text in any other row --
#     an escalate row's path, a claim -- never feeds a resolver).
# Rows key on (session, turn) (B4): a brief may name `session <id>`; when it
# does not and more than one session is served, the act is ambiguous.


def is_table_class(cls: object) -> bool:
    return isinstance(cls, str) and cls.startswith(TABLE_PREFIX)


def _served_rows(excerpts: list) -> list[tuple[str, dict]]:
    """``(excerpt id, row)`` for every line of every excerpt that is a JSON
    object: the shape ``escalate.act_of`` gives a served table (one canon row
    per line). A line that is not one is skipped, never guessed at."""
    out: list[tuple[str, dict]] = []
    for ex in excerpts or []:
        if not isinstance(ex, dict) or not isinstance(ex.get("text"), str):
            continue
        eid = ex.get("id", "") if isinstance(ex.get("id"), str) else ""
        for line in ex["text"].split("\n"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append((eid, row))
    return out


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def bucket(total: int) -> str:
    """story_session.py's read of a 2d6+stat sum: strong 10+, weak 7-9, miss 6-."""
    return "strong" if total >= 10 else "weak" if total >= 7 else "miss"


def _roll_rows(excerpts: list) -> tuple[list[tuple[str, dict]], str | None]:
    """The served rows that record a roll, and the first reason one of them
    cannot be trusted (``total_band_mismatch``, ``no_total_on_record``): a
    roll row is code's, so a total missing or off its band is a defect to
    escalate, never a row to skip."""
    rolls, bad = [], None
    for eid, row in _served_rows(excerpts):
        kind = row.get("what") or row.get("kind")
        if kind != "verdict" or row.get("verdict") not in BANDS:
            continue
        if not _is_int(row.get("turn")):
            continue
        total = row.get("total")
        if not _is_int(total):
            bad = bad or "no_total_on_record"
            continue
        if bucket(total) != row["verdict"]:
            bad = bad or "total_band_mismatch"
            continue
        rolls.append((eid, row))
    return rolls, bad


def _session_of(row: dict) -> str:
    s = row.get("session")
    return s if isinstance(s, str) else ""


def _pick_session(
    brief: str, rolls: list[tuple[str, dict]]
) -> tuple[str | None, str | None]:
    """The session the act is about: the one the brief names, else the only
    one served. ``(session, reason)``; ``reason`` is set when none can be
    picked (``ambiguous_session``, ``no_session_on_record``)."""
    named = _SESSION_RE.findall(brief or "")
    if len(named) > 1:
        return None, "ambiguous_brief"
    served = {_session_of(r) for _, r in rolls}
    if named:
        return (named[0], None) if named[0] in served else (None, "no_session_on_record")
    if len(served) > 1:
        return None, "ambiguous_session"
    return (next(iter(served)), None) if served else (None, "no_session_on_record")


def _resolve_table_roll(fixture_id: str, brief: str, excerpts: list) -> dict:
    """``table.roll: turn N [session S]`` -> ``{"total", "verdict"}`` from the
    one served verdict row for that (session, turn), citing its table id.
    Never draws a number, never reads one out of prose."""
    cls = "table.roll"
    turns = _TURN_RE.findall(brief or "")
    if len(turns) > 1:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "ambiguous_brief")
    if not turns:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "no_turn_in_brief")
    turn = int(turns[0])
    rolls, bad = _roll_rows(excerpts)
    session, why = _pick_session(brief, rolls)
    if why is not None and rolls:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], why)
    found = [
        (eid, r) for eid, r in rolls if r["turn"] == turn and _session_of(r) == session
    ]
    if not found:
        return _record(
            fixture_id, cls, STATUS_ESCALATE, None, [], bad or "no_roll_on_record"
        )
    answers = {(r["total"], r["verdict"]) for _, r in found}
    if len(answers) > 1:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "conflicting_field")
    total, verdict = answers.pop()
    cites = list(dict.fromkeys(eid for eid, _ in found if eid))
    return _record(
        fixture_id, cls, STATUS_RESOLVED, {"total": total, "verdict": verdict}, cites, None
    )


def _rule_readings(excerpts: list) -> list[tuple[str, str, str]]:
    """``(excerpt id, bucket, band text)`` from served LORE facts only, each
    fact read whole against ``_RULE_RE``. No other row, field or segment is a
    rule (B2)."""
    out = []
    for eid, row in _served_rows(excerpts):
        if (row.get("what") or row.get("kind")) != "lore":
            continue
        fact = row.get("fact")
        if isinstance(fact, str) and (m := _RULE_RE.match(fact)):
            out.append((eid, m.group(1).lower(), m.group(2)))
    return out


def _resolve_table_rules(fixture_id: str, brief: str, excerpts: list) -> dict:
    """``table.rules: <bucket>`` -> the band text a served lore rule gives."""
    cls = "table.rules"
    words = [w.lower().strip(".,:;?!") for w in (brief or "").split()]
    buckets = list(dict.fromkeys(w for w in words if w in BANDS))
    if len(buckets) > 1:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "ambiguous_brief")
    if not buckets:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "no_bucket_in_brief")
    found = [(eid, t) for eid, b, t in _rule_readings(excerpts) if b == buckets[0]]
    if not found:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "no_rule_on_record")
    texts = {" ".join(t.split()) for _, t in found}
    if len(texts) > 1:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "conflicting_field")
    cites = list(dict.fromkeys(eid for eid, _ in found if eid))
    return _record(fixture_id, cls, STATUS_RESOLVED, texts.pop(), cites, None)


def _resolve_table_state(fixture_id: str, brief: str, excerpts: list) -> dict:
    """``table.state: tally [session S]`` -> the tally and path of one
    session's served roll rows in turn order; a gap in the turns is
    ``turns_missing``."""
    cls = "table.state"
    rolls, bad = _roll_rows(excerpts)
    if not rolls:
        return _record(
            fixture_id, cls, STATUS_ESCALATE, None, [], bad or "no_roll_on_record"
        )
    session, why = _pick_session(brief, rolls)
    if why is not None:
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], why)
    by_turn: dict[int, set[tuple[int, str]]] = {}
    cites: list[str] = []
    for eid, r in rolls:
        if _session_of(r) != session:
            continue
        by_turn.setdefault(r["turn"], set()).add((r["total"], r["verdict"]))
        if eid:
            cites.append(eid)
    if any(len(v) > 1 for v in by_turn.values()):
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "conflicting_field")
    turns = sorted(by_turn)
    if turns != list(range(turns[0], turns[-1] + 1)):
        return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "turns_missing")
    tally = {b: 0 for b in BANDS}
    path = []
    for t in turns:
        ((_, v),) = by_turn[t]
        tally[v] += 1
        path.append(v)
    answer = {**tally, "turns": len(turns), "path": "-".join(path), "session": session}
    return _record(
        fixture_id, cls, STATUS_RESOLVED, answer, list(dict.fromkeys(cites)), None
    )


def resolve_table(fixture: dict) -> dict:
    """Dispatch one ``table.*`` act (R12). An unknown ``table.`` class
    escalates as ``unknown_class`` like any other; nothing here rolls."""
    fixture_id = fixture.get("id", "")
    cls = fixture.get("class", "")
    brief = fixture.get("brief") if isinstance(fixture.get("brief"), str) else ""
    excerpts = fixture.get("excerpts") or []
    if not isinstance(excerpts, list):
        excerpts = []
    if cls == "table.roll":
        return _resolve_table_roll(fixture_id, brief, excerpts)
    if cls == "table.rules":
        return _resolve_table_rules(fixture_id, brief, excerpts)
    if cls == "table.state":
        return _resolve_table_state(fixture_id, brief, excerpts)
    return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "unknown_class")


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
        if is_route_act(fixture):
            return _route_g1(fixture_id, excerpts)
        return _resolve_g1(fixture_id, parsed)
    if cls == "G2":
        return _resolve_g2(fixture_id, parsed, expected)
    if cls == "G3":
        return _resolve_g3(fixture_id, parsed)
    if cls == "G4":
        return _resolve_g4(fixture_id, parsed)
    if cls == "G5":
        return _resolve_g5(fixture_id, excerpts)
    if is_table_class(cls):  # R12 / C13: the table act, before unknown_class
        return resolve_table(fixture)
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

    if cls == "G1" and status == STATUS_ROUTED:
        must_cite = expected.get("must_cite") or []
        ok = answer == ROUTE_TARGET and set(must_cite).issubset(set(cites))
        return {"scored": True, "correct": ok, "detail": "g1_route_through_willow"}

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
        # A G2 fixture that names must_cite scores cites too, as G1/G3/G5
        # do; before this, a title drawn from the wrong excerpt passed
        # (Loki AEC0E753, gap da93c749120b). v2's 06 names none, so its
        # score is unchanged.
        must_cite = expected.get("must_cite") or []
        ok = ok and set(must_cite).issubset(set(cites))
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

    if is_table_class(cls):
        # A table act is scored against its own gold (`expected.answer`) and
        # cites; a fixture with no gold is not scored. It stays out of the
        # flowering battery's G-class buckets (C13).
        if "answer" not in expected:
            return {"scored": False, "correct": None, "detail": "table_no_gold"}
        must_cite = expected.get("must_cite") or []
        ok = answer == expected["answer"] and set(must_cite).issubset(set(cites))
        return {"scored": True, "correct": ok, "detail": "table_answer_and_cite"}

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
