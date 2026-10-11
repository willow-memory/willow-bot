"""Can the App do what this code asks of it? Asked of GitHub, each tick.

A handler for an event the App is not subscribed to never runs, and says
nothing: the receiver never hears the event. A permission the App lacks
shows up only as a 403 somewhere downstream. Both are facts GitHub states
about the App itself in ``GET /app`` (under the App's own JWT), so this step
asks there and compares against what the code needs.

``REQUIRED_*`` is what the bot cannot work without. ``HANDLED_EVENTS`` is
every event some handler here reads (``router.route`` and
``fleet_bridge.handle``); one of those left unsubscribed is not a fault, but
it is a feature that silently does nothing, so it is named.

States, never collapsed:

- ``unreachable`` — GitHub could not be asked (App not configured, network,
  HTTP error). Says nothing about the grant.
- ``malformed`` — GitHub answered in a shape this step cannot read.
- ``failing`` — a required permission or event is missing.
- ``partial`` — everything required is there; some handled events are not
  subscribed (``unsubscribed``).
- ``ok`` — everything required and everything handled is there.

Writes ``willow-bot/app_grant.json`` (``{"last": <verdict>}``) for the status
surface. Read-only toward GitHub; files nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "app_grant.json"

REQUIRED_PERMISSIONS: dict[str, str] = {
    "metadata": "read",
    "issues": "write",
    "pull_requests": "write",
    "checks": "read",
    "contents": "read",
}

#: Broader than the bot needs; reported, never a failure.
BROADER_THAN_NEEDED: dict[str, str] = {
    "contents": "write",
    "actions": "write",
    "administration": "write",
}

# GitHub sends these to every App; they are not in GET /app events.
IMPLICIT_EVENTS = frozenset({"installation", "installation_repositories"})

REQUIRED_EVENTS = frozenset({
    "pull_request",
    "push",
    "issues",
    "check_run",
    "create",
    "issue_comment",
    "pull_request_review",
})

#: Every event a handler reads, beyond the required ones.
HANDLED_EVENTS = frozenset({
    "fork",
    "watch",
    "release",
    "discussion",
    "workflow_run",
    "pull_request_review_comment",
})

_LEVEL = {"none": 0, "read": 1, "write": 2, "admin": 3}


def path() -> Path:
    return bot_dir() / FILE_NAME


def _fetch_app() -> dict:
    """A module-level name so the test floor (tests/conftest.py) can keep it
    off the network."""
    import github_app

    data = github_app._app_get("/app")
    if not isinstance(data, dict):
        raise ValueError("unexpected /app answer")
    return data


def assess(app: dict) -> dict[str, Any]:
    """The verdict for one ``GET /app`` answer. Pure."""
    perms = app.get("permissions")
    events = app.get("events")
    if not isinstance(perms, dict) or not isinstance(events, list):
        return {"status": "malformed", "detail": "GET /app carried no permissions or events list"}
    perms = {str(k): str(v) for k, v in perms.items()}
    subscribed = {str(e) for e in events} | IMPLICIT_EVENTS

    missing_permissions = {
        k: {"need": need, "have": perms.get(k)}
        for k, need in REQUIRED_PERMISSIONS.items()
        if _LEVEL.get(perms.get(k, "none"), 0) < _LEVEL[need]
    }
    broader = {k: perms[k] for k, lvl in BROADER_THAN_NEEDED.items()
               if _LEVEL.get(perms.get(k, "none"), 0) >= _LEVEL[lvl]}
    missing_events = sorted(REQUIRED_EVENTS - subscribed)
    unsubscribed = sorted(HANDLED_EVENTS - subscribed)

    if missing_permissions or missing_events:
        status = "failing"
    elif unsubscribed:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "app": str(app.get("slug") or app.get("name") or ""),
        "missing_permissions": missing_permissions,
        "missing_events": missing_events,
        "unsubscribed": unsubscribed,
        "broader_than_needed": broader,
    }


def load() -> dict[str, Any]:
    p = path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def run() -> dict[str, Any]:
    """One read of the App's grant. Returns the receipt (the caller emits it)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        app = _fetch_app()
    except Exception as exc:  # noqa: BLE001 — unreachable is its own state, never "ok"
        verdict: dict[str, Any] = {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    else:
        verdict = assess(app)
    verdict["at"] = at
    _save({"last": verdict})
    return {"event": "steward_app_grant", **verdict}
