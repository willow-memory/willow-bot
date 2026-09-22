"""Shared env for steward PR watch (deterministic gh/CI — never Grove 3B watcher)."""
from __future__ import annotations

import os
from pathlib import Path

from willow_bot.paths import webhook_inbox_dir as _paths_webhook_inbox_dir
from willow_bot.paths import willow_home as _paths_willow_home


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


#: The steward's own principal (sealed 163b9a70): app_id ``willow-bot``,
#: with its own signed manifest, never the human orchestrator seat. This is
#: the code-level default only — the LIVE deploy unit still needs its own
#: env (or a willow-bot manifest + the willow-mcp-side acceptance, packet
#: 4326FDFE) before this identity switch is safe to install; see
#: ``systemd/willow-bot-steward.service.template``'s header comment.
STEWARD_APP_ID = "willow-bot"

#: The env var every call site reads to resolve which app_id it speaks to
#: willow-mcp as. One name, read in exactly one place (``app_id()`` below)
#: — Loki 9778E096 F2 found this duplicated as a literal
#: ``os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"``
#: at seven call sites in tick.py alone, each one a place the default could
#: quietly drift from the sender identity used elsewhere.
APP_ID_ENV = "WILLOW_BOT_MCP_APP_ID"


def app_id() -> str:
    """The app_id the steward speaks to willow-mcp as. ``$WILLOW_BOT_MCP_APP_ID``
    if set, else ``STEWARD_APP_ID`` ("willow-bot") — NOT "willow": the
    steward is its own principal, never the human orchestrator seat
    (sealed 163b9a70). A box whose willow-bot manifest / willow-mcp
    acceptance has not landed yet must set ``WILLOW_BOT_MCP_APP_ID=willow``
    explicitly in its own env to keep working as before; that is the
    "gate" — nothing here silently reverts to the old default."""
    return os.environ.get(APP_ID_ENV, "").strip() or STEWARD_APP_ID


def manifest_fingerprint(for_app_id: str | None = None) -> str | None:
    """A content fingerprint (sha256 hex) of ``for_app_id``'s (default:
    ``app_id()``'s) own manifest under ``$WILLOW_MCP_APPS_ROOT`` (default:
    ``$WILLOW_HOME/mcp_apps``). ``None`` when the manifest is missing or
    unreadable — a caller using this to decide "did my own grant just
    change" falls back to a periodic probe cadence instead, never treats
    an unreadable manifest as "nothing changed".

    The steward CAN read its own manifest (it is a local file, not a
    willow-mcp call) — this is what lets a `blocked` ci_comments sub-state
    notice a grant landing the very next tick rather than waiting out
    ``ci_comments.BLOCKED_PROBE_TICKS``."""
    import hashlib

    app = for_app_id or app_id()
    root = os.environ.get("WILLOW_MCP_APPS_ROOT", str(willow_home() / "mcp_apps"))
    path = Path(root) / app / "manifest.json"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


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
