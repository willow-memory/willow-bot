"""The App-grant step: GitHub's own statement of what the App may do and
which events it hears, against what the code needs. No network."""
from __future__ import annotations

import json
import re
from pathlib import Path

from willow_bot import status
from willow_bot.steward import app_grant, tick

ROOT = Path(__file__).resolve().parents[2]
FULL_PERMS = {"metadata": "read", "issues": "write", "pull_requests": "write", "checks": "read",
              "contents": "read"}


def _app(perms=None, events=None):
    return {"slug": "my-bot", "permissions": FULL_PERMS if perms is None else perms,
            "events": sorted(app_grant.REQUIRED_EVENTS | app_grant.HANDLED_EVENTS) if events is None else events}


def test_everything_granted_is_ok():
    v = app_grant.assess(_app())
    assert v["status"] == "ok" and v["app"] == "my-bot"
    assert v["missing_permissions"] == {} and v["missing_events"] == [] and v["unsubscribed"] == []


def test_a_handled_event_left_unsubscribed_is_partial_and_named():
    v = app_grant.assess(_app(events=sorted(app_grant.REQUIRED_EVENTS)))
    assert v["status"] == "partial"
    assert v["unsubscribed"] == sorted(app_grant.HANDLED_EVENTS)


def test_a_missing_required_event_or_permission_is_failing():
    v = app_grant.assess(_app(events=sorted(app_grant.REQUIRED_EVENTS - {"check_run"})))
    assert v["status"] == "failing" and v["missing_events"] == ["check_run"]
    v = app_grant.assess(_app(perms={**FULL_PERMS, "issues": "read"}))
    assert v["status"] == "failing" and v["missing_permissions"] == {"issues": {"need": "write", "have": "read"}}


def test_a_higher_level_satisfies_a_lower_need_and_is_noted_when_broad():
    v = app_grant.assess(_app(perms={**FULL_PERMS, "contents": "write", "checks": "write"}))
    assert v["status"] == "ok" and v["broader_than_needed"] == {"contents": "write"}


def test_implicit_events_are_not_required_in_the_list():
    assert app_grant.assess(_app())["missing_events"] == []


def test_a_shape_it_cannot_read_is_malformed():
    assert app_grant.assess({"permissions": "?"})["status"] == "malformed"


def test_unreachable_is_its_own_state_and_lands_in_status(monkeypatch):
    receipt = app_grant.run()  # the test floor makes _fetch_app raise
    assert receipt["event"] == "steward_app_grant" and receipt["status"] == "unreachable"
    s = status.report()["app"]
    assert s["status"] == "populated" and s["verdict"] == "unreachable"


def test_a_partial_grant_reaches_status(monkeypatch):
    monkeypatch.setattr(app_grant, "_fetch_app", lambda: _app(events=sorted(app_grant.REQUIRED_EVENTS)))
    tick.main(["app-grant"])
    s = status.report()["app"]
    assert s["verdict"] == "partial" and "fork" in s["last"]["unsubscribed"]


def test_status_before_the_step_ever_ran_is_empty():
    assert status.report()["app"]["status"] == "empty"


def test_every_routed_event_is_accounted_for():
    """A new handler in router.py must be named here too, or the grant check
    would never notice its event going unheard."""
    src = (ROOT / "router.py").read_text()
    block = src[src.index("handlers = {"):src.index("}", src.index("handlers = {"))]
    routed = set(re.findall(r'"([a-z_]+)":', block))
    known = app_grant.REQUIRED_EVENTS | app_grant.HANDLED_EVENTS
    assert routed and routed <= known, routed - known


def test_the_written_file_is_json_with_a_last_verdict():
    app_grant.run()
    assert json.loads(app_grant.path().read_text())["last"]["status"] == "unreachable"


# ── the flag ───────────────────────────────────────────────────────────────


class _Broker:
    def __init__(self, refuse=False):
        self.calls, self.refuse = [], refuse

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        if self.refuse:
            return {"error": "refused"}
        return {"id": "item-1"} if tool == "human_required_enqueue" else {"ok": True}


def _failing():
    return _app(perms={**FULL_PERMS, "checks": "none"})


def test_failing_files_one_flag_and_holds_it(monkeypatch):
    broker = _Broker()
    monkeypatch.setattr(app_grant, "_fetch_app", _failing)
    r = app_grant.run(enable_mcp=True, call=broker, app="willow")
    assert r["flag"] == {"state": "filed", "id": "item-1"}
    assert "permission checks: need read" in broker.calls[0][1]["summary"]
    r = app_grant.run(enable_mcp=True, call=broker, app="willow")
    assert r["flag"]["state"] == "open" and len(broker.calls) == 1


def test_recovery_resolves_the_flag_and_partial_counts(monkeypatch):
    broker = _Broker()
    monkeypatch.setattr(app_grant, "_fetch_app", _failing)
    app_grant.run(enable_mcp=True, call=broker, app="willow")
    monkeypatch.setattr(app_grant, "_fetch_app", lambda: _app(events=sorted(app_grant.REQUIRED_EVENTS)))
    r = app_grant.run(enable_mcp=True, call=broker, app="willow")
    assert r["status"] == "partial" and r["flag"] == {"state": "resolved", "id": "item-1"}
    assert broker.calls[-1][0] == "human_required_resolve"
    assert app_grant.load()["flag"] is None


def test_unreachable_does_not_resolve(monkeypatch):
    broker = _Broker()
    monkeypatch.setattr(app_grant, "_fetch_app", _failing)
    app_grant.run(enable_mcp=True, call=broker, app="willow")
    monkeypatch.setattr(app_grant, "_fetch_app", lambda: (_ for _ in ()).throw(OSError("down")))
    r = app_grant.run(enable_mcp=True, call=broker, app="willow")
    assert r["status"] == "unreachable" and r["flag"]["state"] == "open"


def test_without_mcp_failing_is_unfiled_and_a_refusal_retries(monkeypatch):
    monkeypatch.setattr(app_grant, "_fetch_app", _failing)
    assert app_grant.run()["flag"]["state"] == "unfiled"
    r = app_grant.run(enable_mcp=True, call=_Broker(refuse=True), app="willow")
    assert r["flag"]["state"] == "could-not-file" and app_grant.load()["flag"] is None


def test_status_shows_the_open_flag(monkeypatch):
    monkeypatch.setattr(app_grant, "_fetch_app", _failing)
    app_grant.run(enable_mcp=True, call=_Broker(), app="willow")
    assert "FLAG OPEN" in status.brief()
