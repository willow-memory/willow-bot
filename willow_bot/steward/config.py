"""Shared env for steward PR watch (deterministic gh/CI — never Grove 3B watcher)."""
from __future__ import annotations

import os
from pathlib import Path

from willow_bot.paths import webhook_inbox_dir as _paths_webhook_inbox_dir
from willow_bot.paths import willow_home as _paths_willow_home


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


#: The steward's own principal (sealed 163b9a70): app_id ``willow-bot``,
#: with its own signed manifest, never the human orchestrator seat, and the
#: constant the Grove ``sender`` resolves to whenever ``app_id()`` IS
#: ``willow-bot`` (``tick._grove_sender``). NOT the default ``app_id()``
#: returns today — see ``app_id()``'s own docstring (Loki 738DB24E F1: the
#: code default moving ahead of the live unit's env is exactly the "unit
#: goes dark on next restart" defect this rework closes). Reached only by
#: an explicit ``WILLOW_BOT_MCP_APP_ID=willow-bot`` (the deploy unit
#: template sets this once ``mcp_apps/willow-bot/manifest.json`` exists and
#: willow-mcp accepts it — packet 4326FDFE).
STEWARD_APP_ID = "willow-bot"

#: The default ``app_id()`` returns with no override — what main had
#: BEFORE this principal work started, and what every already-installed
#: unit still resolves to today (no ``WILLOW_BOT_MCP_APP_ID`` in the
#: currently-installed unit; ``WILLOW_HUMAN_ORCHESTRATOR=1`` is set
#: precisely because the steward speaks AS this seat). Flipping this
#: default to ``STEWARD_APP_ID`` is a later one-line change, made only
#: once ``mcp_apps/willow-bot/manifest.json`` is live — doing it here,
#: ahead of that, was Loki 738DB24E F1: a merge + routine pull + the next
#: unit restart would have entered the steward as ``willow-bot``, which
#: has no manifest on the box, and every orchestrator-scoped verb plus
#: every Grove send goes ``gate denied`` — the steward goes dark until a
#: human notices and fixes the env by hand. The gate belongs on the unit
#: (it sets ``WILLOW_BOT_MCP_APP_ID=willow-bot`` explicitly once ready),
#: never on the code default silently moving out from under an
#: already-installed unit.
_LEGACY_APP_ID = "willow"

#: The env var every call site reads to resolve which app_id it speaks to
#: willow-mcp as. One name, read in exactly one place (``app_id()`` below)
#: — Loki 9778E096 F2 found this duplicated as a literal
#: ``os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"``
#: at seven call sites in tick.py alone, each one a place the default could
#: quietly drift from the sender identity used elsewhere.
APP_ID_ENV = "WILLOW_BOT_MCP_APP_ID"


def app_id() -> str:
    """The app_id the steward speaks to willow-mcp as. ``$WILLOW_BOT_MCP_APP_ID``
    if set, else ``_LEGACY_APP_ID`` ("willow") — main's existing behavior,
    unchanged, so an already-installed unit (no ``WILLOW_BOT_MCP_APP_ID``
    line) keeps working exactly as it does today after a merge + pull +
    restart. The steward becomes its own principal (``STEWARD_APP_ID`` /
    sealed 163b9a70) only once the DEPLOY UNIT explicitly sets
    ``WILLOW_BOT_MCP_APP_ID=willow-bot`` — the gate lives on the unit's own
    env, never on this default (Loki 738DB24E F1)."""
    return os.environ.get(APP_ID_ENV, "").strip() or _LEGACY_APP_ID


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
