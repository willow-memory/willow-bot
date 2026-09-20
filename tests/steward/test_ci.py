"""The ci step (2026-09-14): a red check reaches a seat.

Gap 8d1bcb2b7c02 — the bot recorded two reds tonight and reported them to
nobody; the operator told the seat. This step reads the bot's own deposits
from an offset and files red heads as human_required review items.

Gap 25cb3c1a3489 (2026-09-20) — three asks on top: `cancelled` is its own
state (superseded / waiting / stuck / unreachable), one item per (repo, pr,
head_sha) with the aggregate `test` job folded in, and resolve-on-green.
The deposits file is real; the MCP client is a fake.
"""
from __future__ import annotations

import json
import time

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
    monkeypatch.delenv(tick._CI_CANCELLED_GRACE_ENV, raising=False)
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

    def named(self, name):
        return [i for n, i in self.calls if n == name]


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


def _row(repo, sha, cid, name, conclusion, pr=76, received_at=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid,
                                     check_name=name, conclusion=conclusion, received_at=received_at)
    rec["pr_number"] = pr
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


SHA = "34542ff3a05131c265a04bf96823b217da6f891b"
SHA2 = "9e1d0c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d"
GROVE = "willow-memory/willows-grove"


def _seed():
    # The step's first run starts at EOF (test below). These tests are about
    # what happens AFTER the offset exists, so establish it before seeding —
    # exactly what a box looks like once the step has ticked once.
    deposits.append_local(_row("willow-memory/willow-bot", "0" * 40, 0, "older", "failure", pr=1))
    first = tick.run_ci(enable_mcp=False)
    assert first["first_run_skipped_bytes"] > 0 and first["red"] == []
    deposits.append_local(_row(GROVE, SHA, 1, "title", "failure"))
    deposits.append_local(_row(GROVE, SHA, 2, "CodeQL", "neutral"))
    deposits.append_local(_row(GROVE, SHA, 3, "test-suite (3.13)", "failure"))
    deposits.append_local(_row("willow-memory/willow-bot", "b" * 40, 4, "test-matrix (3.12)", "success", pr=7))
    deposits.append_local(_row(GROVE, SHA, 5, "test-windows (3.11)", "cancelled"))


def _later(minutes: float):
    base = time.time()
    return lambda: base + minutes * 60


# ── the basics, carried over ─────────────────────────────────────────────────

def test_no_deposits_file_is_an_honest_ok(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "ok" and r["present"] is False
    assert r["red"] == [] and r["cancelled"] == [] and r["resolved"] == []
    assert c.calls == []


def test_reds_are_reported_even_when_mcp_is_off(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "absent"
    assert [x["check"] for x in r["red"]] == ["title", "test-suite (3.13)"]
    assert [(x["leg"], x["state"]) for x in r["cancelled"]] == [("test-windows (3.11)", "waiting")]
    assert c.calls == [] and r["filed"] == []
    # the offset advanced: the seat has read these; a later MCP-on tick does not re-report them
    r2 = tick.run_ci()
    assert r2["red"] == []


def test_first_run_starts_at_eof_and_files_nothing_old(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    for i in range(3):
        deposits.append_local(_row("forge-play/Forge", "d" * 40, 100 + i, "Tests", "failure", pr=4))
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "ok" and r["red"] == [] and r["filed"] == []
    assert r["first_run_skipped_bytes"] == r["size"] > 0 and r["offset"] == r["size"]
    assert c.calls == []
    deposits.append_local(_row("forge-play/Forge", "e" * 40, 200, "Tests", "failure", pr=5))
    r2 = tick.run_ci()
    assert [f["where"] for f in r2["filed"]] == ["forge-play/Forge#5"]
    assert "first_run_skipped_bytes" not in r2


def test_a_red_with_no_pr_names_the_sha(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", "c" * 40, 9, "release-please", "failure", pr=None))
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["filed"][-1]["where"] == "willow-memory/willow-mcp@" + "c" * 12


def test_ci_step_runs_in_the_loop_after_mirror_before_audit():
    import inspect

    src = inspect.getsource(tick.run_loop)
    assert src.index('("mirror", run_mirror)') < src.index('("ci", run_ci)') < src.index('("audit", run_audit)')


# ── ask 2: one filing per (repo, pr, head_sha) ───────────────────────────────

def test_one_item_per_head_naming_every_red_leg(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "ok"
    enq = c.named("human_required_enqueue")
    assert len(enq) == 1, [i["title"] for i in enq]
    i = enq[0]
    assert i["title"] == f"CI red: {GROVE}#76 — 2 leg(s): title, test-suite (3.13)"
    assert i["app_id"] == "willow" and i["kind"] == "review"
    assert i["summary"].startswith(f"head {SHA}\n")
    assert "job/1" in i["summary"] and "job/3" in i["summary"]
    assert i["source_ref"].endswith("/job/1")
    # the cancelled leg is not in the item and not filed
    assert "test-windows" not in i["summary"]
    assert r["filed"] == [{"where": f"{GROVE}#76", "head_sha": SHA, "legs": ["title", "test-suite (3.13)"],
                           "conclusions": ["failure", "failure"], "id": "hr-1"}]
    # both legs map to the one item, under the key shape voice reads by sha prefix
    state = json.loads(state_path().read_text())
    assert state["ci_filed"] == {f"{SHA}:1": "hr-1", f"{SHA}:3": "hr-1"}
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["id"] == "hr-1"
    # second tick: nothing new, nothing re-filed
    r2 = tick.run_ci()
    assert r2["red"] == [] and r2["filed"] == [] and len(c.calls) == 1


def test_a_redelivered_completion_is_not_filed_twice(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA, 1, "title", "failure"))
    r = tick.run_ci()
    assert len(r["red"]) == 1 and r["filed"] == [] and r["appended"] == [] and r["skipped"] == 1
    assert len(c.calls) == 1


def test_a_new_leg_on_a_filed_head_joins_its_item(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA, 6, "fleet-seams", "failure"))
    r = tick.run_ci()
    assert r["filed"] == [] and r["appended"] == [{"where": f"{GROVE}#76", "check": "fleet-seams", "id": "hr-1"}]
    assert len(c.named("human_required_enqueue")) == 1
    state = json.loads(state_path().read_text())
    assert state["ci_filed"][f"{SHA}:6"] == "hr-1"
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["legs"] == ["title", "test-suite (3.13)", "fleet-seams"]


def test_the_aggregate_test_job_is_folded_into_its_cause(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 20, "test-matrix (3.11)", "failure", pr=550))
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 21, "test", "failure", pr=550))
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    mcp_items = [i for i in c.named("human_required_enqueue") if "#550" in i["title"]]
    assert len(mcp_items) == 1
    assert mcp_items[0]["title"] == "CI red: willow-memory/willow-mcp#550 — 1 leg(s): test-matrix (3.11)"
    assert "test concluded failure (aggregate)" in mcp_items[0]["summary"]
    assert mcp_items[0]["source_ref"].endswith("/job/20")


