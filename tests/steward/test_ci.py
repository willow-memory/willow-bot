"""The ci step (2026-09-14): a red check reaches a seat.

Gap 8d1bcb2b7c02 — the bot recorded two reds tonight and reported them to
nobody; the operator told the seat. This step reads the bot's own deposits
from an offset and files red heads as human_required review items.

Gap 25cb3c1a3489 (2026-09-20) — three asks on top: `cancelled` is its own
state (superseded / waiting / stuck / unreachable), one item per (repo, pr,
head_sha) with the aggregate `test` job folded in, and resolve-on-green.
Loki's audit (82A7DB13) added: green means the earlier head's leg set has
reported, not the first green leg; a stuck cancelled leg is not a red;
a head re-run to green resolves; the state maps stay bounded; and the
one-time clear of items filed before this build runs inside the bot.
The deposits file is real; the MCP client is a fake.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

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
        # `hr-N` ids number the human_required_* calls specifically — the
        # CI-red comment/Grove line now run their own `grove_send_message`
        # calls through this same fake client, independently of and
        # interleaved with `human_required_enqueue`/`_resolve` (finding 2,
        # dispatch E026CFE7), so a plain "Nth call of any kind" counter
        # would renumber `hr-` ids out from under tests that never asked
        # about Grove at all.
        self._hr_n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if callable(self.result):
            return self.result(name, inputs, self.n)
        if self.result is not None:
            return self.result
        if name.startswith("human_required"):
            self._hr_n += 1
            return {"ok": True, "id": f"hr-{self._hr_n}"}
        return {"ok": True}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row(repo, sha, cid, name, conclusion, pr=76, received_at=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid,
                                     check_name=name, conclusion=conclusion, received_at=received_at)
    rec["pr_number"] = pr
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


SHA = "34542ff3a05131c265a04bf96823b217da6f891b"
SHA2 = "9e1d0c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d"
SHA3 = "1111111111111111111111111111111111111111"
GROVE = "willow-memory/willows-grove"
# The seed head's leg set — the expected set a later head must report on.
SEED_LEGS = ("title", "CodeQL", "test-suite (3.13)", "test-windows (3.11)")


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


def _green_head(repo, sha, pr, *, at, legs=SEED_LEGS, start_cid=50):
    """Every leg in `legs` green on `sha` — CodeQL neutral, the rest success."""
    for i, name in enumerate(legs):
        deposits.append_local(_row(repo, sha, start_cid + i, name, "neutral" if name == "CodeQL" else "success",
                                   pr=pr, received_at=_iso(at)))


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


def test_ci_steps_run_in_the_loop_after_mirror_before_audit():
    import inspect

    src = inspect.getsource(tick.run_loop)
    assert (src.index('("mirror", run_mirror)') < src.index('("ci", run_ci)')
            < src.index('("ci-legacy-clear", run_ci_legacy_clear)') < src.index('("audit", run_audit)'))


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
    assert "test-windows" not in i["summary"]
    assert r["filed"] == [{"where": f"{GROVE}#76", "head_sha": SHA, "legs": ["title", "test-suite (3.13)"],
                           "conclusions": ["failure", "failure"], "id": "hr-1"}]
    state = json.loads(state_path().read_text())
    assert state["ci_filed"] == {f"{SHA}:1": "hr-1", f"{SHA}:3": "hr-1"}
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["id"] == "hr-1"
    r2 = tick.run_ci()
    # `c.calls` also carries the steward's own independent CI-red Grove
    # line (`ci_comments`, unconditional per red head since dispatch
    # E026CFE7's re-audit) — count filings specifically, not every call.
    assert r2["red"] == [] and r2["filed"] == [] and len(c.named("human_required_enqueue")) == 1


def test_a_redelivered_completion_is_not_filed_twice(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA, 1, "title", "failure"))
    r = tick.run_ci()
    assert len(r["red"]) == 1 and r["filed"] == [] and r["appended"] == [] and r["skipped"] == 1
    assert len(c.named("human_required_enqueue")) == 1


def test_a_new_leg_on_a_filed_head_joins_its_item(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA, 6, "fleet-seams", "failure"))
    r = tick.run_ci()
    assert r["filed"] == [] and r["appended"] == [{"where": f"{GROVE}#76", "check": "fleet-seams",
                                                   "conclusion": "failure", "id": "hr-1"}]
    assert len(c.named("human_required_enqueue")) == 1
    state = json.loads(state_path().read_text())
    assert state["ci_filed"][f"{SHA}:6"] == "hr-1"
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["legs"] == ["title", "test-suite (3.13)", "fleet-seams"]


def test_a_rerun_of_a_red_leg_does_not_duplicate_its_name(home, monkeypatch):
    """Loki note: a re-run under a new check_run_id is the same leg."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA, 7, "title", "failure"))  # re-run, still red
    r = tick.run_ci()
    assert [a["check"] for a in r["appended"]] == ["title"]
    state = json.loads(state_path().read_text())
    assert state["ci_items"][f"{GROVE}#76@{SHA}"]["legs"] == ["title", "test-suite (3.13)"]
    assert state["ci_filed"][f"{SHA}:7"] == "hr-1"


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


