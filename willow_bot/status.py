"""Read-only status surface for the seat.

Gap ``158600e03598``. The Willow seat cannot tell populated / empty /
unreachable apart for the bot: an unreadable journal reads as "unit
absent", a missing checkout reads as "no bot installed", an inbox
directory that has never been created reads the same as one whose
contents cannot be listed. This module reads what it can from the local
filesystem and returns a structured receipt where every source names
its own state.

One operation: ``report()`` returns a dict of the form:

    {
      "at": "<ISO8601 UTC>",
      "version": {"status": "populated"|"empty"|"unreachable", ...},
      "running_commit": {"status": ..., "sha": <str|None>, "detail": ...},
      "heartbeat": {"status": ..., "last": <receipt>, "at": <ISO8601>},
      "tick": {"status": ..., "last": <receipt>, "at": <ISO8601>},
      "journal": {"status": ..., "lines": [...], "count": N},
      "inbox": {"status": ..., "depth_by_kind": {"pull_request": N, ...}},
      "cursors": {"mirror_offset": N|None, "ci_offset": N|None, "chain_tip": <sha|None>},
      "sync": {"status": ..., "last_success": <receipt>, "at": <ISO8601>},
    }

Three states per source, chosen deliberately:

- ``populated``: the source exists and has data
- ``empty``: the source exists (or its container exists) but has nothing
  to report — a fresh install with no ticks yet, an inbox never written to
- ``unreachable``: the source could not be read (permission denied, an
  IO error, a broken JSON that would need repair) — the seat's read of
  this field cannot rely on it, but the ABSENCE of a value is not itself
  a sign the unit is missing

The distinction matters: a seat that maps every failure to "empty" or
every miss to "unreachable" cannot answer "is the bot running?" honestly.
The seat's own dashboard needs the three states to compose a truthful
badge (grey/green/red) for each field.

Never writes; never opens a network socket; never spawns a subprocess
outside a bounded ``git rev-parse HEAD`` (with a short timeout, and
whose failure is a receipt line, not a raise).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot.paths import (
    bot_dir as _bot_dir,
    deposits_dir as _deposits_dir,
    webhook_inbox_dir as _inbox_dir,
    willow_home as _willow_home,
)


_JOURNAL_TAIL = 5  # last N receipts included in the journal excerpt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── version ──────────────────────────────────────────────────────────────


def _read_version() -> dict[str, Any]:
    try:
        from importlib.metadata import version as _pkg_version

        v = _pkg_version("willow-bot")
        return {"status": "populated", "version": v}
    except Exception as exc:  # noqa: BLE001 — not installed, or metadata missing
        return {"status": "unreachable", "detail": f"importlib.metadata: {exc}"[:200]}


# ── running commit ────────────────────────────────────────────────────────


def _read_running_commit(cwd: Path | None = None) -> dict[str, Any]:
    """The commit the installed package was built from. `git rev-parse
    HEAD` in the bot's own checkout — a bot installed from PyPI has no
    git tree here and reports ``unreachable``, which is the right read
    (the version came from PyPI, not this box)."""
    root = cwd or Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        return {"status": "unreachable", "detail": f"no .git under {root}", "sha": None}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"status": "unreachable", "detail": str(exc)[:200], "sha": None}
    if proc.returncode != 0:
        return {"status": "unreachable",
                "detail": (proc.stderr or proc.stdout).strip()[:200], "sha": None}
    sha = proc.stdout.strip()
    return {"status": "populated" if sha else "empty", "sha": sha or None}


# ── receipts (heartbeat + tick) ───────────────────────────────────────────


def _read_last_receipt(path: Path) -> dict[str, Any]:
    """Return the last row of a JSONL receipt file with its state:

    - file missing → ``empty`` (unit has never run; not the same as broken)
    - file present but no readable JSON row → ``unreachable``
    - file present with rows → ``populated`` + the last row
    """
    if not path.is_file():
        return {"status": "empty", "detail": f"no file at {path}", "last": None, "at": None}
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read()
    except OSError as exc:
        return {"status": "unreachable", "detail": str(exc)[:200], "last": None, "at": None}
    last: dict[str, Any] | None = None
    for raw in reversed(tail.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            last = rec
            break
    if last is None:
        return {"status": "unreachable", "detail": "no valid JSON row in tail",
                "last": None, "at": None}
    return {"status": "populated", "last": last, "at": last.get("at")}


def _tick_receipts_path() -> Path:
    return _bot_dir() / "steward_ticks.jsonl"


def _heartbeat_receipts_path() -> Path:
    return _bot_dir() / "steward_heartbeat.jsonl"


# ── journal excerpt ───────────────────────────────────────────────────────


def _read_journal_excerpt(path: Path, n: int = _JOURNAL_TAIL) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "empty", "count": 0, "lines": []}
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return {"status": "unreachable", "count": 0, "lines": [], "detail": str(exc)[:200]}
    parsed: list[dict[str, Any]] = []
    for raw in lines[-n:]:
        s = raw.strip()
        if not s:
            continue
        try:
            parsed.append(json.loads(s))
        except json.JSONDecodeError:
            parsed.append({"event": "unparseable", "raw": s[:200]})
    return {"status": "populated" if parsed else "empty",
            "count": len([l for l in lines if l.strip()]),
            "lines": parsed}


# ── inbox depth by kind ───────────────────────────────────────────────────


def _read_inbox_depth() -> dict[str, Any]:
    """Count the pending inbox rows grouped by ``kind``. Cheap: only the
    file's JSON key is parsed, not the whole payload (a webhook body can
    be a few KB and this box has 500+ deposits already)."""
    inbox = _inbox_dir()
    if not inbox.is_dir():
        return {"status": "empty", "total": 0, "depth_by_kind": {}}
    try:
        entries = list(inbox.glob("*.json"))
    except OSError as exc:
        return {"status": "unreachable", "total": 0, "depth_by_kind": {},
                "detail": str(exc)[:200]}
    by_kind: dict[str, int] = {}
    unreadable = 0
    for entry in entries:
        try:
            with entry.open("r", encoding="utf-8") as fh:
                item = json.load(fh)
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(item, dict):
            unreadable += 1
            continue
        kind = str(item.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    result: dict[str, Any] = {
        "status": "populated" if entries else "empty",
        "total": len(entries),
        "depth_by_kind": dict(sorted(by_kind.items())),
    }
    if unreadable:
        result["unreadable"] = unreadable
    return result


# ── cursors ──────────────────────────────────────────────────────────────


def _read_int_file(path: Path) -> int | None:
    """Read an offset file that contains a single integer. Absent → None;
    present-but-garbled → None (an operator can inspect the file itself).
    We do not raise on parse failure — status is not the place to lecture."""
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return None


def _read_str_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        v = path.read_text(encoding="utf-8").strip()
        return v or None
    except OSError:
        return None


def _read_cursors() -> dict[str, Any]:
    mirror = _read_int_file(_deposits_dir() / "mirror.offset")
    ci = _read_int_file(_deposits_dir() / "ci.offset")
    tip = _read_str_file(_deposits_dir() / "ci_outcomes.chain.tip")
    if mirror is None and ci is None and tip is None:
        return {"status": "empty", "mirror_offset": None, "ci_offset": None, "chain_tip": None,
                "annulled": 0}
    return {"status": "populated", "mirror_offset": mirror, "ci_offset": ci, "chain_tip": tip,
            "annulled": _count_annulled()}


def _count_annulled() -> int:
    """Rows voided by annul rows in the deposits file (gap 9) — the count a
    reader of the status needs beside the tip. Needle scan; only annul
    lines are parsed, so a long file costs one pass of bytes, not JSON."""
    from willow_bot.deposits import void_set_before

    path = _deposits_dir() / "ci_outcomes.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    voids = void_set_before(path, size)
    return len(voids.hashes) + len(voids.legacy_ids)


# ── sync (last successful sweep) ─────────────────────────────────────────


def _read_last_successful_sync() -> dict[str, Any]:
    """The most recent ``steward_sweep`` receipt with ``status=ok``. Read
    from the tail of the tick log so a busy log does not force reading
    the whole file."""
    path = _tick_receipts_path()
    if not path.is_file():
        return {"status": "empty", "last_success": None, "at": None}
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read()
    except OSError as exc:
        return {"status": "unreachable", "detail": str(exc)[:200],
                "last_success": None, "at": None}
    for raw in reversed(tail.splitlines()):
        s = raw.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("event") == "steward_sweep" and rec.get("status") == "ok":
            return {"status": "populated", "last_success": rec, "at": rec.get("at")}
    return {"status": "empty", "last_success": None, "at": None,
            "detail": "no successful sweep in the tail read"}


# ── the surface ──────────────────────────────────────────────────────────


def report() -> dict[str, Any]:
    """Assemble one status surface. Every field is a three-state read; no
    field's failure hides another field's success."""
    return {
        "at": _now(),
        "willow_home": str(_willow_home()),
        "version": _read_version(),
        "running_commit": _read_running_commit(),
        "heartbeat": _read_last_receipt(_heartbeat_receipts_path()),
        "tick": _read_last_receipt(_tick_receipts_path()),
        "journal": _read_journal_excerpt(_tick_receipts_path()),
        "inbox": _read_inbox_depth(),
        "cursors": _read_cursors(),
        "sync": _read_last_successful_sync(),
    }
