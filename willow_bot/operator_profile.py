"""operator_profile.py — who runs this install, and what it looks after.

willow-bot is one codebase that anyone can install as their own App. Nothing
about a particular operator (their login, orgs, repos, clone layout, the
App's display name, where the voice speaks) lives in code. It lives in one
profile per install:

    $WILLOW_HOME/willow-bot/operator.json

or wherever ``WILLOW_BOT_OPERATOR_PROFILE`` points. ``deploy/operator.example.json``
documents every key. Every key is optional; an absent key falls back to
what can be learned from GitHub (``gh api /user``, org memberships), and
failing that to *nothing*: no orgs, no repos, a silent voice. It never
falls back to some other operator's setup.

Precedence for each value: the older per-value env overrides (kept so a
running unit's env still wins), then the profile, then discovery, then
empty.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("willow-bot.operator")

PROFILE_ENV = "WILLOW_BOT_OPERATOR_PROFILE"
DEFAULT_BOT_NAME = "willow-bot"


def profile_path() -> Path | None:
    raw = os.environ.get(PROFILE_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        from willow_bot.paths import bot_dir

        return bot_dir() / "operator.json"
    except RuntimeError:  # no box configured: no profile either
        return None


@lru_cache(maxsize=1)
def profile() -> dict[str, Any]:
    """The operator profile, or ``{}`` when there is none. A profile that
    exists but does not parse is logged and treated as absent: an install
    with a broken profile is an install with no operator facts, never one
    with someone else's."""
    path = profile_path()
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("operator profile %s unreadable (%s); running with no operator facts", path, exc)
        return {}
    if not isinstance(data, dict):
        log.error("operator profile %s is not an object; ignored", path)
        return {}
    return data


def reset() -> None:
    """Drop the cached profile and discoveries (tests; a profile edit)."""
    profile.cache_clear()
    clear = getattr(_discovered_login, "cache_clear", None)
    if clear is not None:  # a test may have replaced it
        clear()


def _env_list(name: str) -> list[str] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return [v.strip() for v in raw.split(",") if v.strip()]


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _section(name: str) -> dict:
    sec = profile().get(name)
    return sec if isinstance(sec, dict) else {}


@lru_cache(maxsize=1)
def _discovered_login() -> str:
    """The login of whoever the box's ``gh`` is signed in as, or ""."""
    try:
        out = subprocess.check_output(["gh", "api", "/user", "--jq", ".login"], text=True,
                                      stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.strip()


def login() -> str:
    """The operator's GitHub login: ``LOKI_PR_WATCH_USER``, else the
    profile's ``login``, else whoever ``gh`` is signed in as, else ""."""
    env = os.environ.get("LOKI_PR_WATCH_USER", "").strip()
    if env:
        return env
    return str(profile().get("login") or "").strip() or _discovered_login()


def orgs() -> list[str] | None:
    """Orgs the profile names, or ``None`` when it names none (the caller
    then discovers them from org memberships)."""
    if "orgs" not in profile():
        return None
    return _str_list(profile().get("orgs"))


def upstream_watch_repos() -> list[str]:
    """Other people's repos the operator contributes to, in attention order."""
    return _env_list("WILLOW_BOT_UPSTREAM_WATCH_REPOS") or _str_list(profile().get("upstream_watch"))


def core_repos() -> list[str]:
    """Repos the App must be installed on for this fleet to work
    (``scripts/audit_app_config.py``)."""
    return _str_list(profile().get("core_repos"))


def github_root() -> Path:
    """Where clones live: ``GITHUB_ROOT`` (or Loki's ``WILLOW_GITHUB_ROOT``),
    else the profile's ``github_root``, else ``~/github``."""
    raw = (os.environ.get("GITHUB_ROOT", "").strip() or os.environ.get("WILLOW_GITHUB_ROOT", "").strip()
           or str(profile().get("github_root") or "").strip())
    return Path(raw).expanduser() if raw else Path.home() / "github"


def checkouts() -> dict[str, Path]:
    """``owner/name`` → local clone, for clones not at ``<root>/<owner>/<name>``.
    Relative paths are under ``github_root()``."""
    raw = profile().get("checkouts")
    if not isinstance(raw, dict):
        return {}
    root = github_root()
    out = {}
    for repo, path in raw.items():
        p = Path(str(path)).expanduser()
        out[str(repo)] = p if p.is_absolute() else root / p
    return out


def clone_orgs() -> list[str]:
    """Extra org directories under ``github_root()`` to look in for a clone
    whose owner directory does not match (a repo moved between orgs)."""
    return _str_list(profile().get("clone_orgs"))


def bot_name() -> str:
    """The App's display name, as stamped on deposits: ``WILLOW_BOT_NAME``,
    else the profile's ``bot_name``, else ``willow-bot``. A label only;
    nothing matches a bot by name (BOT-INVENTORY.md)."""
    return (os.environ.get("WILLOW_BOT_NAME", "").strip()
            or str(profile().get("bot_name") or "").strip() or DEFAULT_BOT_NAME)


def voice_orgs() -> list[str] | None:
    """Orgs the voice may speak in, or ``None`` when the profile does not
    say (the caller then uses ``willow-bot.json``'s ``troll_scope.orgs``)."""
    voice = _section("voice")
    return _str_list(voice.get("orgs")) if "orgs" in voice else None


def grove_ci_red_orgs() -> list[str]:
    """Orgs whose CI reds also go to the operator's Grove channel."""
    return [o.lower() for o in _str_list(_section("grove").get("ci_red_orgs"))]


def grove_operator_sender() -> str:
    """The operator's sender name in Grove, or "" when not configured."""
    return str(_section("grove").get("operator_sender") or "").strip()


def broker_pairs() -> dict[str, dict[str, Any]]:
    """``owner/name`` → ``{"venv", "extras"}`` fallback for the broker
    allowlist when its file is absent."""
    raw = profile().get("broker_pairs")
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def repo_owner_in(repo: str, owners: list[str]) -> bool:
    return str(repo).split("/", 1)[0].lower() in {o.lower() for o in owners}