def test_the_aggregate_alone_on_an_unfiled_head_is_still_filed(home, monkeypatch):
    """Loki note: when only the aggregate arrives (its cause leg's deposit
    lost or later), the head is still red — filed naming `test`."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 21, "test", "failure", pr=550))
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    mcp_items = [i for i in c.named("human_required_enqueue") if "#550" in i["title"]]
    assert mcp_items[0]["title"] == "CI red: willow-memory/willow-mcp#550 — 1 leg(s): test"
    assert "(aggregate)" not in mcp_items[0]["summary"]
    assert mcp_items[0]["source_ref"].endswith("/job/21")


def test_a_refusal_holds_the_offset_and_retries_only_the_unfiled(home, monkeypatch):
    """A refusal that is NOT the limiter (gap 52928edb3fc7 moved
    rate_limited to pacing — see the pacing tests below)."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 20, "lint", "failure", pr=550))

    denied = {"once": False}

    def answer(name, inputs, n):
        if (name == "human_required_enqueue" and "#550" in inputs.get("title", "")
                and not denied["once"]):
            denied["once"] = True
            return {"error": "denied: human_required_enqueue not in tools_allowed"}
        return {"ok": True, "id": f"hr-{n}"}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["status"] == "could-not-run"
    assert [f["where"] for f in r["filed"]] == [f"{GROVE}#76"]
    assert r["refused"] == [{"where": "willow-memory/willow-mcp#550", "legs": ["lint"],
                             "error": "denied: human_required_enqueue not in tools_allowed"}]
    assert r["new_offset"] == r["offset"]
    assert r["remaining"] == 1 and r["budget_spent"] is False and r["paced"] == 0
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
    r2 = tick.run_ci()
    assert [x["state"] for x in r2["cancelled"]] == ["waiting"]
    state = json.loads(state_path().read_text())
    assert list(state["ci_cancelled_pending"]) == [f"{SHA}:5"]
    assert isinstance(state["ci_cancelled_pending"][f"{SHA}:5"]["pending_since"], float)


def test_cancelled_is_superseded_when_a_later_head_appears(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    deposits.append_local(_row(GROVE, SHA2, 7, "title", "success", received_at=_iso(time.time() + 30)))
    r = tick.run_ci()
    assert r["cancelled"] == [{"repo": GROVE, "pr": 76, "head_sha": SHA, "leg": "test-windows (3.11)",
                               "state": "superseded", "successor": SHA2}]
    assert len(c.named("human_required_enqueue")) == 1
    state = json.loads(state_path().read_text())
    assert state["ci_cancelled_pending"] == {}
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
    assert r["filed"] == [] and r["appended"] == [{"where": f"{GROVE}#76", "check": "test-windows (3.11)",
                                                   "conclusion": "cancelled", "id": "hr-1"}]
    state = json.loads(state_path().read_text())
    assert state["ci_cancelled_pending"] == {}
    # Loki finding 2: a cancelled leg is remembered, but NOT as a red —
    # voice must not derive `ci-red` / "N red leg(s) filed" from it.
    assert f"{SHA}:5" not in state["ci_filed"]
    assert state["ci_filed_cancelled"][f"{SHA}:5"] == "hr-1"
    from willow_bot.steward import voice

    assert voice._ci_red_legs_for_sha(state, SHA) == [f"{SHA}:1", f"{SHA}:3"]
    r3 = tick.run_ci()
    assert r3["cancelled"] == [] and r3["appended"] == []


def test_stuck_cancelled_on_an_unfiled_head_is_its_own_item_and_not_a_red(home, monkeypatch):
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
    state = json.loads(state_path().read_text())
    from willow_bot.steward import voice

    assert voice._ci_red_legs_for_sha(state, SHA2) == []


def test_grace_boundary_at_equality_is_stuck(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "5")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    state = json.loads(state_path().read_text())
    received = tick._ci_epoch(state["ci_cancelled_pending"][f"{SHA}:5"]["received_at"])
    monkeypatch.setattr(tick, "_ci_clock", lambda: received + 300.0)
    assert [x["state"] for x in tick.run_ci()["cancelled"]] == ["stuck"]


def test_grace_boundary_just_under_is_waiting(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "5")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    state = json.loads(state_path().read_text())
    received = tick._ci_epoch(state["ci_cancelled_pending"][f"{SHA}:5"]["received_at"])
    monkeypatch.setattr(tick, "_ci_clock", lambda: received + 299.0)
    assert [x["state"] for x in tick.run_ci()["cancelled"]] == ["waiting"]


def test_cancelled_with_unreadable_timestamp_is_unreachable_not_filed(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 40, "lint", "cancelled", pr=552,
                               received_at="not a time"))
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    states = {x["leg"]: x["state"] for x in r["cancelled"]}
    assert states["lint"] == "unreachable" and states["test-windows (3.11)"] == "waiting"
    assert not any("#552" in i["title"] for i in c.named("human_required_enqueue"))


