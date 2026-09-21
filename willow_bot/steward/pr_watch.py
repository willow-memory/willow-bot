"""Who opened a PR, and where to tell them — the steward's read side of
``$WILLOW_HOME/willow-bot/pr_watch.json`` (sealed pair 11ccb0f7, part 1).

willow-mcp's ``pr_open_execute`` writes one row per PR it opens, keyed
``repo#pr``::

    {"willow-memory/ratatosk#48": {"app_id": "willow", "session_id": "…",
                                   "channel": "#willow", "opened_at": "…",
                                   "head": "feat/provider-ladder"}}

This module only reads. An absent, unreadable or malformed file reads as
an empty table — a PR with no row is simply not watched, and ``run_ci``
says so (``skipped: no watch row``) rather than guessing a channel.
"""
from __future__ import annotations

import json
from pathlib import Path

from willow_bot.paths import bot_dir

WATCH_FILE = "pr_watch.json"


def watch_path() -> Path:
    return bot_dir() / WATCH_FILE


def load(path: Path | None = None) -> dict[str, dict]:
    p = path or watch_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def watcher_for(repo: str, pr: object, table: dict[str, dict] | None = None) -> dict | None:
    """The watch row for ``repo#pr``, or None. A PR-less head (``pr`` falsy)
    has no watcher by construction — nothing opened it."""
    if not pr:
        return None
    table = load() if table is None else table
    row = table.get(f"{repo}#{pr}")
    if not isinstance(row, dict) or not row.get("channel"):
        return None
    return row
