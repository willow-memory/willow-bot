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
        "comment": {"status": "pending"|"posted"|"green"|"stalled",
                    "comment_id": int|None, "attempts": int,
                    "last_error": str|None, "last_attempt_tick": int|None,
                    "paused_until": int|None},
        "spoke": {"status": "pending"|"posted"|"stalled",
                  "attempts": int, "last_error": str|None,
                  "last_attempt_tick": int|None, "paused_until": int|None},
        "pending_kind": "red"|"green"  # only meaningful while comment is pending
      },
      "_tick": int  # a monotonic counter for backoff; not a head entry
    }

``comment``/``spoke`` are independent — a head can have a posted comment
and a still-pending Grove line, or vice versa (an MCP outage blocks only
the Grove half). Past ``MAX_ATTEMPTS`` a sub-state reads ``stalled``, never
``abandoned``: Loki's re-audit (second pass, dispatch E026CFE7) found a
GitHub outage silenced the Grove line too (it was gated on the comment
having landed) and never resumed once GitHub came back, with no receipt
line saying so. ``stalled`` keeps being probed forever, once every
``STALL_PROBE_TICKS`` ticks, and any later success resumes it.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "ci_comments.json"
MAX_ATTEMPTS = 10
# Past MAX_ATTEMPTS a sub-state goes `stalled`, not abandoned: it keeps
# being probed forever, once every STALL_PROBE_TICKS ticks, and a success
# on any later tick resumes it (Loki's re-audit, dispatch E026CFE7:
# "never silently abandon" — GitHub coming back after a long outage used
# to get zero further attempts and no receipt line).
STALL_PROBE_TICKS = 12
MAX_ENTRY_AGE_DAYS = 14
_TICK_KEY = "_tick"


def path() -> Path:
    return bot_dir() / FILE_NAME


def load() -> tuple[dict[str, Any], str | None]:
    """``(state, corrupt_reason)``. ``corrupt_reason`` is ``None`` on a
    clean load — including a missing file, which is not corruption — and
    a short string otherwise. Two different shapes of trouble, told apart
    by prefix:

    * ``"invalid json: ..."`` / ``"not a json object"`` — the file is
      there and readable but not a good table; the caller
      (``run_ci``) calls ``quarantine_corrupt()`` to rename it aside and
      starts a fresh table, with a receipt line saying so.
    * ``"unreachable: ..."`` — the file could not be READ at all (EACCES,
      EIO, ENOTDIR, ...). Loki's re-audit: the prior version caught every
      ``OSError`` here, including these, and returned ``({}, None)`` —
      indistinguishable from a missing file — so the receipt read
      ``ci_comments_table: ok`` and the next ``save()`` happily
      ``os.replace``'d a fresh empty table over one it never actually
      read (keys before: [head], keys after: [], no ``.corrupt`` file:
      the exact silent-``{}`` this module's docstring says it removed).
      Only a genuinely MISSING file (``FileNotFoundError``) is "nothing to
      forget"; every other read failure must stop the caller from ever
      calling ``save()`` this tick, not just relabel the receipt.

    Every entry missing ``_created_at`` (any row written before that field
    existed) is given one here, in memory — so it starts aging under
    ``prune_old`` instead of living forever ungoverned; the caller's
    ``save()`` (when it runs) is what makes it durable."""
    p = path()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"unreachable: {exc}"[:200]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"invalid json: {exc}"[:200]
    if not isinstance(data, dict):
        return {}, "not a json object"
    for key, entry in data.items():
        if key != _TICK_KEY and isinstance(entry, dict) and "_created_at" not in entry:
            entry["_created_at"] = time.time()
    return data, None


def quarantine_corrupt() -> str | None:
    """Rename the unreadable table aside as
    ``ci_comments.json.corrupt-<timestamp>`` so the evidence survives and
    the next ``save()`` starts clean. Returns the new path, or ``None``
    when there was nothing on disk to move."""
    p = path()
    if not p.exists():
        return None
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = p.with_name(f"{p.name}.corrupt-{ts}")
    try:
        os.replace(p, dest)
    except OSError:
        return None
    return str(dest)


