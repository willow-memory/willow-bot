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
