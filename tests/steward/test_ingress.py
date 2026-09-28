"""Is GitHub's webhook reaching the bot? (desk, 2026-09-28)

GitHub POSTed /webhook to willow-mcp on :8768 and every delivery answered
404 all day while the bot listened on :9000. The steward now reads
GitHub's own delivery record each tick. GitHub (`ingress._fetch_*`) and
the MCP client are fakes here; nothing reaches the network.
"""
from __future__ import annotations

import json

import pytest

from willow_bot import status
from willow_bot.steward import ingress, tick

HOOK = "https://hooks.example.invalid/webhook"


def _d(i, code, status_text=None, event="pull_request"):
    return {"id": i, "status_code": code, "status": status_text or ("OK" if code == 200 else "Not Found"),
            "event": event, "delivered_at": f"2026-09-28T10:{i:02d}:00Z"}


class _Client:
    def __init__(self, *, refuse=None):
        self.calls = []
        self.refuse = refuse or {}

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        if name in self.refuse:
            return {"error": self.refuse[name]}
        return {"ok": True, "id": "hr-9"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


@pytest.fixture
def github(monkeypatch):
    box = {"deliveries": [], "raise": None}

    def _fetch():
        if box["raise"]:
            raise box["raise"]
        return list(box["deliveries"])

    monkeypatch.setattr(ingress, "_fetch_deliveries", _fetch)
    monkeypatch.setattr(ingress, "_fetch_hook_url", lambda: HOOK)
    monkeypatch.delenv("BOT_PORT", raising=False)
    return box


# ── assess: pure ─────────────────────────────────────────────────────────────

def test_assess_empty():
    assert ingress.assess([]) == {"status": "empty", "window": 0}


def test_assess_newest_ok_is_ok_even_with_older_failures():
    v = ingress.assess([_d(1, 404), _d(2, 404), _d(3, 404), _d(4, 200)])
    assert v["status"] == "ok"
    assert v["failing_streak"] == 0 and v["failed_in_window"] == 3
    assert v["by_status_code"] == {"200": 1, "404": 3}


def test_assess_streak_of_three_non_2xx_is_failing_in_any_order():
    v = ingress.assess([_d(4, 404), _d(1, 200), _d(3, 404), _d(2, 404)])
    assert v["status"] == "failing"
    assert v["failing_streak"] == 3
    assert v["latest"]["id"] == 4 and v["last_success"]["id"] == 1


def test_assess_two_failures_after_a_success_is_not_yet_failing():
    assert ingress.assess([_d(1, 200), _d(2, 404), _d(3, 404)])["status"] == "ok"


def test_assess_no_answer_counts_as_failure():
    v = ingress.assess([_d(1, 0, "Invalid HTTP Response: timeout"), _d(2, 0, "timeout"), _d(3, 502, "Bad")])
    assert v["status"] == "failing"


def test_assess_fewer_deliveries_than_the_streak_all_failing_is_failing():
    assert ingress.assess([_d(1, 404)])["status"] == "failing"


# ── run: flag once per episode, resolve on recovery ─────────────────────────

def test_failing_files_one_flag_naming_the_status_and_url(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["event"] == "steward_ingress" and r["status"] == "failing"
    assert r["flag"] == {"state": "filed", "id": "hr-9"}
    [item] = c.named("human_required_enqueue")
    assert item["app_id"] == "willow-bot" and item["priority"] == "high"
    assert item["title"] == "Webhook ingress failing: last 3 deliveries answered 404 Not Found"
    assert HOOK in item["summary"] and item["source_ref"] == HOOK
    assert "127.0.0.1:9000/webhook" in item["summary"]

    r2 = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert len(c.named("human_required_enqueue")) == 1  # not re-filed
    assert r2["flag"]["state"] == "open" and r2["flag"]["id"] == "hr-9"


def test_recovery_resolves_the_flag_once(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["deliveries"].append(_d(4, 200))
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "ok" and r["flag"] == {"state": "resolved", "id": "hr-9"}
    [res] = c.named("human_required_resolve")
    assert res["item_id"] == "hr-9" and res["status"] == "resolved"
    r2 = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert "flag" not in r2 and len(c.named("human_required_resolve")) == 1


def test_refused_filing_is_retried_next_tick(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    c = _Client(refuse={"human_required_enqueue": "gate denied"})
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"] == {"state": "could-not-file", "error": "gate denied"}
    c.refuse = {}
    r2 = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r2["flag"]["state"] == "filed"


def test_refused_resolve_keeps_the_flag_open(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["deliveries"].append(_d(4, 200))
    c.refuse = {"human_required_resolve": "upstream 502"}
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"]["state"] == "resolve-refused"
    c.refuse = {}
    assert ingress.run(enable_mcp=True, call=c, app="willow-bot")["flag"]["state"] == "resolved"


def test_mcp_off_reports_failing_unfiled(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    r = ingress.run(enable_mcp=False, call=None, app="willow-bot")
    assert r["status"] == "failing" and r["flag"]["state"] == "unfiled"


def test_unreachable_is_its_own_state_and_keeps_an_open_flag(github):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["raise"] = RuntimeError("GitHub App not configured")
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "unreachable" and "not configured" in r["detail"]
    assert r["flag"]["state"] == "open"
    assert c.named("human_required_resolve") == []


def test_empty_record_files_nothing(github):
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "empty" and "flag" not in r and c.calls == []


def test_hook_url_unreadable_still_files(github, monkeypatch):
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]

    def _boom():
        raise RuntimeError("403")

    monkeypatch.setattr(ingress, "_fetch_hook_url", _boom)
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"]["state"] == "filed"
    assert "(hook URL could not be read)" in c.named("human_required_enqueue")[0]["summary"]


# ── the tick step and the status surface ─────────────────────────────────────

def test_run_ingress_emits_a_tick_receipt(github, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "0")
    github["deliveries"] = [_d(1, 200)]
    r = tick.run_ingress()
    assert r["status"] == "ok"
    rows = [json.loads(ln) for ln in tick._receipts_path().read_text().splitlines()]
    assert rows[-1]["event"] == "steward_ingress" and rows[-1]["status"] == "ok"


def test_status_three_states(github):
    assert status.report()["ingress"]["status"] == "empty"
    github["deliveries"] = [_d(1, 404), _d(2, 404), _d(3, 404)]
    ingress.run(enable_mcp=False, call=None, app="willow-bot")
    got = status.report()["ingress"]
    assert got["status"] == "populated" and got["verdict"] == "failing"
    ingress.path().write_text("{not json", encoding="utf-8")
    assert status.report()["ingress"]["status"] == "unreachable"


def test_list_hook_deliveries_uses_the_app_jwt_not_an_install_token(monkeypatch):
    import github_app

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [_d(1, 200), "junk"]

    class _Req:
        def get(self, url, headers=None, params=None, timeout=None):  # noqa: ARG002
            seen.update(url=url, auth=headers["Authorization"], params=params)
            return _Resp()

    monkeypatch.setattr(github_app, "_configured", lambda: True)
    monkeypatch.setattr(github_app, "_make_jwt", lambda: "APPJWT")
    monkeypatch.setattr(github_app, "_auth_headers", lambda repo: pytest.fail("install token used"))
    monkeypatch.setattr(github_app, "requests", _Req())
    assert github_app.list_hook_deliveries(per_page=30) == [_d(1, 200)]
    assert seen == {"url": "https://api.github.com/app/hook/deliveries",
                    "auth": "Bearer APPJWT", "params": {"per_page": 30}}
