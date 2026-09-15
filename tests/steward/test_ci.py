"""The ci step (2026-09-14): a red check reaches a seat.

Gap 8d1bcb2b7c02 — the bot recorded two reds tonight and reported them to
nobody; the operator told the seat. This step reads the bot's own deposits
from an offset and files each red leg as a human_required review item.
The deposits file is real; the MCP client is a fake.
"""
from __future__ import annotations

import json

import pytest

from willow_bot import deposits
from willow_bot.steward import tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    return tmp_path


class _Client:
    def __init__(self, *, result=None):
        self.calls = []
        self.result = result
        self.n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if callable(self.result):
            return self.result(name, inputs, self.n)
        return self.result if self.result is not None else {"ok": True, "id": f"hr-{self.n}"}


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


def _row(repo, sha, cid, name, conclusion, pr=76):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid,
                                     check_name=name, conclusion=conclusion)
    rec["pr_number"] = pr
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


SHA = "34542ff3a05131c265a04bf96823b217da6f891b"


def _seed():
    deposits.append_local(_row("willow-memory/willows-grove", SHA, 1, "title", "failure"))
    deposits.append_local(_row("willow-memory/willows-grove", SHA, 2, "CodeQL", "neutral"))
    deposits.append_local(_row("willow-memory/willows-grove", SHA, 3, "test-suite (3.13)", "failure"))
    deposits.append_local(_row("willow-memory/willow-bot", "b" * 40, 4, "test-matrix (3.12)", "success", pr=7))
    deposits.append_local(_row("willow-memory/willows-grove", SHA, 5, "test-windows (3.11)", "cancelled"))


def test_no_deposits_file_is_an_honest_ok(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "ok" and r["present"] is False and r["red"] == []
    assert c.calls == []


def test_reds_are_reported_even_when_mcp_is_off(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "absent"
    assert [x["check"] for x in r["red"]] == ["title", "test-suite (3.13)", "test-windows (3.11)"]
    assert c.calls == [] and r["filed"] == []
    # the offset advanced: the seat has read these; a later MCP-on tick does not re-report them
    r2 = tick.run_ci()
    assert r2["red"] == []


def test_each_red_leg_is_filed_once_with_pr_leg_and_url(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "ok"
    assert [n for n, _ in c.calls] == ["human_required_enqueue"] * 3
    titles = [i["title"] for _, i in c.calls]
    assert titles[0] == "CI red: willow-memory/willows-grove#76 — title failure"
    assert titles[2] == "CI red: willow-memory/willows-grove#76 — test-windows (3.11) cancelled"
    for _, i in c.calls:
        assert i["app_id"] == "willow" and i["kind"] == "review"
        assert i["source_ref"].startswith("https://github.com/")
        assert SHA in i["summary"]
    assert [f["id"] for f in r["filed"]] == ["hr-1", "hr-2", "hr-3"]
    # green and neutral never reach the tool
    assert not any("success" in i["title"] or "neutral" in i["title"] for _, i in c.calls)
    # second tick: nothing new, nothing re-filed
    r2 = tick.run_ci()
    assert r2["red"] == [] and r2["filed"] == [] and len(c.calls) == 3


def test_a_redelivered_completion_is_not_filed_twice(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    # GitHub redelivers the same check_run completion; the bot appends it again
    deposits.append_local(_row("willow-memory/willows-grove", SHA, 1, "title", "failure"))
    r = tick.run_ci()
    assert len(r["red"]) == 1 and r["filed"] == [] and r["skipped"] == 1
    assert len(c.calls) == 3
    state = json.loads(state_path().read_text())
    assert set(state["ci_filed"]) == {f"{SHA}:1", f"{SHA}:3", f"{SHA}:5"}


def test_a_refusal_holds_the_offset_and_retries_only_the_unfiled(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()

    def answer(name, inputs, n):
        if n == 2:
            return {"error": "rate_limited", "retry_after": 3}
        return {"ok": True, "id": f"hr-{n}"}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "could-not-run"
    assert [f["check"] for f in r["filed"]] == ["title"]
    assert r["refused"][0]["check"] == "test-suite (3.13)" and r["refused"][0]["error"] == "rate_limited"
    assert r["new_offset"] == 0  # held
    # next tick: title is remembered, the other two are filed
    r2 = tick.run_ci()
    assert r2["status"] == "ok"
    assert [f["check"] for f in r2["filed"]] == ["test-suite (3.13)", "test-windows (3.11)"]
    assert len(c.calls) == 4


def test_a_red_with_no_pr_names_the_sha(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    deposits.append_local(_row("willow-memory/willow-mcp", "c" * 40, 9, "release-please", "failure", pr=None))
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["filed"][0]["where"] == "willow-memory/willow-mcp@" + "c" * 12


def test_ci_step_runs_in_the_loop_after_mirror_before_audit():
    import inspect

    src = inspect.getsource(tick.run_loop)
    assert src.index('("mirror", run_mirror)') < src.index('("ci", run_ci)') < src.index('("audit", run_audit)')
