"""Code-first resolver tier (D0) for the flowering pool.

stdlib only. No network, no Ollama. Each resolver reads a pool record
(the fixture's excerpts) and returns an answer or ``ESCALATE``; only an
escalated act would ever reach a model. The number that matters is
resolver *coverage* (acts closed by code) and resolver *precision* (a
resolved answer that is wrong is a defect, not a score).
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

TERMINAL_PUNCTUATION = (".", "?", "!")

# 12-hex before 8-hex: alternation is first-match, and a 12-hex id is a
# superset shape of an 8-hex one, so the longer alternative must lead or
# an 8-hex match would win inside a 12-hex string.
_HEX_ID_RE = re.compile(r"\b[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{8}\b")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_excerpt_fields(text: str) -> dict[str, str]:
    """Parse a ``;``-separated excerpt into a dict of fields.

    Each segment is ``key value`` or ``key: value``. The key is the text
    before the earlier of a colon or a space in that segment; whichever
    comes first decides the split. A segment with neither (no separator
    at all, or an empty key/value after stripping) is ignored -- never
    guessed.
    """
    fields: dict[str, str] = {}
    for raw_segment in (text or "").split(";"):
        segment = raw_segment.strip()
        if not segment:
            continue
        colon = segment.find(":")
        space = segment.find(" ")
        if colon != -1 and (space == -1 or colon < space):
            key, _, value = segment.partition(":")
        elif space != -1:
            key, _, value = segment.partition(" ")
        else:
            continue
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        if key not in fields:
            fields[key] = value
    return fields


def _parse_excerpts(excerpts: list) -> tuple[dict[str, str], dict[str, str]]:
    """Merge fields across all excerpts. Returns (fields, field->excerpt_id).

    First excerpt to offer a given key wins -- never overwritten by a
    later, possibly stale, excerpt.
    """
    fields: dict[str, str] = {}
    source: dict[str, str] = {}
    for ex in excerpts or []:
        if not isinstance(ex, dict):
            continue
        eid = ex.get("id", "")
        parsed = parse_excerpt_fields(ex.get("text", ""))
        for key, value in parsed.items():
            if key not in fields:
                fields[key] = value
                source[key] = eid
    return fields, source


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


def _resolve_g1(fixture_id: str, fields: dict, source: dict) -> dict:
    to_app = fields.get("to_app")
    if not to_app:
        return _record(fixture_id, "G1", STATUS_ESCALATE, None, [], "no_to_app_field")
    cite = [source["to_app"]] if "to_app" in source else []
    return _record(fixture_id, "G1", STATUS_RESOLVED, to_app, cite, None)


def _resolve_g2(fixture_id: str, fields: dict, source: dict, expected: dict) -> dict:
    title = fields.get("title")
    if title is None:
        return _record(fixture_id, "G2", STATUS_ESCALATE, None, [], "no_title_field")
    max_chars = int(expected.get("max_chars", 120)) if expected else 120
    if len(title) > max_chars:
        return _record(fixture_id, "G2", STATUS_ESCALATE, None, [], "title_over_limit")
    cite = [source["title"]] if "title" in source else []
    return _record(fixture_id, "G2", STATUS_RESOLVED, title, cite, None)


def _resolve_g3(fixture_id: str, fields: dict) -> dict:
    gid = fields.get("id")
    question = fields.get("question")
    if not gid or not question:
        return _record(
            fixture_id, "G3", STATUS_ESCALATE, None, [], "missing_id_or_question"
        )
    if not question.rstrip().endswith(TERMINAL_PUNCTUATION):
        return _record(fixture_id, "G3", STATUS_ESCALATE, None, [], "source_truncated")
    answer = f"{gid}: {question}"
    return _record(fixture_id, "G3", STATUS_RESOLVED, answer, [gid], None)


def _resolve_g4(fixture_id: str, fields: dict) -> dict:
    answer: dict[str, str] = {}
    if "to_app" in fields:
        answer["builder_seat"] = fields["to_app"]
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
    """Resolve one fixture dict. Never raises on a malformed field -- an
    unparseable or missing field escalates, it is never guessed."""
    fixture_id = fixture.get("id", "")
    cls = fixture.get("class", "")
    excerpts = fixture.get("excerpts") or []
    expected = fixture.get("expected") or {}
    fields, source = _parse_excerpts(excerpts)

    if cls == "G1":
        return _resolve_g1(fixture_id, fields, source)
    if cls == "G2":
        return _resolve_g2(fixture_id, fields, source, expected)
    if cls == "G3":
        return _resolve_g3(fixture_id, fields)
    if cls == "G4":
        return _resolve_g4(fixture_id, fields)
    if cls == "G5":
        return _resolve_g5(fixture_id, excerpts)
    return _record(fixture_id, cls, STATUS_ESCALATE, None, [], "unknown_class")


def _allowed_ids(excerpts: list) -> set[str]:
    """8-hex/12-hex ids that legitimately appear in this fixture's excerpts
    (both the excerpt's own id and any id-shaped token inside its text)."""
    allowed: set[str] = set()
    for ex in excerpts or []:
        if not isinstance(ex, dict):
            continue
        eid = ex.get("id", "")
        if eid:
            allowed.add(eid)
            if eid.startswith("ex-"):
                allowed.add(eid[3:])
        allowed |= set(_HEX_ID_RE.findall(ex.get("text", "")))
    return allowed


def score_resolution(fixture: dict, resolution: dict) -> dict:
    """Deterministic scorer against the fixture's ``expected`` block.

    An ``escalate`` row is never scored -- escalating is never a failure.
    Everything else (resolved, verbatim, flowering_required) is checked
    against ``expected``.
    """
    status = resolution.get("status")
    if status == STATUS_ESCALATE:
        return {"scored": False, "correct": None, "detail": "escalate_not_scored"}

    expected = fixture.get("expected") or {}
    cls = resolution.get("class")
    cites = resolution.get("cites") or []
    answer = resolution.get("answer")

    if cls == "G1":
        must_cite = expected.get("must_cite") or []
        ok = answer == expected.get("to_app") and set(must_cite).issubset(set(cites))
        return {"scored": True, "correct": ok, "detail": "g1_to_app_and_cite"}

    if cls == "G2":
        max_chars = int(expected.get("max_chars", 120))
        must_name = expected.get("must_name") or []
        text = answer or ""
        ok = len(text) <= max_chars and all(name in text for name in must_name)
        return {"scored": True, "correct": ok, "detail": "g2_length_and_names"}

    if cls == "G3":
        must_cite = expected.get("must_cite") or []
        ok_cite = set(must_cite).issubset(set(cites))
        text = answer or ""
        allowed = _allowed_ids(fixture.get("excerpts") or [])
        found = set(_HEX_ID_RE.findall(text))
        stray = found - allowed
        ok = ok_cite and not stray
        return {"scored": True, "correct": ok, "detail": "g3_cite_and_no_stray_ids"}

    if cls == "G4":
        ok = isinstance(answer, dict) and answer.get("builder_seat") == expected.get(
            "builder_seat"
        )
        return {"scored": True, "correct": ok, "detail": "g4_builder_seat"}

    if cls == "G5":
        ok = bool(cites)
        return {"scored": True, "correct": ok, "detail": "g5_cite_present"}

    return {"scored": False, "correct": None, "detail": "unknown_class"}


def _fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_fixture_files(fixtures_dir: Path) -> list[Path]:
    return sorted(Path(fixtures_dir).glob("S-growth-*.json"))


def resolve_fixtures_dir(fixtures_dir: Path) -> list[dict]:
    """Resolve + score every ``S-growth-*.json`` fixture under a dir.

    Returns one row per fixture: the resolution record plus ``scored``,
    ``correct``, ``score_detail``, and ``fixture_sha256``.
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
        resolution = resolve_fixture(fixture)
        score = score_resolution(fixture, resolution)
        row = dict(resolution)
        row["scored"] = score["scored"]
        row["correct"] = score["correct"]
        row["score_detail"] = score["detail"]
        row["fixture_sha256"] = _fixture_sha256(fp)
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    by_status: dict[str, int] = {}
    resolved_total = 0
    resolved_correct = 0
    for row in rows:
        status = row.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        if row.get("scored"):
            resolved_total += 1
            if row.get("correct"):
                resolved_correct += 1
    return {
        "n": n,
        "by_status": by_status,
        "resolved_total": resolved_total,
        "resolved_correct": resolved_correct,
    }


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