def test_unreachable_ages_by_first_sighting_and_becomes_stuck(home, monkeypatch):
    """Loki finding 4 (pending set unbounded): an unreadable timestamp is
    `unreachable` only until the grace window has passed since the step
    first saw it; then it is stuck by `pending_since`."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    deposits.append_local(_row("willow-memory/willow-mcp", SHA2, 40, "lint", "cancelled", pr=552,
                               received_at="not a time"))
    c = _Client()
    _use(monkeypatch, c)
    base = time.time()
    monkeypatch.setattr(tick, "_ci_clock", lambda: base)
    r = tick.run_ci()
    assert {x["leg"]: x["state"] for x in r["cancelled"]}["lint"] == "unreachable"
    monkeypatch.setattr(tick, "_ci_clock", lambda: base + 601)
    r2 = tick.run_ci()
    lint = [x for x in r2["cancelled"] if x["leg"] == "lint"][0]
    assert lint["state"] == "stuck" and lint["aged_by"] == "pending_since"
    assert any("#552" in i["title"] for i in c.named("human_required_enqueue"))
    assert json.loads(state_path().read_text())["ci_cancelled_pending"] == {}


def test_unreadable_timestamp_on_the_newest_head_is_not_superseded_by_an_older_head(home, monkeypatch):
    """Loki 18CE5C43: with `mine` unknown, any dated head used to count as
    a successor — an older one included — so a cancelled leg on the newest
    head with a bad clock was dropped. Now "later" is judged against the
    moment the step first saw the leg."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    base = time.time()
    # #552 has an older, dated head, then a newer head whose deposit clock is unreadable
    deposits.append_local(_row(MCP, SHA3, 41, "lint", "success", pr=552, received_at=_iso(base - 3600)))
    deposits.append_local(_row(MCP, SHA2, 40, "lint", "cancelled", pr=552, received_at="not a time"))
    c = _Client()
    _use(monkeypatch, c)
    monkeypatch.setattr(tick, "_ci_clock", lambda: base)
    r = tick.run_ci()
    assert {x["leg"]: x["state"] for x in r["cancelled"] if x["pr"] == 552} == {"lint": "unreachable"}
    # a head that genuinely arrives after the sighting IS a successor
    deposits.append_local(_row(MCP, "f" * 40, 42, "lint", "success", pr=552, received_at=_iso(base + 30)))
    r2 = tick.run_ci()
    lint = [x for x in r2["cancelled"] if x["pr"] == 552][0]
    assert lint["state"] == "superseded" and lint["successor"] == "f" * 40 and lint["aged_by"] == "pending_since"


def test_grace_env_overrides_the_default(home, monkeypatch):
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "2.5")
    assert tick._ci_grace_s() == 150.0
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "junk")
    assert tick._ci_grace_s() == tick._CI_CANCELLED_GRACE_MIN_DEFAULT * 60.0


# ── ask 3: resolve-on-green ──────────────────────────────────────────────────