def save(state: dict[str, Any]) -> None:
    """Atomic write: same-directory temp file, then ``os.replace`` — a
    reader never sees a half-written file, and a crash mid-write leaves
    the previous good version in place. A crash BETWEEN ``mkstemp`` and
    ``os.replace`` on an earlier run leaves a ``.ci_comments.<rand>.tmp``
    sibling that nothing else cleans up (Loki's re-audit); swept here,
    before writing a new one, rather than left to accumulate for the life
    of the install."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    for stray in p.parent.glob(".ci_comments.*.tmp"):
        try:
            stray.unlink()
        except OSError:
            pass
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
             "comment": _sub(), "spoke": _sub(), "_created_at": time.time()}
        state[head_key] = e
    return e


def _interval_s() -> float:
    """The steward loop's own tick interval, for converting a GitHub
    ``Retry-After`` (seconds) into a tick count. Same env vars ``tick.py``
    reads for ``loop``; a caller with neither set gets the same 300 s
    default."""
    raw = os.environ.get("WILLOW_BOT_STEWARD_INTERVAL", os.environ.get("LOKI_PR_WATCH_INTERVAL", "300"))
    try:
        return float(raw) or 300.0
    except (TypeError, ValueError):
        return 300.0


def due(sub: dict[str, Any], *, tick: int) -> bool:
    """A ``pending`` or ``stalled`` sub-state is due for another attempt.

    ``pending``: never tried, or enough ticks have passed since the last
    attempt — backoff grows with the attempt count (capped at 8) so a
    persistently broken head does not hammer GitHub every tick. Past
    ``MAX_ATTEMPTS`` the sub-state moves to ``stalled`` (``record_failure``)
    rather than being abandoned, and stays due forever after that, once
    every ``STALL_PROBE_TICKS`` — a human reading the file sees exactly how
    many tries were made and why the last one failed, and a later success
    (GitHub back up) resumes it via ``record_success``.

    A ``paused_until`` tick (set by ``record_rate_limited`` to honour a
    GitHub ``Retry-After``) blocks any attempt before that tick regardless
    of backoff — a rate limit is a scheduled wait, not a retry to race."""
    status = sub.get("status")
    if status not in ("pending", "stalled"):
        return False
    paused_until = sub.get("paused_until")
    if paused_until is not None and tick < int(paused_until):
        return False
    last = sub.get("last_attempt_tick")
    if last is None:
        return True
    if status == "stalled":
        return (tick - int(last)) >= STALL_PROBE_TICKS
    attempts = int(sub.get("attempts") or 0)
    backoff = min(attempts, 8)
    return (tick - int(last)) >= backoff


def record_success(sub: dict[str, Any], *, status: str, tick: int) -> None:
    sub["status"] = status
    sub["attempts"] = 0
    sub["last_error"] = None
    sub["last_attempt_tick"] = tick
    sub.pop("paused_until", None)


def record_failure(sub: dict[str, Any], *, error: str, tick: int) -> None:
    """A real failure (502, timeout, missing permission, ...) — counts
    toward ``MAX_ATTEMPTS``. Never a rate limit; use
    ``record_rate_limited`` for that, which does not burn an attempt."""
    sub["attempts"] = int(sub.get("attempts") or 0) + 1
    sub["last_error"] = error[:400]
    sub["last_attempt_tick"] = tick
    if sub["attempts"] >= MAX_ATTEMPTS:
        sub["status"] = "stalled"


def record_rate_limited(sub: dict[str, Any], *, retry_after: object, tick: int) -> None:
    """A 403 rate limit is a pause, not a failure: ``attempts`` is left
    untouched (Loki's re-audit finding 2 — a rate-limited 403 used to burn
    an attempt exactly like a 502 and could abandon a head 10 limiter
    responses in). ``retry_after`` (GitHub's header, seconds, or ``None``)
    is converted to a tick count and stored as ``paused_until``; ``due()``
    refuses any attempt before that tick."""
    sub["last_attempt_tick"] = tick
    sub["last_error"] = "rate limited"
    try:
        secs = float(retry_after)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        secs = _interval_s()
    ticks = max(1, math.ceil(secs / _interval_s()))
    sub["paused_until"] = tick + ticks


def strip_blocks(entry: dict[str, Any]) -> None:
    """Drop the (up to 120-line) failure block from every leg once the
    comment carrying it has actually landed — the table only needs the
    leg identity (check/conclusion/url) to detect a genuinely new leg
    later; the block itself is always rebuildable from the log on
    demand. Keeps the owed table from growing 5-15 KB per red head for
    the life of the install (Loki's re-audit, owed-table finding)."""
    for info in (entry.get("legs") or {}).values():
        if isinstance(info, dict):
            info.pop("block", None)


def retire_check(owed: dict[str, Any], *, closed: dict[str, Any], tick: int) -> list[dict[str, Any]]:
    """Remove entries that are done: the PR has closed or merged (``where``
    — ``repo#pr`` — is a key in ``closed``), or the comment has read
    ``green`` for at least one full tick (giving the same tick's Grove
    line, if any, a chance to be read from the entry before it vanishes).
    Returns ``[{key, reason}]`` for every head retired this call —
    ``reason`` is ``"pr merged"``, ``"pr closed"``, or ``"green"``, so a
    human reading the receipt does not have to guess which of the two
    honest ways this entry finished."""
    retired: list[dict[str, Any]] = []
    for head_key in head_keys(owed):
        entry = owed[head_key]
        where = entry.get("where")
        cs = entry.get("comment") or {}
        reason = None
        if bool(where) and where in closed:
            reason = "pr merged" if closed[where].get("merged") else "pr closed"
        elif cs.get("status") == "green":
            retire_at = entry.get("_retire_at_tick")
            if retire_at is None:
                entry["_retire_at_tick"] = tick + 1
            elif tick >= retire_at:
                reason = "green"
        if reason:
            del owed[head_key]
            retired.append({"key": head_key, "reason": reason})
    return retired


def prune_old(owed: dict[str, Any], *, now_epoch: float, max_age_days: float = MAX_ENTRY_AGE_DAYS) -> list[dict[str, Any]]:
    """Drop entries older than ``max_age_days`` whose comment has already
    gone ``green`` — a defensive backstop for a green entry
    ``retire_check`` somehow missed, never the primary path for removing
    one. A still ``pending``/``posted``/``stalled`` comment — the
    still-active, still-being-probed shape — survives this regardless of
    age and keeps being probed every tick.

    Loki's re-audit: the prior version dropped ANY entry past
    ``max_age_days`` regardless of status, with a receipt line
    (``retired: [key]``) indistinguishable from a genuine green/closed
    retirement — so a month-long GitHub outage silently lost its comment
    at day 14, and GitHub coming back afterward posted nothing (the
    head's legs were already in ``ci_filed``, so nothing ever regrouped
    them). This is now only a backstop for the one status
    (``green``) that should already be gone via ``retire_check``, never a
    way to forget something still broken. Returns ``[{key, reason}]``."""
    cutoff = now_epoch - max_age_days * 86400
    pruned: list[dict[str, Any]] = []
    for head_key in head_keys(owed):
        entry = owed[head_key]
        created = entry.get("_created_at")
        if not isinstance(created, (int, float)) or created >= cutoff:
            continue
        cs_status = (entry.get("comment") or {}).get("status")
        if cs_status != "green":
            continue  # pending/posted/stalled — still active; never pruned by age alone
        del owed[head_key]
        pruned.append({"key": head_key,
                       "reason": f"aged out at {max_age_days:g}d past green (comment=green)"})
    return pruned


def stalled_report(owed: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{key, channel, attempts, last_error}]`` for every sub-state
    currently ``stalled`` — carried on the tick receipt every tick it
    stays that way, so "GitHub down for a night" is never a silent gap in
    the record."""
    out: list[dict[str, Any]] = []
    for head_key in head_keys(owed):
        entry = owed[head_key]
        where = entry.get("where", head_key)
        for channel in ("comment", "spoke"):
            sub = entry.get(channel)
            if isinstance(sub, dict) and sub.get("status") == "stalled":
                out.append({"key": where, "channel": channel,
                            "attempts": sub.get("attempts"), "last_error": sub.get("last_error")})
    return out
