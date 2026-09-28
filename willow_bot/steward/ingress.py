"""Is GitHub's webhook actually reaching the bot? Asked of GitHub, each tick.

The desk, 2026-09-28: GitHub POSTed ``/webhook`` to willow-mcp on
``127.0.0.1:8768`` and every delivery answered 404 all day, while the bot
listens on ``:9000``. Nothing on the box said so — a misrouted ingress is
silent from the receiving side, because the receiver never hears anything.
GitHub records what every delivery got back, so the one honest place to
ask is GitHub: ``GET /app/hook/deliveries`` under the App's own JWT. No
model, no ``gh``, no lease.

Four states, never collapsed (the status surface's three-state rule plus
the verdict):

- ``unreachable`` — GitHub could not be asked (App not configured, network,
  HTTP error). Says nothing about the ingress either way.
- ``empty`` — GitHub has no deliveries on record.
- ``failing`` — the newest ``FAILING_STREAK`` deliveries all answered
  non-2xx (or nothing answered). One ``human_required`` item is filed,
  once per episode, naming the status the receiving end gave and the URL
  GitHub posts to; it is resolved when deliveries succeed again.
- ``ok`` — the newest delivery succeeded. ``failed_in_window`` still counts
  any failures further back, so a flapping route is visible without being
  flagged.

State lives in ``$WILLOW_HOME/willow-bot/ingress.json``: the last verdict
(what ``willow-bot-steward status`` shows) and the open flag, if any.
Receipts go to the steward tick receipts as ``steward_ingress``.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "ingress.json"
WINDOW = 30
FAILING_STREAK = 3


def path() -> Path:
    return bot_dir() / FILE_NAME


def _fetch_deliveries() -> list[dict]:
    """A module-level name so the test floor (tests/conftest.py) can keep it
    off the network."""
    import github_app  # local import so a test can monkeypatch the module

    return github_app.list_hook_deliveries(per_page=WINDOW)


def _fetch_hook_url() -> str:
    import github_app

    return str(github_app.hook_config().get("url") or "")


def _ok(d: dict) -> bool:
    try:
        code = int(d.get("status_code") or 0)
    except (TypeError, ValueError):
        return False
    return 200 <= code < 300


def _brief(d: dict) -> dict:
    return {"id": d.get("id"), "event": d.get("event"), "status_code": d.get("status_code"),
            "status": d.get("status"), "delivered_at": d.get("delivered_at")}


def assess(deliveries: list[dict]) -> dict[str, Any]:
    """The verdict on a list of deliveries (any order; newest decided by
    ``id``, which GitHub assigns increasing). Pure — no I/O."""
    rows = sorted((d for d in deliveries if isinstance(d, dict)),
                  key=lambda d: int(d.get("id") or 0), reverse=True)
    if not rows:
        return {"status": "empty", "window": 0}
    streak = 0
    for d in rows:
        if _ok(d):
            break
        streak += 1
    by_code: dict[str, int] = {}
    for d in rows:
        key = str(d.get("status_code"))
        by_code[key] = by_code.get(key, 0) + 1
    last_ok = next((d for d in rows if _ok(d)), None)
    last_fail = next((d for d in rows if not _ok(d)), None)
    failing = streak >= min(FAILING_STREAK, len(rows)) and streak > 0
    return {
        "status": "failing" if failing else "ok",
        "window": len(rows),
        "failing_streak": streak,
        "failed_in_window": sum(1 for d in rows if not _ok(d)),
        "by_status_code": dict(sorted(by_code.items())),
        "latest": _brief(rows[0]),
        "last_success": _brief(last_ok) if last_ok else None,
        "last_failure": _brief(last_fail) if last_fail else None,
    }


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".ingress.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _flag_title(verdict: dict) -> str:
    lf = verdict.get("last_failure") or {}
    code = lf.get("status_code")
    what = f"{code} {lf.get('status') or ''}".strip() if code else (lf.get("status") or "no answer")
    return f"Webhook ingress failing: last {verdict.get('failing_streak')} deliveries answered {what}"[:200]


def _flag_summary(verdict: dict, hook_url: str) -> str:
    ls = verdict.get("last_success") or {}
    lines = [
        f"GitHub's record of the willows-bot App webhook: the newest {verdict.get('failing_streak')} "
        f"of {verdict.get('window')} deliveries were not answered 2xx "
        f"(by status code: {verdict.get('by_status_code')}).",
        f"GitHub posts to: {hook_url or '(hook URL could not be read)'}",
        f"Last success: {ls.get('delivered_at') or 'none in the window'}.",
        f"The bot listens on 127.0.0.1:{os.environ.get('BOT_PORT', '').strip() or '9000'}/webhook; "
        "the ingress route must reach it.",
    ]
    return "\n".join(lines)


def run(*, enable_mcp: bool, call, app: str) -> dict[str, Any]:
    """One ingress read. Returns the receipt (the caller emits it)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt: dict[str, Any] = {"event": "steward_ingress", "at": at}
    state = load()
    flag = state.get("flag") if isinstance(state.get("flag"), dict) else None
    try:
        verdict = assess(_fetch_deliveries())
    except Exception as exc:  # noqa: BLE001 — unreachable is its own state, never "ok"
        verdict = {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    receipt.update(verdict)

    if verdict["status"] == "failing":
        hook_url = ""
        try:
            hook_url = _fetch_hook_url()
        except Exception:  # noqa: BLE001 — the flag still files without the URL
            pass
        receipt["hook_url"] = hook_url
        if flag and flag.get("id"):
            receipt["flag"] = {"state": "open", "id": flag["id"], "since": flag.get("since")}
        elif not enable_mcp or call is None:
            receipt["flag"] = {"state": "unfiled", "reason": "WILLOW_BOT_MCP not enabled"}
        else:
            try:
                result = call("human_required_enqueue", {
                    "app_id": app, "kind": "review", "priority": "high",
                    "title": _flag_title(verdict), "summary": _flag_summary(verdict, hook_url),
                    "source_ref": hook_url or "github:/app/hook/deliveries",
                })
                err = result.get("error") if isinstance(result, dict) else None
            except Exception as exc:  # noqa: BLE001 — retried next tick
                result, err = None, str(exc)[:300]
            if err:
                receipt["flag"] = {"state": "could-not-file", "error": str(err)[:300]}
            else:
                item_id = (result.get("id") if isinstance(result, dict) else None) or "filed"
                flag = {"id": item_id, "since": at}
                receipt["flag"] = {"state": "filed", "id": item_id}
    elif verdict["status"] == "ok" and flag and flag.get("id"):
        if not enable_mcp or call is None:
            receipt["flag"] = {"state": "open", "id": flag["id"], "detail": "recovered; resolve owed"}
        else:
            try:
                result = call("human_required_resolve", {
                    "app_id": app, "item_id": flag["id"], "status": "resolved",
                    "note": f"webhook deliveries answering 2xx again at {at}",
                })
                err = result.get("error") if isinstance(result, dict) else None
            except Exception as exc:  # noqa: BLE001 — retried next tick
                err = str(exc)[:300]
            if err:
                receipt["flag"] = {"state": "resolve-refused", "id": flag["id"], "error": str(err)[:300]}
            else:
                receipt["flag"] = {"state": "resolved", "id": flag["id"]}
                flag = None
    elif flag and flag.get("id"):
        # empty or unreachable: the flag stands; nothing here says it recovered.
        receipt["flag"] = {"state": "open", "id": flag["id"], "since": flag.get("since")}

    _save({"last": {k: v for k, v in receipt.items() if k != "event"}, "flag": flag})
    return receipt
