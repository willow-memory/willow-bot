"""Is GitHub's App webhook actually reaching the bot? Asked of GitHub, each tick.

The desk, 2026-09-28: between 16:59 and 17:02Z, GitHub POSTs to
``/webhook`` landed on willow-mcp serve at ``127.0.0.1:8768`` and answered
404, while the bot listens on ``:9000``. Nothing on the box said so — a
misrouted ingress is silent from the receiving side, because the receiver
never hears anything. GitHub records what every delivery got back, so the
honest place to ask is GitHub: ``GET /app/hook/deliveries`` under the App's
own JWT. No model, no ``gh``, no lease.

This reads the **App webhook** only. A repository or organization hook is a
different delivery record (and would need ``webhooks: read``, which this
App does not hold), so a fault on one of those is not seen here — every
line this step writes says "App webhook" for that reason.

States, never collapsed:

- ``unreachable`` — GitHub could not be asked (App not configured, network,
  HTTP error). Says nothing about the ingress either way.
- ``malformed`` — GitHub answered, but not in a shape this step can read.
- ``empty`` — GitHub has no App-webhook deliveries on record.
- ``ok`` — the newest delivery was answered 2xx, within the last
  ``WILLOW_BOT_INGRESS_QUIET_DAYS`` days (default 7). ``failed_in_window``
  still counts failures further back.
- ``quiet`` — the newest delivery was answered 2xx, but it is older than
  that: nothing recent to judge the route by. Reported, not flagged, and it
  does not resolve an open flag.
- ``degraded`` — the newest delivery was not answered 2xx, but fewer than
  ``FAILING_STREAK`` in a row (so a record of one or two failed deliveries
  is degraded, never an episode). Reported, not flagged.
- ``failing`` — the newest ``FAILING_STREAK`` deliveries were not answered
  2xx and at least one of them got an HTTP answer (the route reaches
  something, and it is the wrong thing: a 404 from the wrong listener).
- ``unanswered`` — the newest ``FAILING_STREAK`` deliveries got no HTTP
  answer at all (status code 0: timed out, refused, TLS). Nothing is
  listening, or nothing is reachable.

Only 2xx is success: GitHub does not follow redirects, so a 3xx is a failure.

``failing`` and ``unanswered`` each file ONE ``human_required`` item per
episode, resolved when the newest delivery is 2xx again. Every verdict
carries ``latest_age_s`` — how old the newest delivery is — because a
month-old 2xx still reads ``ok``.

The hook URL never leaves this module whole: it is reduced to
``scheme://host[:port]/path`` (no login, no query, no fragment, and any path
segment that is not a plain lowercase word shown as ``…``) before it
reaches a receipt, ``ingress.json``, the status surface or a filed item,
and ``github_app.hook_url`` returns only the URL, never the config's
``secret``.

State lives in ``$WILLOW_HOME/willow-bot/ingress.json``: the last verdict
(what ``willow-bot-steward status`` shows) and the open flag, if any.
Receipts go to the steward tick receipts as ``steward_ingress``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from willow_bot.paths import bot_dir

FILE_NAME = "ingress.json"
WINDOW = 30
FAILING_STREAK = 3
_FLAGGED = ("failing", "unanswered")
#: A newest delivery older than this reads ``quiet``, not ``ok``: an old
#: success says nothing about the route now (Loki 757108E8).
QUIET_AFTER_DAYS_ENV = "WILLOW_BOT_INGRESS_QUIET_DAYS"
QUIET_AFTER_DAYS_DEFAULT = 7.0


def _quiet_after_s() -> float:
    raw = os.environ.get(QUIET_AFTER_DAYS_ENV, "").strip()
    try:
        days = float(raw) if raw else QUIET_AFTER_DAYS_DEFAULT
    except ValueError:
        days = QUIET_AFTER_DAYS_DEFAULT
    return max(days, 0.0) * 86400.0


def path() -> Path:
    return bot_dir() / FILE_NAME


def _fetch_deliveries() -> list[dict]:
    """A module-level name so the test floor (tests/conftest.py) can keep it
    off the network."""
    import github_app  # local import so a test can monkeypatch the module

    return github_app.list_hook_deliveries(per_page=WINDOW)


def _fetch_hook_url() -> str:
    import github_app

    return github_app.hook_url()


_SAFE_SEGMENT = re.compile(r"^[a-z][a-z_.-]{0,31}$")
#: GitHub's own token prefixes: a segment starting with one is a token,
#: whatever its letters (``ghp_``, ``github_pat_``, ...).
_TOKEN_PREFIX = re.compile(r"^(gh[pousr]_|github_pat_)", re.IGNORECASE)


def _redact_segment(seg: str) -> str:
    """A path segment is kept only when it reads as a word — lowercase
    letters and ``_ . -`` only, at most 32 characters (``webhook``,
    ``hooks``, ``github-app``) — and does not start with a GitHub token
    prefix. Anything else is shown as ``…``: a relay channel
    (``smee.io/<channel>``) is mixed-case, often letters only, and a token
    placed in the path is exactly that shape (Loki 757108E8, 87DF2A04)."""
    if not seg:
        return seg
    if _TOKEN_PREFIX.match(seg) or not _SAFE_SEGMENT.match(seg):
        return "…"
    return seg


def redact_url(url: str) -> str:
    """``scheme://host[:port]/path`` with every path segment that could be a
    secret replaced by ``…`` — drops any login, query and fragment, the
    other places a credential rides in a webhook URL. An IPv6 host keeps
    its brackets (``http://[::1]:9000/webhook``)."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    path = "/".join(_redact_segment(seg) for seg in parts.path.split("/"))
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def _code(d: dict) -> int:
    try:
        return int(d.get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _ok(d: dict) -> bool:
    return 200 <= _code(d) < 300


def _id(d: dict) -> int | None:
    try:
        return int(d.get("id"))
    except (TypeError, ValueError):
        return None


def _age_s(delivered_at: object, now: float) -> int | None:
    if not isinstance(delivered_at, str) or not delivered_at:
        return None
    try:
        dt = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int(now - dt.timestamp()))


def _brief(d: dict) -> dict:
    return {"id": d.get("id"), "event": d.get("event"), "status_code": d.get("status_code"),
            "status": d.get("status"), "delivered_at": d.get("delivered_at")}


def assess(deliveries: list[dict], *, now: float | None = None) -> dict[str, Any]:
    """The verdict on a list of deliveries (any order; newest decided by
    ``id``, which GitHub assigns increasing). Pure — no I/O."""
    now = time.time() if now is None else now
    rows = [d for d in deliveries if isinstance(d, dict)]
    if not rows:
        return {"status": "empty", "window": 0}
    if any(_id(d) is None for d in rows):
        return {"status": "malformed", "window": len(rows),
                "detail": "a delivery without an integer id; cannot order them"}
    rows.sort(key=lambda d: _id(d) or 0, reverse=True)
    streak = 0
    for d in rows:
        if _ok(d):
            break
        streak += 1
    by_code: dict[str, int] = {}
    for d in rows:
        key = str(_code(d))
        by_code[key] = by_code.get(key, 0) + 1
    last_ok = next((d for d in rows if _ok(d)), None)
    last_fail = next((d for d in rows if not _ok(d)), None)
    age = _age_s(rows[0].get("delivered_at"), now)
    if streak == 0:
        status = "quiet" if age is not None and age > _quiet_after_s() else "ok"
    elif streak < FAILING_STREAK:
        # Fewer than FAILING_STREAK failures in a row — including a record
        # holding only one or two deliveries — is not yet an episode.
        status = "degraded"
    elif all(_code(d) == 0 for d in rows[:streak]):
        status = "unanswered"
    else:
        status = "failing"
    return {
        "status": status,
        "window": len(rows),
        "failing_streak": streak,
        "failed_in_window": sum(1 for d in rows if not _ok(d)),
        "by_status_code": dict(sorted(by_code.items())),
        "latest": _brief(rows[0]),
        "latest_age_s": age,
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


def _answer(d: dict) -> str:
    """``HTTP 404`` or ``no HTTP answer``, plus GitHub's own status text
    when it says more than the code (never "404 … 404")."""
    code = _code(d)
    text = str(d.get("status") or "").strip()
    head = f"HTTP {code}" if code else "no HTTP answer"
    if text and (not code or str(code) not in text):
        return f"{head} ({text})"
    return head


def _flag_title(verdict: dict) -> str:
    lf = verdict.get("last_failure") or {}
    n = verdict.get("failing_streak")
    if verdict["status"] == "unanswered":
        return f"App webhook unanswered: last {n} deliveries got {_answer(lf)}"[:200]
    return f"App webhook failing: last {n} deliveries answered {_answer(lf)}"[:200]


def _flag_summary(verdict: dict, hook_url: str) -> str:
    ls = verdict.get("last_success") or {}
    port = os.environ.get("BOT_PORT", "").strip() or "9000"
    return "\n".join([
        f"GitHub's delivery record for the willows-bot App webhook: the newest "
        f"{verdict.get('failing_streak')} of {verdict.get('window')} deliveries were not answered 2xx "
        f"(by status code: {verdict.get('by_status_code')}; 0 = no HTTP answer).",
        f"GitHub posts to: {hook_url or '(hook URL could not be read)'}",
        f"Last success: {ls.get('delivered_at') or 'none in the window'}.",
        f"The bot listens on 127.0.0.1:{port}/webhook; the ingress route must reach it.",
        "This is the App webhook only; a repository or organization hook is not read here.",
    ])


def run(*, enable_mcp: bool, call, app: str) -> dict[str, Any]:
    """One ingress read. Returns the receipt (the caller emits it)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt: dict[str, Any] = {"event": "steward_ingress", "at": at, "hook": "app"}
    state = load()
    flag = state.get("flag") if isinstance(state.get("flag"), dict) else None
    try:
        deliveries = _fetch_deliveries()
    except Exception as exc:  # noqa: BLE001 — unreachable is its own state, never "ok"
        verdict: dict[str, Any] = {"status": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    else:
        verdict = assess(deliveries)
    receipt.update(verdict)

    if verdict["status"] in _FLAGGED:
        hook_url = ""
        try:
            hook_url = redact_url(_fetch_hook_url())
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
            item_id = result.get("id") if isinstance(result, dict) and not err else None
            if err or not item_id:
                # No id means nothing to resolve later: not filed, retried
                # next tick (a placeholder id would never resolve).
                receipt["flag"] = {"state": "could-not-file",
                                   "error": str(err)[:300] if err else "enqueue returned no item id"}
            else:
                flag = {"id": item_id, "since": at}
                receipt["flag"] = {"state": "filed", "id": item_id}
    elif verdict["status"] == "ok" and flag and flag.get("id"):
        if not enable_mcp or call is None:
            receipt["flag"] = {"state": "open", "id": flag["id"], "detail": "recovered; resolve owed"}
        else:
            try:
                result = call("human_required_resolve", {
                    "app_id": app, "item_id": flag["id"], "status": "resolved",
                    "note": f"App webhook deliveries answering 2xx again at {at}",
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
        # quiet, degraded, empty, malformed or unreachable: the flag stands; none
        # of those says the ingress recovered.
        receipt["flag"] = {"state": "open", "id": flag["id"], "since": flag.get("since")}

    _save({"last": {k: v for k, v in receipt.items() if k != "event"}, "flag": flag})
    return receipt
