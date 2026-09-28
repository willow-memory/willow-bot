"""Is GitHub's App webhook reaching the bot? (desk, 2026-09-28)

Between 16:59 and 17:02Z, GitHub POSTs to /webhook landed on willow-mcp
serve at :8768 and answered 404 while the bot listened on :9000. The
steward now reads GitHub's own delivery record each tick. GitHub
(`ingress._fetch_*`) and the MCP client are fakes here; nothing reaches the
network. Status texts are GitHub's own ("OK", "Invalid HTTP Response: 404").
"""
from __future__ import annotations

import json

import pytest

from willow_bot import status
from willow_bot.steward import ingress, tick

HOOK = "https://hooks.example.invalid/webhook"
NOW = 1790000000.0  # 2026-09-21T..Z; deliveries below are dated relative to it


def _d(i, code, text=None, event="pull_request", at=None):
    if text is None:
        text = "OK" if code == 200 else (f"Invalid HTTP Response: {code}" if code else "timed out")
    return {"id": i, "status_code": code, "status": text, "event": event,
            "delivered_at": at or f"2026-09-28T10:{i:02d}:00Z"}


class _Client:
    def __init__(self, *, refuse=None, answer=None):
        self.calls = []
        self.refuse = refuse or {}
        self.answer = answer

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        if name in self.refuse:
            return {"error": self.refuse[name]}
        if self.answer is not None:
            return self.answer
        return {"ok": True, "id": "hr-9"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


@pytest.fixture
def github(monkeypatch):
    box = {"deliveries": [], "raise": None, "url": HOOK}

    def _fetch():
        if box["raise"]:
            raise box["raise"]
        return list(box["deliveries"])

    monkeypatch.setattr(ingress, "_fetch_deliveries", _fetch)
    monkeypatch.setattr(ingress, "_fetch_hook_url", lambda: box["url"])
    monkeypatch.delenv("BOT_PORT", raising=False)
    return box


FAILING = [_d(1, 404), _d(2, 404), _d(3, 404)]


# ── assess: pure ─────────────────────────────────────────────────────────────

def test_assess_empty():
    assert ingress.assess([]) == {"status": "empty", "window": 0}


def test_assess_newest_2xx_is_ok_with_older_failures_still_counted():
    v = ingress.assess([_d(1, 404), _d(2, 404), _d(3, 404), _d(4, 200)])
    assert v["status"] == "ok"
    assert v["failing_streak"] == 0 and v["failed_in_window"] == 3
    assert v["by_status_code"] == {"200": 1, "404": 3}


def test_assess_newest_not_2xx_but_short_streak_is_degraded_not_ok():
    """Loki 7E5306B6 F2: never "ok" while the newest delivery is a 404."""
    v = ingress.assess([_d(1, 200), _d(2, 404), _d(3, 404)])
    assert v["status"] == "degraded" and v["failing_streak"] == 2


def test_assess_streak_of_answered_non_2xx_is_failing_in_any_order():
    v = ingress.assess([_d(4, 404), _d(1, 200), _d(3, 404), _d(2, 404)])
    assert v["status"] == "failing" and v["failing_streak"] == 3
    assert v["latest"]["id"] == 4 and v["last_success"]["id"] == 1


def test_assess_streak_with_no_http_answer_is_unanswered():
    """Loki 7E5306B6 F1: arrived-and-failed and never-arrived are different."""
    assert ingress.assess([_d(1, 0), _d(2, 0), _d(3, 0)])["status"] == "unanswered"


def test_assess_mixed_streak_with_any_http_answer_is_failing():
    assert ingress.assess([_d(1, 0), _d(2, 502), _d(3, 0)])["status"] == "failing"


def test_assess_a_redirect_is_a_failure():
    """M01: GitHub does not follow redirects; only 2xx succeeds."""
    v = ingress.assess([_d(1, 302, "Invalid HTTP Response: 302"), _d(2, 302), _d(3, 302)])
    assert v["status"] == "failing"
    assert ingress.assess([_d(1, 200), _d(2, 302)])["status"] == "degraded"


def test_assess_fewer_deliveries_than_the_streak_all_failing_is_failing():
    assert ingress.assess([_d(1, 404)])["status"] == "failing"


def test_assess_carries_the_age_of_the_newest_delivery():
    """F6: a month-old 2xx must not read as a live ok."""
    v = ingress.assess([_d(1, 200, at="2026-08-28T10:00:00Z")], now=NOW)
    assert v["status"] == "ok"
    assert v["latest_age_s"] == int(NOW) - 1787911200


def test_assess_a_non_integer_id_is_malformed_not_a_raise():
    """F7: GitHub answered; a shape surprise is not "unreachable"."""
    v = ingress.assess([_d(1, 200), {**_d(2, 404), "id": "abc"}])
    assert v["status"] == "malformed"


# ── the flag: failing / unanswered file once; ok resolves ────────────────────

def test_failing_files_one_flag_naming_the_app_webhook_status_and_url(github):
    github["deliveries"] = list(FAILING)
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["event"] == "steward_ingress" and r["status"] == "failing" and r["hook"] == "app"
    assert r["flag"] == {"state": "filed", "id": "hr-9"}
    [item] = c.named("human_required_enqueue")
    assert item["app_id"] == "willow-bot" and item["priority"] == "high"
    assert item["title"] == "App webhook failing: last 3 deliveries answered HTTP 404"
    assert HOOK in item["summary"] and item["source_ref"] == HOOK
    assert "127.0.0.1:9000/webhook" in item["summary"]
    assert "App webhook only" in item["summary"]

    r2 = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert len(c.named("human_required_enqueue")) == 1  # not re-filed
    assert r2["flag"]["state"] == "open" and r2["flag"]["id"] == "hr-9"


def test_unanswered_files_its_own_title(github):
    github["deliveries"] = [_d(1, 0), _d(2, 0), _d(3, 0)]
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "unanswered" and r["flag"]["state"] == "filed"
    assert c.named("human_required_enqueue")[0]["title"] == (
        "App webhook unanswered: last 3 deliveries got no HTTP answer (timed out)")


def test_degraded_neither_files_nor_resolves(github):
    github["deliveries"] = list(FAILING)
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["deliveries"] = [_d(4, 200), _d(5, 404)]
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "degraded" and r["flag"]["state"] == "open"
    assert len(c.named("human_required_enqueue")) == 1 and c.named("human_required_resolve") == []


def test_recovery_resolves_the_flag_once(github):
    github["deliveries"] = list(FAILING)
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["deliveries"] = FAILING + [_d(4, 200)]
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["status"] == "ok" and r["flag"] == {"state": "resolved", "id": "hr-9"}
    [res] = c.named("human_required_resolve")
    assert res["item_id"] == "hr-9" and res["status"] == "resolved"
    r2 = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert "flag" not in r2 and len(c.named("human_required_resolve")) == 1


def test_refused_filing_is_retried_next_tick(github):
    github["deliveries"] = list(FAILING)
    c = _Client(refuse={"human_required_enqueue": "gate denied"})
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"] == {"state": "could-not-file", "error": "gate denied"}
    c.refuse = {}
    assert ingress.run(enable_mcp=True, call=c, app="willow-bot")["flag"]["state"] == "filed"


def test_filing_that_returns_no_id_is_not_filed(github):
    """F8: a placeholder id would never resolve; retry instead."""
    github["deliveries"] = list(FAILING)
    c = _Client(answer={"ok": True})
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"] == {"state": "could-not-file", "error": "enqueue returned no item id"}
    assert ingress.load()["flag"] is None
    c.answer = None
    assert ingress.run(enable_mcp=True, call=c, app="willow-bot")["flag"]["state"] == "filed"


def test_refused_resolve_keeps_the_flag_open(github):
    github["deliveries"] = list(FAILING)
    c = _Client()
    ingress.run(enable_mcp=True, call=c, app="willow-bot")
    github["deliveries"] = FAILING + [_d(4, 200)]
    c.refuse = {"human_required_resolve": "upstream 502"}
    assert ingress.run(enable_mcp=True, call=c, app="willow-bot")["flag"]["state"] == "resolve-refused"
    c.refuse = {}
    assert ingress.run(enable_mcp=True, call=c, app="willow-bot")["flag"]["state"] == "resolved"


def test_mcp_off_reports_failing_unfiled(github):
    github["deliveries"] = list(FAILING)
    r = ingress.run(enable_mcp=False, call=None, app="willow-bot")
    assert r["status"] == "failing" and r["flag"]["state"] == "unfiled"


def test_unreachable_is_its_own_state_and_keeps_an_open_flag(github):
    github["deliveries"] = list(FAILING)
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
    github["deliveries"] = list(FAILING)

    def _boom():
        raise RuntimeError("403")

    monkeypatch.setattr(ingress, "_fetch_hook_url", _boom)
    c = _Client()
    r = ingress.run(enable_mcp=True, call=c, app="willow-bot")
    assert r["flag"]["state"] == "filed"
    assert "(hook URL could not be read)" in c.named("human_required_enqueue")[0]["summary"]


# ── secrets never leave whole (Loki 7E5306B6 F3) ─────────────────────────────

SECRET_URL = "https://hookuser:hookpass@hooks.example.invalid:8443/webhook?token=QUERYSECRET#frag"


def test_redact_url_keeps_scheme_host_port_path_only():
    assert ingress.redact_url(SECRET_URL) == "https://hooks.example.invalid:8443/webhook"
    assert ingress.redact_url("") == "" and ingress.redact_url("not a url") == ""


def test_a_credentialed_hook_url_reaches_none_of_the_four_places(github, capsys):
    github["deliveries"] = list(FAILING)
    github["url"] = SECRET_URL
    c = _Client()
    r = tick._emit(ingress.run(enable_mcp=True, call=c, app="willow-bot"))
    places = {
        "stdout+receipt": capsys.readouterr().out + json.dumps(r),
        "ingress.json": ingress.path().read_text(encoding="utf-8"),
        "status": json.dumps(status.report()["ingress"]),
        "human_required": json.dumps(c.named("human_required_enqueue")),
    }
    for where, text in places.items():
        assert "hooks.example.invalid:8443/webhook" in text, where
        for secret in ("hookuser", "hookpass", "QUERYSECRET", "frag"):
            assert secret not in text, (where, secret)


def test_hook_url_returns_only_the_url_never_the_secret(monkeypatch):
    import github_app

    monkeypatch.setattr(github_app, "_app_get", lambda path, **kw: {
        "url": HOOK, "content_type": "json", "secret": "WEBHOOKSECRET", "insecure_ssl": "0"})
    assert github_app.hook_url() == HOOK


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


# ── the tick step, the loop, the CLI and the status surface ──────────────────

def test_run_ingress_emits_a_tick_receipt(github, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "0")
    github["deliveries"] = [_d(1, 200)]
    r = tick.run_ingress()
    assert r["status"] == "ok"
    rows = [json.loads(ln) for ln in tick._receipts_path().read_text().splitlines()]
    assert rows[-1]["event"] == "steward_ingress" and rows[-1]["status"] == "ok"


def test_the_loop_runs_ingress_every_tick(monkeypatch):
    """M19."""
    order: list[str] = []
    for name in ("run_once", "run_resolve", "run_install_receipts", "run_mirror", "run_ci",
                 "run_ci_legacy_clear", "run_catchup", "run_audit"):
        monkeypatch.setattr(tick, name, lambda *_a, _n=name, **_k: order.append(_n))
    monkeypatch.setattr(tick, "run_sweep", lambda *_a, **_k: {"ranges": []})
    monkeypatch.setattr(tick, "run_ingress", lambda *_a, **_k: order.append("run_ingress"))
    monkeypatch.setattr("willow_bot.steward.heartbeat.run_heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr("willow_bot.steward.voice.run_voice", lambda *_a, **_k: order.append("run_voice"))

    class _Stop(Exception):
        pass

    def sleep_once(_):
        if "run_voice" in order:
            raise _Stop()

    monkeypatch.setattr(tick.time, "sleep", sleep_once)
    with pytest.raises(_Stop):
        tick.run_loop(0.0)
    assert order.count("run_ingress") == 1
    assert order.index("run_audit") < order.index("run_ingress") < order.index("run_voice")


def test_the_cli_knows_ingress(monkeypatch):
    """M20."""
    ran = []
    monkeypatch.setattr(tick, "run_ingress", lambda *_a, **_k: ran.append(1))
    assert tick.main(["ingress"]) == 0 and ran == [1]


def test_status_states(github):
    assert status.report()["ingress"]["status"] == "empty"
    github["deliveries"] = list(FAILING)
    ingress.run(enable_mcp=False, call=None, app="willow-bot")
    got = status.report()["ingress"]
    assert got["status"] == "populated" and got["verdict"] == "failing" and got["hook"] == "App webhook"
    assert "latest_age_s" in got
    ingress.path().write_text("{not json", encoding="utf-8")
    assert status.report()["ingress"]["status"] == "unreachable"


def test_status_file_without_a_verdict_is_unreachable(github):
    """M18: a file present but carrying no last verdict is not "populated"."""
    ingress.path().parent.mkdir(parents=True, exist_ok=True)
    ingress.path().write_text(json.dumps({"flag": None}), encoding="utf-8")
    got = status.report()["ingress"]
    assert got["status"] == "unreachable" and "no last verdict" in got["detail"]