def test_a_refusal_holds_the_offset_and_retries_only_the_unfiled(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 20, "lint", "failure", pr=550))

    def answer(name, inputs, n):
        if n == 2:
            return {"error": "rate_limited", "retry_after": 3}
        return {"ok": True, "id": f"hr-{n}"}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "could-not-run"
    assert [f["where"] for f in r["filed"]] == [f"{GROVE}#76"]
    assert r["refused"] == [{"where": "willow-memory/willow-mcp#550", "legs": ["lint"], "error": "rate_limited"}]
    assert r["new_offset"] == r["offset"]  # held where the refused row can be found again
    r2 = tick.run_ci()
    assert r2["status"] == "ok"
    assert [f["where"] for f in r2["filed"]] == ["willow-memory/willow-mcp#550"]
    assert len(c.named("human_required_enqueue")) == 3


# ── ask 1: cancelled is its own state ────────────────────────────────────────

def test_cancelled_waits_inside_the_grace_window(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["cancelled"] == [{"repo": GROVE, "pr": 76, "head_sha": SHA, "leg": "test-windows (3.11)",
                               "state": "waiting"}]
    assert "cancelled" not in json.dumps([i["title"] for i in c.named("human_required_enqueue")])
    # still pending next tick; decided again, not re-read
    r2 = tick.run_ci()
    assert [x["state"] for x in r2["cancelled"]] == ["waiting"]
    state = json.loads(state_path().read_text())
    assert list(state["ci_cancelled_pending"]) == [f"{SHA}:5"]


def test_cancelled_is_superseded_when_a_later_head_appears(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    # the next push: a new head for #76 — the cancelled run was superseded
    deposits.append_local(_row(GROVE, SHA2, 7, "title", "success",
                               received_at=_iso(time.time() + 30)))
    r = tick.run_ci()
    assert r["cancelled"] == [{"repo": GROVE, "pr": 76, "head_sha": SHA, "leg": "test-windows (3.11)",
                               "state": "superseded", "successor": SHA2}]
    assert len(c.named("human_required_enqueue")) == 1  # the red item only
    state = json.loads(state_path().read_text())
    assert state["ci_cancelled_pending"] == {}
    # gone for good: the next tick does not mention it
    assert tick.run_ci()["cancelled"] == []


def test_cancelled_with_no_successor_after_grace_is_stuck_and_filed(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "5")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(6))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["stuck"]
    # folded into the SAME item as the head's reds — no second item
    assert r["filed"] == [] and r["appended"] == [{"where": f"{GROVE}#76", "check": "test-windows (3.11)",
                                                   "id": "hr-1"}]
    state = json.loads(state_path().read_text())
    assert state["ci_filed"][f"{SHA}:5"] == "hr-1" and state["ci_cancelled_pending"] == {}


def test_stuck_cancelled_on_an_unfiled_head_is_its_own_item(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 30, "test-matrix (3.13)", "cancelled", pr=551))
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    stuck = [f for f in r["filed"] if f["where"] == "willow-memory/willow-mcp#551"]
    assert stuck == [{"where": "willow-memory/willow-mcp#551", "head_sha": SHA2, "legs": ["test-matrix (3.13)"],
                      "conclusions": ["cancelled"], "id": "hr-2"}]


