"""Shared env for steward PR watch (deterministic gh/CI — never Grove 3B watcher)."""
from __future__ import annotations

import os
from pathlib import Path

from willow_bot.paths import webhook_inbox_dir as _paths_webhook_inbox_dir
from willow_bot.paths import willow_home as _paths_willow_home


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def willow_home() -> Path:
    # Re-export from the shared paths module so an old caller
    # (`from willow_bot.steward.config import willow_home`) still resolves.
    return _paths_willow_home()


def webhook_inbox_dir() -> Path:
    return _paths_webhook_inbox_dir()


def state_path() -> Path:
    vault = os.environ.get("WILLOW_VAULT_BOX", str(willow_home()))
    return Path(
        os.environ.get(
            "WILLOW_BOT_STEWARD_STATE",
            os.environ.get("LOKI_PR_WATCH_STATE", f"{vault}/loki_pr_watch_state.json"),
        )
    )


def watcher_call_enabled() -> bool:
    """PR watch must not call Ollama/Cerebras. Opt-in is reserved and currently rejected."""
    return _truthy("WILLOW_BOT_STEWARD_CALL_WATCHER") or _truthy("LOKI_PR_WATCH_CALL_WATCHER")


def watcher_url() -> str:
    """Documented for willow-bot loki.watcher only; steward does not HTTP here."""
    return os.environ.get("LOKI_WATCHER_URL", "http://localhost:11434/v1").strip()


def host_sync_enabled() -> bool:
    """merge.py's host-side merge→``gh``/``git pull``/``pip -e`` sync.

    Explicit ``WILLOW_BOT_STEWARD_HOST_SYNC`` always wins. Otherwise: ON when
    MCP is off (the legacy loki_pr_watch behaviour, which is the only pull
    path a box without the broker has) and OFF when ``WILLOW_BOT_MCP`` is on,
    because then the tick's sweep asks willow-mcp's ``gitsync_sweep`` to
    bring merges home under the App's token with a FRANK receipt, and two
    pullers racing the same checkout is how a tree ends up half-way.
    """
    if "WILLOW_BOT_STEWARD_HOST_SYNC" in os.environ:
        return _truthy("WILLOW_BOT_STEWARD_HOST_SYNC", "1")
    return not _truthy("WILLOW_BOT_MCP")