def test_a_later_green_head_resolves_the_prs_older_items(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    first = tick.run_ci()
    assert first["resolved"] == []
    _green_head(GROVE, SHA2, 76, at=time.time() + 60)
    r = tick.run_ci()
    assert r["resolved"] == [{"where": f"{GROVE}#76@{SHA}", "item_id": "hr-1", "superseded_by": SHA2,
                              "how": "superseded"}]
    res = c.named("human_required_resolve")
    assert len(res) == 1
    assert res[0]["item_id"] == "hr-1" and res[0]["status"] == "resolved"
    assert res[0]["note"].startswith(f"superseded by {SHA2}, green at ")
    # Loki finding 4: a resolved item is dropped from state; its legs stay in ci_filed
    state = json.loads(state_path().read_text())
    assert f"{GROVE}#76@{SHA}" not in state["ci_items"]
    assert state["ci_filed"][f"{SHA}:1"] == "hr-1"
    assert tick.run_ci()["resolved"] == [] and len(c.named("human_required_resolve")) == 1


def test_a_single_early_green_leg_does_not_resolve(home, monkeypatch):
    """Loki finding 1: deposits arrive one per webhook; `{lint: success}`
    alone is an early head, not a green one. The item's own leg set is the
    expected set — only when every one has reported green does it resolve."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    at = time.time() + 60
    deposits.append_local(_row(GROVE, SHA2, 50, "title", "success", received_at=_iso(at)))
    assert tick.run_ci()["resolved"] == []
    deposits.append_local(_row(GROVE, SHA2, 51, "CodeQL", "neutral", received_at=_iso(at)))
    deposits.append_local(_row(GROVE, SHA2, 52, "test-suite (3.13)", "success", received_at=_iso(at)))
    assert tick.run_ci()["resolved"] == []  # test-windows (3.11) has not reported
    assert c.named("human_required_resolve") == []
    deposits.append_local(_row(GROVE, SHA2, 53, "test-windows (3.11)", "success", received_at=_iso(at)))
    r = tick.run_ci()
    assert [x["item_id"] for x in r["resolved"]] == ["hr-1"]


def test_a_later_head_with_an_extra_red_leg_resolves_nothing(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    at = time.time() + 60
    _green_head(GROVE, SHA2, 76, at=at)
    deposits.append_local(_row(GROVE, SHA2, 62, "fleet-seams", "failure", received_at=_iso(at)))
    r = tick.run_ci()
    assert r["resolved"] == [] and c.named("human_required_resolve") == []
    assert [f["head_sha"] for f in r["filed"]] == [SHA2]


def test_an_earlier_green_head_does_not_resolve_a_later_red(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    _green_head(GROVE, SHA2, 76, at=time.time() - 3600)
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_ci()
    assert r["resolved"] == [] and c.named("human_required_resolve") == []


def test_the_same_head_rerun_to_green_resolves(home, monkeypatch):
    """Loki finding 3: a head that goes green by re-running its failed
    jobs (new check_run_ids, same sha) resolves its own item."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    at = time.time() + 120
    deposits.append_local(_row(GROVE, SHA, 71, "title", "success", received_at=_iso(at)))
    assert tick.run_ci()["resolved"] == []  # test-suite and test-windows still not green
    deposits.append_local(_row(GROVE, SHA, 72, "test-suite (3.13)", "success", received_at=_iso(at)))
    deposits.append_local(_row(GROVE, SHA, 73, "test-windows (3.11)", "success", received_at=_iso(at)))
    r = tick.run_ci()
    assert r["resolved"] == [{"where": f"{GROVE}#76@{SHA}", "item_id": "hr-1", "superseded_by": SHA,
                              "how": "re-run"}]
    note = c.named("human_required_resolve")[0]["note"]
    assert note.startswith("re-run green at ")
    # the pending cancelled leg re-ran too: it is `rerun`, not waiting, and
    # leaves the pending set; no second item was filed
    assert r["cancelled"] == [{"repo": GROVE, "pr": 76, "head_sha": SHA, "leg": "test-windows (3.11)",
                               "state": "rerun", "latest": "success"}]
    assert json.loads(state_path().read_text())["ci_cancelled_pending"] == {}
    assert len(c.named("human_required_enqueue")) == 1


def test_a_refused_resolve_is_reported_and_retried(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()

    resolve_calls = {"n": 0}
    hr_n = {"n": 0}

    def answer(name, inputs, n):
        if name == "human_required_resolve":
            resolve_calls["n"] += 1
            if resolve_calls["n"] == 1:
                return {"error": "unknown_item"}
        if name.startswith("human_required"):
            hr_n["n"] += 1
            return {"ok": True, "id": f"hr-{hr_n['n']}"}
        return {"ok": True}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    tick.run_ci()
    _green_head(GROVE, SHA2, 76, at=time.time() + 60)
    r = tick.run_ci()
    assert r["resolved"] == [] and r["resolve_refused"][0]["error"] == "unknown_item"
    assert r["status"] == "ok"
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
    assert r["would_resolve"] == [{"where": f"{GROVE}#76@{SHA}", "superseded_by": SHA2, "how": "superseded"}]
    assert c.named("human_required_resolve") == []


# ── Loki finding 4: the maps stay bounded ────────────────────────────────────

def test_state_maps_are_pruned_to_live_heads(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    base = time.time()
    # ten green heads on an unrelated PR: only the newest survives
    for i in range(10):
        _green_head("willow-memory/willow-mcp", f"{i:040x}", 600, at=base + i, start_cid=1000 + 10 * i)
    # and the seed PR goes green on a later head, then gets two more heads
    _green_head(GROVE, SHA2, 76, at=base + 60)
    _green_head(GROVE, SHA3, 76, at=base + 120, start_cid=90)
    r = tick.run_ci()
    assert [x["item_id"] for x in r["resolved"]] == ["hr-1"]
    assert r["pruned"]["items"] == 1 and r["pruned"]["heads"] >= 10
    state = json.loads(state_path().read_text())
    assert state["ci_items"] == {}
    assert list(state["ci_heads"]["willow-memory/willow-mcp#600"]) == [f"{9:040x}"]
    assert list(state["ci_heads"][f"{GROVE}#76"]) == [SHA3]
    # one live head per PR — including the seed's green willow-bot#7
    assert set(state["ci_head_legs"]) == {f"willow-memory/willow-mcp#600@{9:040x}", f"{GROVE}#76@{SHA3}",
                                          "willow-memory/willow-bot#7@" + "b" * 40}
    # ci_filed is the durable memory: the resolved item's legs never re-file
    deposits.append_local(_row(GROVE, SHA, 1, "title", "failure"))
    assert tick.run_ci()["skipped"] == 1 and len(c.named("human_required_enqueue")) == 1


def test_pruning_keeps_the_head_of_an_unresolved_item_and_a_pending_cancel(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()
    # a newer head that is NOT green: the old item stays, and so must its head
    deposits.append_local(_row(GROVE, SHA2, 80, "title", "failure", received_at=_iso(time.time() + 60)))
    tick.run_ci()
    state = json.loads(state_path().read_text())
    assert set(state["ci_heads"][f"{GROVE}#76"]) == {SHA, SHA2}
    assert set(state["ci_items"]) == {f"{GROVE}#76@{SHA}", f"{GROVE}#76@{SHA2}"}


# ── the one-time legacy clear ────────────────────────────────────────────────

def _legacy(i, where=f"{GROVE}#76", check="test-matrix (3.13)", conclusion="cancelled"):
    return {"id": f"old-{i}", "kind": "review", "status": "open",
            "title": f"CI red: {where} — {check} {conclusion}",
            "source_ref": f"https://github.com/willow-memory/x/actions/runs/1/job/{i}"}


def _state_or_empty() -> dict:
    p = state_path()
    return json.loads(p.read_text()) if p.is_file() and p.read_text().strip() else {}


def _queue_client(rows, *, refuse=()):
    def answer(name, inputs, n):
        if name == "human_required_list":
            assert inputs["kind"] == "review" and inputs["status"] == "open"
            return {"items": rows, "by_status": {"open": len(rows)}}
        if name == "human_required_resolve":
            if inputs["item_id"] in refuse:
                return {"error": "unknown_item"}
            return {"ok": True}
        return {"ok": True, "id": f"hr-{n}"}

    return _Client(result=answer)


def _live_shaped_state(*, open_prs, old_ids, merged=(), scan_filters=(), scan=True):
    """The box as the leg-per-item build left it: its item ids in
    `ci_filed` (Loki 18CE5C43 — the collapse build must not read those as
    its own), the scan's `open` set, `merged_synced`, and the scan record
    run_once now writes (absent, or filtered, when the test says so)."""
    state = _state_or_empty()
    state["open"] = open_prs
    state["merged_synced"] = list(merged)
    state["ci_filed"] = {f"{'e' * 40}:{i}": oid for i, oid in enumerate(old_ids)}
    if scan:
        state["scan"] = {"at": "2026-09-20T22:00:00Z", "open": len(open_prs), "filters": list(scan_filters)}
    else:
        state.pop("scan", None)
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


# ── the scan writes `open` honestly (gap 1045a4056d11) ──────────────────────

def _scan_stub(monkeypatch, lines):
    seen_argv: list[list[str]] = []

    def main():
        import sys

        seen_argv.append(list(sys.argv))
        # the real scan filters on sys.argv[1:]; mimic it so a leaked argv shows
        filters = sys.argv[1:]
        for ln in lines:
            key = ln.split("|", 1)[0]
            if not filters or any(f in key for f in filters):
                print(ln)

    monkeypatch.setattr(tick.scan_mod, "main", main)
    monkeypatch.setattr(tick.inbox_mod, "ingest", lambda path: 0)
    return seen_argv


def test_scan_runs_with_argv_reset_so_the_units_loop_arg_is_not_a_filter(home, monkeypatch):
    """The unit runs `willow-bot-steward loop`; scan read `['loop']` as a repo
    filter, rejected every PR and wrote `open = []` every tick."""
    import sys

    monkeypatch.setattr(sys, "argv", ["willow-bot-steward", "loop"])
    seen = _scan_stub(monkeypatch, ["o/r#1|a|https://x/1", "o/r#2|b|https://x/2"])
    assert tick.run_once(do_host_sync=False) == 0
    assert seen == [["scan"]]
    st = _state_or_empty()
    assert st["open"] == ["o/r#1", "o/r#2"]
    assert st["scan"]["open"] == 2 and st["scan"]["filters"] == [] and st["scan"]["at"]
    assert sys.argv == ["willow-bot-steward", "loop"]  # restored


def test_scan_filters_are_explicit_and_receipted(home, monkeypatch, capsys):
    seen = _scan_stub(monkeypatch, ["o/r#1|a|https://x/1", "p/q#2|b|https://x/2"])
    assert tick.run_once(do_host_sync=False, scan_filters=["p/"]) == 0
    assert seen == [["scan", "p/"]]
    st = _state_or_empty()
    assert st["open"] == ["p/q#2"] and st["scan"]["filters"] == ["p/"]
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    scan_line = [ln for ln in lines if ln["event"] == "steward_scan"][0]
    assert scan_line["open"] == 1 and scan_line["filters"] == ["p/"] and "detail" not in scan_line


def test_an_empty_unfiltered_scan_is_visible_in_the_receipt(home, monkeypatch, capsys):
    _scan_stub(monkeypatch, [])
    assert tick.run_once(do_host_sync=False) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    scan_line = [ln for ln in lines if ln["event"] == "steward_scan"][0]
    assert scan_line["open"] == 0 and scan_line["filters"] == []
    assert scan_line["detail"].startswith("scan returned no open PRs")
    assert _state_or_empty()["scan"]["open"] == 0


MCP = "willow-memory/willow-mcp"


def test_legacy_clear_resolves_only_what_run_ci_filed_before_this_build(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _seed()
    c = _Client()
    _use(monkeypatch, c)
    tick.run_ci()  # files hr-1, a new-shape item
    rows = [
        _legacy(1),                                                  # cancelled, open PR → noise
        _legacy(2, check="test-matrix (3.11)"),                      # cancelled, open PR → noise
        _legacy(3, check="fleet-seams", conclusion="failure"),       # REAL red on an open PR → kept
        _legacy(4, where=f"{MCP}#579", check="test-matrix (3.13)", conclusion="cancelled"),  # live #579 shape
        _legacy(5, where=f"{MCP}#550", check="test-matrix (3.11)", conclusion="failure"),    # red, PR merged → moot
        _legacy(6, where=f"{MCP}#540", check="lint", conclusion="timed_out"),                 # red, PR closed → moot
        {"id": "hr-1", "kind": "review", "status": "open",
         "title": f"CI red: {GROVE}#76 — 2 leg(s): title, test-suite (3.13)",
         "source_ref": "https://github.com/x/actions/runs/1/job/1"},
        {"id": "ask-9", "kind": "review", "status": "open", "title": "Review the willow-bot brief", "source_ref": ""},
        {"id": "ask-10", "kind": "review", "status": "open",
         "title": "CI red: something — someone typed this failure", "source_ref": "mailto:not-github"},
        {"id": "cons-1", "kind": "consent", "status": "open", "title": "CI red: x — y failure", "source_ref": ""},
    ]
    # the leg-per-item build's ids sit in ci_filed on the live box; #76 and #579 are open,
    # #550 merged, #540 closed without merge; the scan record is this build's, unfiltered
    _live_shaped_state(open_prs=[f"{GROVE}#76", f"{MCP}#579"], merged=[f"{MCP}#550"],
                       old_ids=["old-1", "old-2", "old-3", "old-4", "old-5", "old-6"])
    q = _queue_client(rows)
    _use(monkeypatch, q)
    r = tick.run_ci_legacy_clear(build_sha="6c91320")
    assert r["status"] == "ok" and r["ran"] and r["recorded"] and r["open_known"] is True
    assert r["listed"] == 10 and r["legacy"] == 6
    assert [(x["item_id"], x["why"]) for x in r["resolved"]] == [
        ("old-1", "cancelled run"), ("old-2", "cancelled run"), ("old-4", "cancelled run"),
        ("old-5", "PR merged"), ("old-6", "PR closed without merge")] and r["refused"] == []
    assert r["kept"] == [{"item_id": "old-3", "title": _legacy(3, check="fleet-seams", conclusion="failure")["title"],
                          "reason": "real red on an open PR"}]
    notes = {i["item_id"]: i["note"] for i in q.named("human_required_resolve")}
    assert notes["old-1"] == "superseded by the run_ci collapse build (6c91320): cancelled run, not a failure"
    assert notes["old-5"] == "moot: PR merged; cleared by the run_ci collapse build (6c91320)"
    assert notes["old-6"] == "moot: PR closed without merge; cleared by the run_ci collapse build (6c91320)"
    assert all(i["status"] == "resolved" for i in q.named("human_required_resolve"))
    state = json.loads(state_path().read_text())
    assert state["ci_legacy_cleared"] == {"at": r["at"], "resolved": 5, "kept": 1, "build_sha": "6c91320"}


def test_legacy_clear_keeps_every_real_red_when_the_open_set_is_unknown(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    q = _queue_client([_legacy(1), _legacy(3, check="fleet-seams", conclusion="failure")])
    _use(monkeypatch, q)
    r = tick.run_ci_legacy_clear(build_sha="abc")  # no state file: no scan has ever run
    assert r["open_known"] is False
    assert [x["item_id"] for x in r["resolved"]] == ["old-1"]
    assert r["kept"] == [{"item_id": "old-3", "title": _legacy(3, check="fleet-seams", conclusion="failure")["title"],
                          "reason": "open set unknown (no unfiltered scan on record)"}]
    assert r["recorded"] is True  # kept is a decision, not a refusal


def test_legacy_clear_does_not_trust_an_empty_open_set_from_the_old_filtered_scan(home, monkeypatch):
    """Loki B7B947C4: the unit's scan wrote `open = []` under the leaked
    `['loop']` filter every tick, so `open_known` was True on an empty set
    and every real red read as moot. An empty `open` with no scan record —
    the box as the old build left it — is unknown, not empty."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    red = _legacy(3, check="fleet-seams", conclusion="failure")
    _live_shaped_state(open_prs=[], old_ids=["old-1", "old-3"], scan=False)
    q = _queue_client([_legacy(1), red])
    _use(monkeypatch, q)
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["open_known"] is False
    assert [x["item_id"] for x in r["resolved"]] == ["old-1"]
    assert r["kept"][0]["item_id"] == "old-3"
    # a FILTERED scan record is not trusted either
    _live_shaped_state(open_prs=[], old_ids=["old-1", "old-3"], scan_filters=["loop"])
    r2 = tick.run_ci_legacy_clear(build_sha="abc", force=True)
    assert r2["open_known"] is False and r2["kept"][0]["item_id"] == "old-3"
    # an unfiltered scan that genuinely found nothing IS trusted: the red's PR is not open
    _live_shaped_state(open_prs=[], old_ids=["old-1", "old-3"], scan_filters=[])
    r3 = tick.run_ci_legacy_clear(build_sha="abc", force=True)
    assert r3["open_known"] is True and r3["kept"] == []
    assert [(x["item_id"], x["why"]) for x in r3["resolved"]] == [("old-1", "cancelled run"),
                                                                    ("old-3", "PR closed without merge")]


def test_legacy_clear_paces_against_the_limiter_and_finishes(home, monkeypatch):
    """169 open on the box; the store meters 60/min with a burst of 10 and
    answers the 11th call rate_limited. The clear waits what it is told
    and retries the same item — every item lands, none is skipped."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    rows = [_legacy(i) for i in range(1, 26)]
    limited = {"n": 0}

    def answer(name, inputs, n):
        if name == "human_required_list":
            return {"items": rows, "count": len(rows), "stats": {"open": len(rows)}}
        if name == "human_required_resolve":
            limited["n"] += 1
            if limited["n"] % 11 == 0:
                return {"error": "rate_limited", "retry_after": 2}
            return {"ok": True}
        return {"ok": True}

    sleeps: list[int] = []
    monkeypatch.setattr(tick, "_sleep", sleeps.append)
    _use(monkeypatch, _Client(result=answer))
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["status"] == "ok" and r["recorded"] and r["remaining"] == 0
    assert len(r["resolved"]) == 25 and r["refused"] == []
    assert r["paced"] == len(sleeps) == 2 and sleeps == [2, 2]


def test_legacy_clear_stops_at_the_time_budget_and_resumes_next_tick(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    rows = [_legacy(i) for i in range(1, 4)]
    calls = {"n": 0}

    def answer(name, inputs, n):
        if name == "human_required_list":
            return {"items": rows}
        calls["n"] += 1
        if calls["n"] == 2:
            return {"error": "rate_limited", "retry_after": 30}
        return {"ok": True}

    monkeypatch.setattr(tick, "_MIRROR_TIME_BUDGET_S", 5.0)
    monkeypatch.setattr(tick, "_sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep past budget")))
    _use(monkeypatch, _Client(result=answer))
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["status"] == "paced" and r["recorded"] is False
    assert [x["item_id"] for x in r["resolved"]] == ["old-1"] and r["remaining"] == 2
    assert "ci_legacy_cleared" not in _state_or_empty()
    monkeypatch.setattr(tick, "_MIRROR_TIME_BUDGET_S", 120.0)
    r2 = tick.run_ci_legacy_clear(build_sha="abc")
    assert r2["ran"] is True and r2["recorded"] is True


def test_legacy_clear_runs_once_unless_forced(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    q = _queue_client([_legacy(1)])
    _use(monkeypatch, q)
    assert tick.run_ci_legacy_clear(build_sha="abc")["resolved"] != []
    r2 = tick.run_ci_legacy_clear(build_sha="abc")
    assert r2["status"] == "ok" and r2["ran"] is False and "already cleared" in r2["detail"]
    assert len(q.named("human_required_list")) == 1
    r3 = tick.run_ci_legacy_clear(build_sha="abc", force=True)
    assert r3["ran"] is True and len(q.named("human_required_list")) == 2


def test_legacy_clear_partial_is_not_recorded_and_retries(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    q = _queue_client([_legacy(1), _legacy(2)], refuse={"old-2"})
    _use(monkeypatch, q)
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["status"] == "partial" and r["recorded"] is False
    assert [x["item_id"] for x in r["resolved"]] == ["old-1"]
    assert r["refused"] == [{"item_id": "old-2", "title": _legacy(2)["title"], "error": "unknown_item"}]
    assert "ci_legacy_cleared" not in _state_or_empty()
    # next tick runs again — the loop does not need a hand pass
    assert tick.run_ci_legacy_clear(build_sha="abc")["ran"] is True


def test_legacy_clear_is_unreachable_when_the_queue_cannot_be_listed(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")

    def answer(name, inputs, n):
        if name == "human_required_list":
            return {"error": "gate denied"}
        raise AssertionError("must not resolve when it cannot see the queue")

    _use(monkeypatch, _Client(result=answer))
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["status"] == "unreachable" and r["ran"] is False and r["detail"] == "gate denied"
    assert "ci_legacy_cleared" not in _state_or_empty()


def test_legacy_clear_is_absent_when_mcp_is_off(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    _use(monkeypatch, _Client(result=lambda *a: (_ for _ in ()).throw(AssertionError("no calls"))))
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["status"] == "absent" and r["ran"] is False


def test_legacy_title_shape_is_exact():
    ok = {"id": "x", "title": f"CI red: {GROVE}#76 — test-windows (3.11) cancelled", "source_ref": ""}
    assert tick._is_legacy_ci_item(ok, own_ids=set())
    assert not tick._is_legacy_ci_item({**ok, "id": "mine"}, own_ids={"mine"})
    assert not tick._is_legacy_ci_item({**ok, "title": f"CI red: {GROVE}#76 — 1 leg(s): lint"}, own_ids=set())
    assert not tick._is_legacy_ci_item({**ok, "title": "CI red: x — y success"}, own_ids=set())
    assert not tick._is_legacy_ci_item({**ok, "source_ref": "https://example.org/job"}, own_ids=set())
