"""welcome.py — one plain welcome for a first-time contributor.

The troll never speaks to outside contributors (``troll.allowed``), which is
right for the troll. A first-timer's first PR or issue may still get one
plain thank-you, once per person per repo. Off unless the operator profile
turns it on:

    "welcome": {"orgs": ["your-org"], "message": "Thanks, @{login} ..."}

``orgs`` lists where it may speak (empty or absent: nowhere). ``message`` is
optional; ``{login}`` and ``{repo}`` are filled in.

Who is a first-timer is GitHub's call, not ours: ``author_association`` of
``FIRST_TIME_CONTRIBUTOR`` or ``FIRST_TIMER``. Bots are never welcomed
(matched by ``type``, never login). Who was already welcomed lives in
``$WILLOW_HOME/willow-bot/welcomed.json``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("willow-bot.welcome")

FIRST_TIMERS = frozenset({"FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"})
DEFAULT_MESSAGE = ("Thanks for your first contribution to {repo}, @{login}! "
                   "A maintainer will take a look soon.")


def _settings() -> dict:
    from willow_bot import operator_profile

    w = operator_profile.profile().get("welcome")
    return w if isinstance(w, dict) else {}


def _path() -> Path:
    from willow_bot.paths import bot_dir

    return bot_dir() / "welcomed.json"


def _load() -> dict[str, list[str]]:
    p = _path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, list[str]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def line_for(repo: str, user: dict | None, association: object) -> str:
    """The welcome to post, or "" when this author gets none here. Claims
    the (repo, login) pair when it returns a line, so it is said once."""
    user = user or {}
    login = str(user.get("login") or "")
    if not login or user.get("type") == "Bot":
        return ""
    if str(association or "").upper() not in FIRST_TIMERS:
        return ""
    settings = _settings()
    orgs = {str(o).lower() for o in settings.get("orgs") or []}
    if str(repo).split("/", 1)[0].lower() not in orgs:
        return ""
    welcomed = _load()
    key = str(repo).lower()
    seen = [str(x).lower() for x in welcomed.get(key) or []]
    if login.lower() in seen:
        return ""
    template = str(settings.get("message") or DEFAULT_MESSAGE)
    try:
        line = template.format(login=login, repo=repo)
    except (KeyError, IndexError, ValueError):
        log.warning("welcome.message does not format; using the default")
        line = DEFAULT_MESSAGE.format(login=login, repo=repo)
    welcomed[key] = [*(welcomed.get(key) or []), login]
    _save(welcomed)
    return line
