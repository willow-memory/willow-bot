"""Shared filesystem paths for willow-bot modules.

Four modules used to re-implement `_willow_home()` with subtly different
behavior around a blank env value and `~` expansion. This module is the
single source of truth: every caller reads `WILLOW_HOME` the same way,
so a rename of the env or a change to the default lands in one place.

- `willow_home()` — root; reads `WILLOW_HOME`, strips, `expanduser()`;
  falls back to `~/sean-data-vault/willow-operator-box` when unset or
  blank. This matches the older `willow_bot.status` / `delivery_dedup`
  shape, which was the more careful of the four; the two callers that
  used a looser shape (`deposits`, `steward.config`) inherit the safer
  behavior on adoption.
- `bot_dir()`, `deposits_dir()`, `webhook_inbox_dir()` — the three
  well-known subdirectories every steward step writes under; each is
  one line and could live at the call site, but centralizing them keeps
  a future move (e.g. `willow-bot/` → `bot/`) as a one-edit change.
"""
from __future__ import annotations

import os
from pathlib import Path


def willow_home() -> Path:
    """Root data directory for willow-bot state (`$WILLOW_HOME`).

    A blank env is treated as unset (whitespace-only counts as blank);
    `~` in the value is expanded. Callers must not pass this Path to a
    write without first ensuring its parent tree exists — this function
    computes a path, it does not create one.
    """
    raw = os.environ.get("WILLOW_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "sean-data-vault" / "willow-operator-box"


def bot_dir() -> Path:
    """`$WILLOW_HOME/willow-bot/` — the bot's own state / journal root."""
    return willow_home() / "willow-bot"


def deposits_dir() -> Path:
    """`$WILLOW_HOME/willow-bot/deposits/` — where `ci_outcomes.jsonl`,
    `mirror.offset`, `ci.offset`, and `ci_outcomes.chain.tip` live."""
    return bot_dir() / "deposits"


def webhook_inbox_dir() -> Path:
    """`$WILLOW_HOME/upstream_steward/webhook_inbox/` — fleet_bridge's
    inbox for the steward's tick to consume."""
    return willow_home() / "upstream_steward" / "webhook_inbox"
