"""Shared env for steward PR watch (deterministic gh/CI — never Grove 3B watcher)."""
from __future__ import annotations

import os
from pathlib import Path


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def willow_home() -> Path:
    default = Path.home() / "sean-data-vault" / "willow-operator-box"
    return Path(os.environ.get("WILLOW_HOME", default))


def webhook_inbox_dir() -> Path:
    return willow_home() / "upstream_steward" / "webhook_inbox"


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
    """Optional merge→pull/pip -e. Default on (legacy loki_pr_watch behaviour)."""
    if "WILLOW_BOT_STEWARD_HOST_SYNC" in os.environ:
        return _truthy("WILLOW_BOT_STEWARD_HOST_SYNC", "1")
    return True
