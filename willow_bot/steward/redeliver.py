"""Ask GitHub to send again the App-webhook deliveries the bot never got.

The `ingress` step reads GitHub's delivery record and says when deliveries
are not answered 2xx. A route that sometimes reaches something else (a
second backend behind the same hostname answers 404) loses those events for
good: GitHub does not retry them on its own. This step closes the loop. For
each delivery in the recent record that was not answered 2xx, is an event
some handler here reads, and has no 2xx attempt under the same guid, it asks
GitHub to redeliver it (`github_app.redeliver`). The redelivery carries the
same `X-GitHub-Delivery` guid, so bot.py's delivery guard still runs each
event once.

Bounded: at most ``MAX_ATTEMPTS`` redeliveries per guid (a delivery that
keeps failing is the `ingress` flag's job, not an endless retry) and
``MAX_PER_TICK`` per tick. Attempts are kept in
``willow-bot/redeliver.json``. Read-only toward the bot's own state
otherwise; files nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "redeliver.json"
WINDOW = 100
MAX_ATTEMPTS = 5
MAX_PER_TICK = 20
#: How long a guid's attempt count is kept after its last attempt.
KEEP_DAYS = 7


def path() -> Path:
    return bot_dir() / FILE_NAME


def _fetch_deliveries() -> list[dict]:
    """A module-level name so the test floor can keep it off the network."""
    import github_app

    return github_app.list_hook_deliveries(per_page=WINDOW)


def _redeliver(delivery_id: int) -> None:
    import github_app

    github_app.redeliver(delivery_id)


def _wanted_events() -> frozenset[str]:
    from willow_bot.steward.app_grant import HANDLED_EVENTS, IMPLICIT_EVENTS, REQUIRED_EVENTS

    return REQUIRED_EVENTS | HANDLED_EVENTS | IMPLICIT_EVENTS


def _ok(d: dict) -> bool:
    try:
        return 200 <= int(d.get("status_code") or 0) < 300
    except (TypeError, ValueError):
        return False


def plan(deliveries: list[dict], attempts: dict[str, dict], *, limit: int = MAX_PER_TICK) -> list[dict]:
    """The deliveries to ask for again, newest first. Pure."""
    wanted = _wanted_events()
    answered = {d.get("guid") for d in deliveries if _ok(d)}
    out: list[dict] = []
    seen_guids: set[str] = set()
    for d in sorted(deliveries, key=lambda x: int(x.get("id") or 0), reverse=True):
        guid = d.get("guid")
        if not guid or guid in answered or guid in seen_guids or _ok(d):
            continue
        if d.get("event") not in wanted:
            continue
        seen_guids.add(guid)
        if int((attempts.get(guid) or {}).get("n") or 0) >= MAX_ATTEMPTS:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def _load() -> dict[str, dict]:
    p = path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(attempts: dict[str, dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(attempts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def run() -> dict[str, Any]:
    """One pass. Returns the receipt (the caller emits it)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt: dict[str, Any] = {"event": "steward_redeliver", "at": at}
    try:
        deliveries = _fetch_deliveries()
    except Exception as exc:  # noqa: BLE001 — unreachable is its own state, never "ok"
        receipt.update(status="unreachable", detail=f"{type(exc).__name__}: {exc}"[:300])
        return receipt
    now = time.time()
    attempts = {g: a for g, a in _load().items()
                if isinstance(a, dict) and now - float(a.get("last") or 0) < KEEP_DAYS * 86400}
    asked: list[dict] = []
    refused: list[dict] = []
    for d in plan(deliveries, attempts):
        guid = d["guid"]
        try:
            _redeliver(int(d["id"]))
        except Exception as exc:  # noqa: BLE001 — one refusal never stops the rest
            refused.append({"id": d.get("id"), "event": d.get("event"), "error": str(exc)[:200]})
            continue
        n = int((attempts.get(guid) or {}).get("n") or 0) + 1
        attempts[guid] = {"n": n, "last": now, "event": d.get("event")}
        asked.append({"id": d.get("id"), "event": d.get("event"), "status_code": d.get("status_code"),
                      "attempt": n})
    gave_up = sorted(g for g, a in attempts.items() if int(a.get("n") or 0) >= MAX_ATTEMPTS)
    _save(attempts)
    receipt.update(status="partial" if refused else "ok", window=len(deliveries),
                   redelivered=asked, refused=refused, gave_up=len(gave_up))
    return receipt
