"""Durable, retried state for the steward's CI-red PR comment and Grove
line — owed per ``(repo, pr, head_sha)`` until it lands.

Loki's audit of the first cut (dispatch E026CFE7, 2026-09-21) found the
comment and the Grove line fired ONCE, from inside the filing loop, after
the legs were already written to the filing step's own ``ci_filed``
marker — so a 502, a refused broker, or the broker being off made the
silence permanent for that head. This module is the fix: a small,
separately and atomically written file
(``$WILLOW_HOME/willow-bot/ci_comments.json``) that owes each head a
comment and a Grove line until one of them actually lands, independent of
whatever ``run_ci``'s own bigger state file does with ``ci_filed`` /
``ci_items`` / the ``human_required_enqueue`` result.

One entry per head key (``repo#pr@head_sha`` — the same shape ``tick``
already uses)::

    {
      "<head_key>": {
        "repo": "...", "pr": 48, "head_sha": "...", "where": "repo#48",
        "legs": {"<check name>": {leg info, incl. block/trimmed/source/
                                   missing_permission/detail/log_truncated}},
        "comment": {"status": "pending"|"posted"|"green"|"abandoned",
                    "comment_id": int|None, "attempts": int,
                    "last_error": str|None, "last_attempt_tick": int|None},
        "spoke": {"status": "pending"|"posted"|"abandoned",
                  "attempts": int, "last_error": str|None,
                  "last_attempt_tick": int|None},
        "pending_kind": "red"|"green"  # only meaningful while comment is pending
      },
      "_tick": int  # a monotonic counter for backoff; not a head entry
    }

``comment``/``spoke`` are independent — a head can have a posted comment
and a still-pending Grove line, or vice versa (an MCP outage blocks only
the Grove half).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "ci_comments.json"
MAX_ATTEMPTS = 10
_TICK_KEY = "_tick"


def path() -> Path:
    return bot_dir() / FILE_NAME


def load() -> dict[str, Any]:
    p = path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(state: dict[str, Any]) -> None:
    """Atomic write: same-directory temp file, then ``os.replace`` — a
    reader never sees a half-written file, and a crash mid-write leaves
    the previous good version in place."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=".ci_comments.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, p)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def head_keys(state: dict[str, Any]) -> list[str]:
    """Every real head-entry key in `state`, sorted — excludes the
    internal tick counter so a caller can iterate entries without
    reaching into this module's private storage shape."""
    return sorted(k for k, v in state.items() if k != _TICK_KEY and isinstance(v, dict))


def next_tick(state: dict[str, Any]) -> int:
    """Bump and return the state's own tick counter — one call per
    ``run_ci`` invocation, used only for backoff spacing."""
    n = int(state.get(_TICK_KEY) or 0) + 1
    state[_TICK_KEY] = n
    return n


def _sub(status: str = "pending") -> dict[str, Any]:
    return {"status": status, "attempts": 0, "last_error": None, "last_attempt_tick": None}


def entry_for(state: dict[str, Any], head_key: str, *, repo: str, pr: object,
              head_sha: str, where: str) -> dict[str, Any]:
    """The head's own entry, created on first sight. Never overwrites an
    existing entry's progress."""
    e = state.get(head_key)
    if not isinstance(e, dict):
        e = {"repo": repo, "pr": pr, "head_sha": head_sha, "where": where, "legs": {},
             "comment": _sub(), "spoke": _sub()}
        state[head_key] = e
    return e


def due(sub: dict[str, Any], *, tick: int) -> bool:
    """A pending sub-state (``comment`` or ``spoke``) is due for another
    attempt when it has never been tried, or enough ticks have passed
    since the last attempt — backoff grows with the attempt count so a
    persistently broken head does not hammer GitHub every tick, and stops
    altogether past ``MAX_ATTEMPTS`` (the entry stays ``pending`` in the
    file, so a human reading it sees exactly how many tries were made and
    why the last one failed, but the step quits paying for it)."""
    if sub.get("status") != "pending":
        return False
    attempts = int(sub.get("attempts") or 0)
    if attempts >= MAX_ATTEMPTS:
        return False
    last = sub.get("last_attempt_tick")
    if last is None:
        return True
    backoff = min(attempts, 8)
    return (tick - int(last)) >= backoff


def record_success(sub: dict[str, Any], *, status: str, tick: int) -> None:
    sub["status"] = status
    sub["attempts"] = 0
    sub["last_error"] = None
    sub["last_attempt_tick"] = tick


def record_failure(sub: dict[str, Any], *, error: str, tick: int) -> None:
    sub["attempts"] = int(sub.get("attempts") or 0) + 1
    sub["last_error"] = error[:400]
    sub["last_attempt_tick"] = tick
    if sub["attempts"] >= MAX_ATTEMPTS:
        sub["status"] = "abandoned"