def test_cancelled_with_unreadable_timestamp_is_unreachable_not_filed(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 40, "lint", "cancelled", pr=552,
                               received_at="not a time"))
    c = _Client()
    _use(monkeypatch, c)
    monkeypatch.setattr(tick, "_ci_clock", _later(60))
    r = tick.run_ci()
    states = {x["leg"]: x["state"] for x in r["cancelled"]}
    assert states["lint"] == "unreachable" and states["test-windows (3.11)"] == "stuck"
    assert not any("#552" in i["title"] for i in c.named("human_required_enqueue"))


def test_grace_env_overrides_the_default(home, monkeypatch):
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "2.5")
    assert tick._ci_grace_s() == 150.0
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "junk")
    assert tick._ci_grace_s() == tick._CI_CANCELLED_GRACE_MIN_DEFAULT * 60.0


# ── ask 3: resolve-on-green ──────────────────────────────────────────────────

def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _green_head(repo, sha, pr, *, at):
    for cid, name in ((51, "lint"), (52, "test-matrix (3.11)"), (53, "CodeQL")):
        deposits.append_local(_row(repo, sha, cid, name, "success" if name != "CodeQL" else "neutral",
                                   pr=pr, received_at=_iso(at)))


def test_a_later_green_head_resolves_the_prs_older_items(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    first = tick.run_ci()
    assert first["resolved"] == []
    _green_head(GROVE, SHA2, 76, at=time.time() + 60)
    r = tick.run_ci()
    assert r["resolved"] == [{"where": f"{GROVE}#76@{SHA}", "item_id": "hr-1", "superseded_by": SHA2}]
    res = c.named("human_required_resolve")
    assert len(res) == 1
    assert res[0]["item_id"] == "hr-1" and res[0]["status"] == "resolved"
    assert res[0]["note"].startswith(f"superseded by {SHA2}, green at ")
    state = json.loads(state_path().read_text())
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["resolved"]["by"] == SHA2
    # idempotent: a third tick resolves nothing again
    assert tick.run_ci()["resolved"] == [] and len(c.named("human_required_resolve")) == 1


def test_a_later_head_that_is_not_fully_green_resolves_nothing(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    at = time.time() + 60
    deposits.append_local(_row(GROVE, SHA2, 61, "lint", "success", received_at=_iso(at)))
    deposits.append_local(_row(GROVE, SHA2, 62, "test-matrix (3.11)", "failure", received_at=_iso(at)))
    r = tick.run_ci()
    assert r["resolved"] == [] and c.named("human_required_resolve") == []
    # the new head's red is its own item
    assert [f["head_sha"] for f in r["filed"]] == [SHA2]


def test_an_earlier_green_head_does_not_resolve_a_later_red(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    _green_head(GROVE, SHA2, 76, at=time.time() - 3600)  # older than the red head
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["resolved"] == [] and c.named("human_required_resolve") == []


def test_a_refused_resolve_is_reported_and_retried(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()

    def answer(name, inputs, n):
        if name == "human_required_resolve" and n == 2:
            return {"error": "unknown_item"}
        return {"ok": True, "id": f"hr-{n}"}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    tick.run_ci()
    _green_head(GROVE, SHA2, 76, at=time.time() + 60)
    r = tick.run_ci()
    assert r["resolved"] == [] and r["resolve_refused"][0]["error"] == "unknown_item"
    assert r["status"] == "ok"  # a refused resolve does not hold the offset
    r2 = tick.run_ci()
    assert [x["item_id"] for x in r2["resolved"]] == ["hr-1"]


def test_resolve_on_green_is_reported_not_done_when_mcp_is_off(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    monkeypatch.delenv("WILLOW_BOT_MCP")
    _green_head(GROVE, SHA2, 76, at=time.time() + 60)
    r = tick.run_ci()
    assert r["status"] == "absent"
    assert r["would_resolve"] == [{"where": f"{GROVE}#76@{SHA}", "superseded_by": SHA2}]
    assert c.named("human_required_resolve") == []
