"""Shared filesystem paths for willow-bot modules.

Four modules used to re-implement `_willow_home()` with subtly different
behavior around a blank env value and `~` expansion. This module is the
single source of truth: every caller reads `WILLOW_HOME` the same way,
so a rename of the env or a change to the default lands in one place.

- `willow_home()` — root: the data-vault box. Reads `WILLOW_HOME`, then
  `WILLOW_VAULT_BOX` (stripped, `~` expanded), and it must be an existing
  directory. There is NO default: the box
  is wherever willow-data-vault's `bootstrap/provision.sh <box>` put it, and
  `WILLOW_HOME == WILLOW_STORE_ROOT == <box>` is that repo's contract. With
  neither set this raises `BoxNotConfigured` rather than guess — the old
  fallback (`~/sean-data-vault/willow-operator-box`, one operator's layout
  hardcoded) and `credentials.py`'s `~/{user}-data-vault/...` disagreed with
  each other, and anything run without the env silently wrote to a guess.
- `bot_dir()`, `deposits_dir()`, `webhook_inbox_dir()` — the three
  well-known subdirectories every steward step writes under; each is
  one line and could live at the call site, but centralizing them keeps
  a future move (e.g. `willow-bot/` → `bot/`) as a one-edit change.
"""
from __future__ import annotations

import os
from pathlib import Path


class BoxNotConfigured(RuntimeError):
    """Neither ``WILLOW_HOME`` nor ``WILLOW_VAULT_BOX`` names the box. A
    ``RuntimeError`` so callers that already treat an unconfigured App as
    "not configured" (``github_app._configured``) keep doing so."""


BOX_NOT_CONFIGURED = (
    "no data-vault box configured: set WILLOW_HOME (or WILLOW_VAULT_BOX) to the box "
    "willow-data-vault's bootstrap/provision.sh created. There is no default box."
)


BOX_ENV = ("WILLOW_HOME", "WILLOW_VAULT_BOX")


def env_box(*names: str) -> Path:
    """The box: the first of ``names`` (default ``BOX_ENV``) set to a
    non-blank value, ``~`` expanded — and it must be an existing directory.
    Otherwise ``BoxNotConfigured``. The one rule every box resolver in this
    repo uses. A named box that does not exist is refused too (Loki
    A726C6F8): the first write would otherwise create a whole bogus tree,
    and every read would report "empty" when the truth is "no box"."""
    for name in names or BOX_ENV:
        raw = os.environ.get(name, "").strip()
        if raw:
            box = Path(raw).expanduser()
            if not box.is_dir():
                raise BoxNotConfigured(
                    f"{name}={box} is not an existing directory; the box is created by "
                    "willow-data-vault's bootstrap/provision.sh, never by willow-bot."
                )
            return box
    raise BoxNotConfigured(BOX_NOT_CONFIGURED)


def willow_home() -> Path:
    """Root data directory for willow-bot state: the box (`$WILLOW_HOME`,
    else `$WILLOW_VAULT_BOX`). Raises `BoxNotConfigured` when neither is
    set — never a guessed path.

    A blank env is treated as unset (whitespace-only counts as blank);
    `~` in the value is expanded. Callers must not pass this Path to a
    write without first ensuring its parent tree exists — this function
    computes a path, it does not create one.
    """
    return env_box(*BOX_ENV)


def bot_dir() -> Path:
    """`$WILLOW_HOME/willow-bot/` — the bot's own state / journal root."""
    return willow_home() / "willow-bot"


def deposits_dir() -> Path:
    """`$WILLOW_HOME/willow-bot/deposits/` — where `ci_outcomes.jsonl`,
    `mirror.offset`, `ci.offset`, and `ci_outcomes.chain.tip` live."""
    return bot_dir() / "deposits"


def persona_dir() -> Path:
    """`$WILLOW_HOME/willow-bot/persona/` — the voice's SQLite counters
    (contributors, CI sigh streaks, rebase shame)."""
    return bot_dir() / "persona"


def webhook_inbox_dir() -> Path:
    """`$WILLOW_HOME/upstream_steward/webhook_inbox/` — fleet_bridge's
    inbox for the steward's tick to consume."""
    return willow_home() / "upstream_steward" / "webhook_inbox"
