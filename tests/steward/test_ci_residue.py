"""run_ci residue after the collapse (gap 52928edb3fc7, 2026-09-21).

Two defects measured after #33 + #35 went live. (1) A PR-less cancelled
leg — a release commit on master, keyed `repo@sha12` — had no successor
by construction and filed as `stuck` after grace under a "CI red" title;
a cancelled leg on a PR that had since closed did the same. (2) Every
MCP step shared the store's one limiter and only the mirror paced, so ci
ended `could-not-run rate_limited` on a real red and audit refused PRs.

Now: the deposit carries `head_branch`, so a PR-less head's successor is
the next head on the same branch; a closed PR's cancelled legs are `moot`;
a head whose only legs are stuck cancellations files as "CI stuck", never
"CI red"; and ci + audit pace through their own budgets and say so.
The deposits file is real; the MCP client is a fake with a fake clock.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from willow_bot import deposits
from willow_bot.steward import inbox, tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
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


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row(repo, sha, cid, name, conclusion, *, pr=None, branch=None, received_at=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid, check_name=name,
                                     conclusion=conclusion, pr_number=pr, head_branch=branch,
                                     received_at=received_at)
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


def _fake_time(monkeypatch, start=1000.0):
    t = {"now": start}
    monkeypatch.setattr(tick, "_clock", lambda: t["now"])
    monkeypatch.setattr(tick, "_sleep", lambda s: t.__setitem__("now", t["now"] + s))
    return t


def _later(minutes: float):
    base = time.time()
    return lambda: base + minutes * 60


MCP = "willow-memory/willow-mcp"
REL = "af6799a0000000000000000000000000000000aa"
REL2 = "0f7687a5bade358ce7a7380cfd7ddb98165b1c2c"
PRSHA = "30a6e37f0bdc0714f27f0de60bdb50c3dfc7532d"


def _prime():
    """First run starts at EOF; these tests are about what happens after."""
    deposits.append_local(_row("x/y", "0" * 40, 0, "older", "success"))
    assert tick.run_ci(enable_mcp=False)["first_run_skipped_bytes"] > 0


# ── the deposit carries the branch ───────────────────────────────────────────

def test_check_run_deposit_carries_the_suite_branch(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)  # local deposit only; no store_put
    payload = {
        "action": "completed",
        "repository": {"full_name": MCP},
        "sender": {"type": "Bot"},
        "check_run": {"id": 7, "name": "test", "head_sha": REL, "conclusion": "cancelled",
                      "status": "completed", "html_url": "https://github.com/x", "pull_requests": [],
                      "check_suite": {"head_branch": "master"}},
    }
    out = deposits.deposit_from_check_run_payload(payload)
    assert out is not None
    rows = [json.loads(ln) for ln in deposits.deposits_jsonl().read_text().splitlines() if ln.strip()]
    assert rows[-1]["head_branch"] == "master" and rows[-1]["pr_number"] is None


def test_a_row_without_a_suite_reads_as_no_branch(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    payload = {"action": "completed", "repository": {"full_name": MCP}, "sender": {},
               "check_run": {"id": 8, "name": "test", "head_sha": REL, "conclusion": "success",
                             "pull_requests": [{"number": 5}]}}
    deposits.deposit_from_check_run_payload(payload)
    rows = [json.loads(ln) for ln in deposits.deposits_jsonl().read_text().splitlines() if ln.strip()]
    assert rows[-1]["head_branch"] == "" and rows[-1]["pr_number"] == 5


# ── 1. a PR-less cancelled leg finds its successor on the branch ─────────────

def test_prless_cancelled_head_is_superseded_by_the_next_head_on_its_branch(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    base = time.time()
    deposits.append_local(_row(MCP, REL, 1, "test", "cancelled", branch="master", received_at=_iso(base)))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["waiting"]
    # The next release commit lands on master — a different head, no PR.
    deposits.append_local(_row(MCP, REL2, 2, "test", "success", branch="master", received_at=_iso(base + 60)))
    r2 = tick.run_ci()
    assert r2["cancelled"] == [{"repo": MCP, "pr": None, "head_sha": REL, "leg": "test",
                                "state": "superseded", "successor": REL2}]
    assert c.named("human_required_enqueue") == []
    state = json.loads(state_path().read_text())
    assert state["ci_cancelled_pending"] == {}
    # The branch group is pruned like a PR's: once the leg is decided only
    # the newest head on master is kept.
    assert list(state["ci_heads"][tick._ci_branch_key(MCP, "master")]) == [REL2]
    assert r2["pruned"] == {"heads": 1, "items": 0}


def test_prless_cancelled_head_on_another_branch_is_not_a_successor(home, monkeypatch):
    _prime()
    _use(monkeypatch, _Client())
    base = time.time()
    deposits.append_local(_row(MCP, REL, 1, "test", "cancelled", branch="master", received_at=_iso(base)))
    deposits.append_local(_row(MCP, REL2, 2, "test", "success", branch="feat/other", received_at=_iso(base + 60)))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["waiting"]


def test_prless_row_without_a_branch_still_ages_to_stuck(home, monkeypatch):
    """A row from before the field existed has no successor group; it is
    judged as it always was — by the grace window alone."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, REL, 1, "test", "cancelled"))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["stuck"]
    assert len(c.named("human_required_enqueue")) == 1


# ── 1. a closed PR's cancelled legs are moot ─────────────────────────────────

def _close(repo_pr: str, *, merged: bool, at="2026-09-21T00:00:00+00:00"):
    state = json.loads(state_path().read_text()) if state_path().is_file() else {}
    state.setdefault("pr_closed", {})[repo_pr] = {"at": at, "merged": merged}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state, indent=2) + "\n")


def test_cancelled_legs_on_a_merged_pr_are_moot_and_never_filed(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    for i, leg in enumerate(("title", "lint", "vendor-sync", "test")):
        deposits.append_local(_row(MCP, PRSHA, 10 + i, leg, "cancelled", pr=583))
    r = tick.run_ci()
    assert {x["state"] for x in r["cancelled"]} == {"waiting"}
    _close(f"{MCP}#583", merged=True)
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r2 = tick.run_ci()
    assert [(x["leg"], x["state"], x["closed"]) for x in r2["cancelled"]] == [
        ("title", "moot", "merged"), ("lint", "moot", "merged"),
        ("vendor-sync", "moot", "merged"), ("test", "moot", "merged")]
    assert c.named("human_required_enqueue") == []
    state = json.loads(state_path().read_text())
    assert state["ci_cancelled_pending"] == {} and state["ci_filed_cancelled"] == {}
    assert tick.run_ci()["cancelled"] == []


def test_cancelled_legs_on_a_pr_closed_without_merge_are_moot(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row("Die-Namic-Systems/Nestor", PRSHA, 20, "test-windows (3.10)", "cancelled", pr=302))
    _close("Die-Namic-Systems/Nestor#302", merged=False)
    r = tick.run_ci()
    assert [(x["state"], x["closed"]) for x in r["cancelled"]] == [("moot", "closed")]
    assert c.named("human_required_enqueue") == []


def test_a_red_leg_on_a_closed_pr_is_still_a_red(home, monkeypatch):
    """Moot is a cancelled-leg state only: a failure is a failure."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 40, "test", "failure", pr=585))
    _close(f"{MCP}#585", merged=True)
    r = tick.run_ci()
    assert [f["where"] for f in r["filed"]] == [f"{MCP}#585"]


# ── 1. the inbox remembers a closed PR durably ───────────────────────────────

def _inbox_item(home, *, action, number=99, merged=False, state="open", wid=None):
    box = home / "upstream_steward" / "webhook_inbox"
    box.mkdir(parents=True, exist_ok=True)
    wid = wid or f"wh-{number}-{action}"
    item = {"source": "willow-bot", "received_at": "2026-09-21T00:00:00+00:00", "event": "pull_request",
            "repo": MCP, "action": action, "work_id": wid, "kind": "pull_request", "number": number,
            "title": "t", "state": state, "merged": merged, "html_url": "https://github.com/x"}
    (box / f"{wid}.json").write_text(json.dumps(item), encoding="utf-8")


def test_inbox_records_a_closed_pr_and_forgets_it_on_reopen(home, monkeypatch, capsys):
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)
    st = home / "state.json"
    _inbox_item(home, action="closed", merged=True, state="closed")
    assert inbox.ingest(st) == 0
    saved = json.loads(st.read_text())
    assert saved["pr_closed"] == {f"{MCP}#99": {"at": "2026-09-21T00:00:00+00:00", "merged": True}}
    _inbox_item(home, action="reopened", state="open")
    assert inbox.ingest(st) == 0
    assert json.loads(st.read_text())["pr_closed"] == {}
    capsys.readouterr()


def test_inbox_closed_record_is_bounded_oldest_first(home, monkeypatch, capsys):
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)
    monkeypatch.setattr(inbox, "_MAX_CLOSED", 2)
    st = home / "state.json"
    for n, at in ((1, "2026-09-01T00:00:00+00:00"), (2, "2026-09-02T00:00:00+00:00"), (3, "2026-09-03T00:00:00+00:00")):
        box = home / "upstream_steward" / "webhook_inbox"
        box.mkdir(parents=True, exist_ok=True)
        item = {"source": "willow-bot", "received_at": at, "event": "pull_request", "repo": MCP,
                "action": "closed", "work_id": f"w{n}", "kind": "pull_request", "number": n,
                "title": "t", "state": "closed", "merged": True, "html_url": ""}
        (box / f"w{n}.json").write_text(json.dumps(item), encoding="utf-8")
    assert inbox.ingest(st) == 0
    assert sorted(json.loads(st.read_text())["pr_closed"]) == [f"{MCP}#2", f"{MCP}#3"]
    capsys.readouterr()


# ── 1. the stuck title is not "CI red" ───────────────────────────────────────

def test_a_head_of_only_stuck_cancellations_files_as_ci_stuck(home, monkeypatch):
    monkeypatch.setenv(tick._CI_CANCELLED_GRACE_ENV, "10")
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row("Die-Namic-Systems/Nestor", PRSHA, 50, "test-windows (3.10)", "cancelled", pr=304))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["stuck"]
    (item,) = c.named("human_required_enqueue")
    assert item["title"] == ("CI stuck: Die-Namic-Systems/Nestor#304 — 1 leg(s) cancelled, "
                             "no successor after 10 min: test-windows (3.10)")
    assert "red" not in item["title"]
    assert item["summary"].startswith(f"head {PRSHA}\n") and "concluded cancelled" in item["summary"]
    assert r["filed"][0]["conclusions"] == ["cancelled"]
    # It is not a legacy item either: the one-time clear must not touch it.
    assert not tick._is_legacy_ci_item({"id": "x", "title": item["title"], "source_ref": item["source_ref"]},
                                       own_ids=set())


def test_a_head_with_a_red_and_a_stuck_leg_is_still_red(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 60, "lint", "cancelled", pr=590))
    deposits.append_local(_row(MCP, PRSHA, 61, "test", "failure", pr=590))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    tick.run_ci()
    (item,) = c.named("human_required_enqueue")
    assert item["title"].startswith("CI red: ")


# ── 2. each step paces through its own budget ────────────────────────────────

class _Metered:
    """The store's token bucket in miniature, shared across steps as the
    live one is: `burst` calls per `retry_after` window succeed, the rest
    answer rate_limited until the clock moves on."""

    def __init__(self, clock, *, burst, retry_after=2):
        self.clock, self.burst, self.retry_after = clock, burst, retry_after
        self.window_start, self.in_window, self.n, self.calls = clock(), 0, 0, []

    def __call__(self, name, inputs):
        now = self.clock()
        if now - self.window_start >= self.retry_after:
            self.window_start, self.in_window = now, 0
        self.calls.append((name, inputs))
        if self.in_window >= self.burst:
            return {"error": "rate_limited", "retry_after": self.retry_after}
        self.in_window += 1
        self.n += 1
        if name == "dispatch_send":
            return {"dispatch_id": f"D{self.n}"}
        return {"ok": True, "id": f"hr-{self.n}"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


def test_ci_paces_through_the_limiter_instead_of_refusing(home, monkeypatch):
    _prime()
    t = _fake_time(monkeypatch)
    c = _Metered(lambda: t["now"], burst=1)
    _use(monkeypatch, c)
    for i, pr in enumerate((601, 602, 603)):
        deposits.append_local(_row(MCP, f"{i}" * 40, 70 + i, "test", "failure", pr=pr))
    r = tick.run_ci()
    assert r["status"] == "ok" and r["refused"] == []
    assert [f["where"] for f in r["filed"]] == [f"{MCP}#601", f"{MCP}#602", f"{MCP}#603"]
    assert r["paced"] >= 2 and r["budget_spent"] is False and r["remaining"] == 0
    assert r["calls"] == len(c.calls)


def test_ci_stops_paced_at_its_own_budget_and_resumes(home, monkeypatch):
    _prime()
    t = _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_CI_TIME_BUDGET_S", 3.0)
    c = _Metered(lambda: t["now"], burst=1, retry_after=2)
    _use(monkeypatch, c)
    for i, pr in enumerate((611, 612, 613)):
        deposits.append_local(_row(MCP, f"{i}" * 40, 80 + i, "test", "failure", pr=pr))
    r = tick.run_ci()
    assert r["status"] == "paced" and r["budget_spent"] is True
    assert 1 <= len(r["filed"]) < 3 and r["remaining"] == 3 - len(r["filed"])
    # A pause is not a refusal (Loki 095AF9DB): status and `refused` agree.
    assert r["refused"] == []
    assert r["stopped"]["reason"].startswith("rate_limited (paced") and r["stopped"]["at"].startswith(MCP)
    assert r["new_offset"] == r["offset"]
    monkeypatch.setattr(tick, "_CI_TIME_BUDGET_S", 60.0)
    r2 = tick.run_ci()
    assert r2["status"] == "ok" and r2["remaining"] == 0
    assert len({f["where"] for f in r["filed"] + r2["filed"]}) == 3


def test_audit_paces_through_the_limiter_and_keeps_its_envelope(home, monkeypatch):
    t = _fake_time(monkeypatch)
    c = _Metered(lambda: t["now"], burst=1)
    _use(monkeypatch, c)
    state = {"pending_audit": [{"repo_pr": f"rudi193-cmd/safe-app-store#{n}", "title": "t", "url": "u"}
                               for n in (151, 152, 153)],
             "audit_dispatched": {}, tick._AUDIT_ENVELOPE_STATE_KEY: "env-dispatch-8cabfd667263"}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_audit()
    assert r["status"] == "ok" and r["refused"] == [] and len(r["dispatched"]) == 3
    assert r["paced"] >= 2 and r["budget_spent"] is False
    assert r["envelope_id"] == "env-dispatch-8cabfd667263"
    assert json.loads(state_path().read_text())[tick._AUDIT_ENVELOPE_STATE_KEY] == "env-dispatch-8cabfd667263"


def test_audit_budget_spent_leaves_the_rest_pending(home, monkeypatch):
    t = _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_AUDIT_TIME_BUDGET_S", 3.0)
    c = _Metered(lambda: t["now"], burst=1, retry_after=2)
    _use(monkeypatch, c)
    state = {"pending_audit": [{"repo_pr": f"x/y#{n}", "title": "t", "url": "u"} for n in (1, 2, 3, 4)],
             "audit_dispatched": {}, tick._AUDIT_ENVELOPE_STATE_KEY: "env-1"}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_audit()
    assert r["status"] == "paced" and r["budget_spent"] is True
    assert 1 <= len(r["dispatched"]) < 4
    assert r["remaining"] == 4 - len(r["dispatched"])
    assert r["refused"] == [] and r["stopped"]["at"].startswith("x/y#")
    saved = json.loads(state_path().read_text())
    assert len(saved["pending_audit"]) == r["remaining"]
    assert all("last_error" not in p for p in saved["pending_audit"])  # paused, not refused
    assert saved[tick._AUDIT_ENVELOPE_STATE_KEY] == "env-1"  # a limiter answer is not a refused id


def test_mirror_is_capped_in_calls_so_later_steps_keep_their_share(home, monkeypatch):
    t = _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_MIRROR_CALLS_PER_TICK", 4)
    c = _Metered(lambda: t["now"], burst=100)
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", c)
    for i in range(10):
        deposits.append_local(_row(MCP, f"{i:040x}", 90 + i, "test", "success", pr=1))
    r = tick.run_mirror()
    assert r["status"] == "paced" and r["mirrored"] == 4 and r["calls"] == 4
    assert r["budget_spent"] is True and r["behind"] > 0
    assert r["detail"].startswith("rate_limited (call cap 4")
    # The next tick resumes exactly where it left off.
    r2 = tick.run_mirror()
    assert r2["mirrored"] == 4 and r2["new_offset"] > r["new_offset"]


THREE = {"paced", "budget_spent", "calls"}


def test_every_paced_step_receipt_carries_the_three_fields(home, monkeypatch):
    _fake_time(monkeypatch)
    _use(monkeypatch, _Client())
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps({"pending_audit": [], "audit_dispatched": {}}) + "\n")
    deposits.append_local(_row(MCP, "a" * 40, 1, "test", "success", pr=1))
    for r in (tick.run_mirror(), tick.run_ci(), tick.run_audit()):
        assert THREE <= set(r), r["event"]
    # The empty audit declared zeros rather than omitting the fields.
    r = tick.run_audit()
    assert r["dispatched"] == [] and (r["paced"], r["budget_spent"], r["calls"]) == (0, False, 0)


def test_early_return_receipts_declare_the_three_fields_too(home, monkeypatch):
    """Loki 095AF9DB: MCP-off and nothing-to-do returns used to omit them,
    leaving a heartbeat reader to guess that absent meant 'did not pace'."""
    _use(monkeypatch, _Client(result=lambda *a: (_ for _ in ()).throw(AssertionError("no calls"))))
    # no deposits file at all
    for r in (tick.run_mirror(), tick.run_ci()):
        assert r["present"] is False and THREE <= set(r) and r["calls"] == 0, r["event"]
    # MCP off, file present
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    deposits.append_local(_row(MCP, "b" * 40, 2, "test", "failure", pr=2))
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps({"pending_audit": [{"repo_pr": "x/y#1", "title": "t", "url": "u"}],
                                        "audit_dispatched": {}}) + "\n")
    for r in (tick.run_mirror(), tick.run_ci(), tick.run_audit()):
        assert r["status"] == "absent" and THREE <= set(r) and r["calls"] == 0, r["event"]


# ── stuck-only items resolve when the PR closes or a successor lands ─────────

def test_a_filed_stuck_item_resolves_when_the_pr_closes(home, monkeypatch):
    """Loki 095AF9DB medium: filed as stuck at tick N, PR merged at N+1 —
    the item used to stay open forever (no green head ever comes for a
    cancelled run)."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 10, "title", "cancelled", pr=583))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    (filed,) = r["filed"]
    assert filed["conclusions"] == ["cancelled"]
    assert json.loads(state_path().read_text())["ci_items"][f"{MCP}#583@{PRSHA}"]["stuck"] is True
    _close(f"{MCP}#583", merged=True)
    r2 = tick.run_ci()
    assert r2["resolved"] == [{"where": f"{MCP}#583@{PRSHA}", "item_id": filed["id"],
                               "superseded_by": PRSHA, "how": "closed-merged"}]
    (res,) = c.named("human_required_resolve")
    assert res["item_id"] == filed["id"] and res["note"].startswith("moot: PR merged; stuck cancelled run")
    assert tick.run_ci()["resolved"] == []


def test_a_filed_stuck_item_resolves_when_the_pr_closes_without_merge(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row("Die-Namic-Systems/Nestor", PRSHA, 11, "test", "cancelled", pr=302))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    tick.run_ci()
    _close("Die-Namic-Systems/Nestor#302", merged=False)
    r = tick.run_ci()
    assert [x["how"] for x in r["resolved"]] == ["closed"]
    assert c.named("human_required_resolve")[0]["note"].startswith("moot: PR closed;")


def test_a_filed_stuck_prless_item_resolves_when_a_later_head_lands_on_its_branch(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    base = time.time()
    deposits.append_local(_row(MCP, REL, 12, "test", "cancelled", branch="master", received_at=_iso(base)))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", lambda: base + 11 * 60)
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["stuck"] and len(r["filed"]) == 1
    item = json.loads(state_path().read_text())["ci_items"][f"{MCP}@{REL[:12]}@{REL}"]
    assert item["stuck"] is True and item["branch"] == "master"
    deposits.append_local(_row(MCP, REL2, 13, "test", "success", branch="master", received_at=_iso(base + 12 * 60)))
    r2 = tick.run_ci()
    assert r2["resolved"] == [{"where": f"{MCP}@{REL[:12]}@{REL}", "item_id": r["filed"][0]["id"],
                               "superseded_by": REL2, "how": "superseded-on-branch"}]
    assert "on the same branch" in c.named("human_required_resolve")[0]["note"]


def test_a_stuck_item_from_the_previous_build_is_recognised_by_the_filed_maps(home, monkeypatch):
    """The three 23:46Z filings carry no `stuck` flag: their id sits in
    ci_filed_cancelled and never in ci_filed."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    state = json.loads(state_path().read_text())
    state["ci_items"] = {f"{MCP}#583@{PRSHA}": {"id": "hr-old", "repo": MCP, "pr": 583, "head_sha": PRSHA,
                                                 "legs": ["title"], "filed_at": "2026-09-20T23:46:00Z"}}
    state["ci_filed_cancelled"] = {f"{PRSHA}:1": "hr-old"}
    state["ci_filed"] = {}
    state["pr_closed"] = {f"{MCP}#583": {"at": "2026-09-21T00:00:00+00:00", "merged": True}}
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_ci()
    assert [(x["item_id"], x["how"]) for x in r["resolved"]] == [("hr-old", "closed-merged")]


def test_a_red_item_on_a_closed_pr_does_not_resolve_by_closure(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 14, "test", "failure", pr=590))
    tick.run_ci()
    _close(f"{MCP}#590", merged=True)
    r = tick.run_ci()
    assert r["resolved"] == [] and c.named("human_required_resolve") == []


def test_a_red_joining_a_stuck_item_makes_it_no_longer_stuck_only(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 15, "lint", "cancelled", pr=591))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    tick.run_ci()
    deposits.append_local(_row(MCP, PRSHA, 16, "test", "failure", pr=591))
    tick.run_ci()
    assert json.loads(state_path().read_text())["ci_items"][f"{MCP}#591@{PRSHA}"]["stuck"] is False
    _close(f"{MCP}#591", merged=True)
    assert tick.run_ci()["resolved"] == []


def test_merged_synced_alone_reads_as_closed_not_merged(home, monkeypatch):
    """merge.py adds closed-not-merged PRs to merged_synced too; the word
    must not claim more than the record knows (Loki 095AF9DB low)."""
    _prime()
    _use(monkeypatch, _Client())
    deposits.append_local(_row(MCP, PRSHA, 30, "lint", "cancelled", pr=584))
    state = json.loads(state_path().read_text())
    state["merged_synced"] = [f"{MCP}#584"]
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_ci()
    assert [(x["state"], x["closed"]) for x in r["cancelled"]] == [("moot", "closed")]


# ── same-tick race: a red and a close together ───────────────────────────────

def test_a_red_and_a_close_in_the_same_tick_do_not_resolve_the_item_moot(home, monkeypatch):
    """Loki FE91FF0E low: candidates were read before this tick's legs
    joined their items, so a red landing in the tick that observed the
    close was resolved away with the red live."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(MCP, PRSHA, 17, "lint", "cancelled", pr=592))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    tick.run_ci()  # filed stuck
    _close(f"{MCP}#592", merged=True)
    deposits.append_local(_row(MCP, PRSHA, 18, "test", "failure", pr=592))  # same tick as the close
    r = tick.run_ci()
    assert [a["check"] for a in r["appended"]] == ["test"]
    assert r["resolved"] == [] and c.named("human_required_resolve") == []
    assert json.loads(state_path().read_text())["ci_items"][f"{MCP}#592@{PRSHA}"]["stuck"] is False


def test_an_old_build_stuck_item_with_a_red_this_tick_is_not_moot_either(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    state = json.loads(state_path().read_text())
    state["ci_items"] = {f"{MCP}#593@{PRSHA}": {"id": "hr-old2", "repo": MCP, "pr": 593, "head_sha": PRSHA,
                                                 "legs": ["lint"], "filed_at": "2026-09-20T23:46:00Z"}}
    state["ci_filed_cancelled"] = {f"{PRSHA}:1": "hr-old2"}
    state["ci_filed"] = {}
    state["pr_closed"] = {f"{MCP}#593": {"at": "2026-09-21T00:00:00+00:00", "merged": True}}
    state_path().write_text(json.dumps(state) + "\n")
    deposits.append_local(_row(MCP, PRSHA, 19, "test", "failure", pr=593))
    r = tick.run_ci()
    assert [a["check"] for a in r["appended"]] == ["test"] and r["resolved"] == []


def test_audit_status_precedence_matches_ci(home, monkeypatch):
    """A tool refusal outranks a pause, on both steps (Loki FE91FF0E info)."""
    t = _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_AUDIT_TIME_BUDGET_S", 3.0)
    inner = _Metered(lambda: t["now"], burst=1, retry_after=2)

    def answer(name, inputs):
        if inputs.get("summary", "").startswith("Audit x/y#1:"):
            return {"error": "gate denied"}
        return inner(name, inputs)

    _use(monkeypatch, answer)
    state = {"pending_audit": [{"repo_pr": f"x/y#{n}", "title": "t", "url": "u"} for n in (1, 2, 3, 4, 5)],
             "audit_dispatched": {}, tick._AUDIT_ENVELOPE_STATE_KEY: "env-1"}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_audit()
    assert r["refused"][0]["error"] == "gate denied"
    assert r["status"] == "could-not-run"  # not "paced", even though the budget was also spent
    assert r["budget_spent"] is True and "stopped" in r


# ── the one-time backfill of stuck items filed by the build before ───────────

def _old_stuck_item(state, where_key, item_id, *, pr, sha, repo=MCP, branch=None):
    item = {"id": item_id, "repo": repo, "pr": pr, "head_sha": sha, "legs": ["test"],
            "filed_at": "2026-09-20T23:46:00Z"}
    if branch is not None:
        item["branch"] = branch
    state.setdefault("ci_items", {})[where_key] = item
    state.setdefault("ci_filed_cancelled", {})[f"{sha}:{item_id}"] = item_id
    state.setdefault("ci_filed", {})


SHA583 = "915c73b" + "0" * 33
SHA304 = "4a6d065" + "0" * 33
SHAREL = "af6799a" + "0" * 33
SHANES = "0464659" + "0" * 33


def _live_box_state(state):
    """The four stuck-only items Loki read on the box (Kart 7GVSQT42),
    plus an unfiltered scan that still lists Nestor#304 as open."""
    _old_stuck_item(state, f"{MCP}#583@{SHA583}", "ac816f29", pr=583, sha=SHA583)
    _old_stuck_item(state, f"Die-Namic-Systems/Nestor#304@{SHA304}", "a1949d40", pr=304, sha=SHA304,
                    repo="Die-Namic-Systems/Nestor")
    _old_stuck_item(state, f"{MCP}@{SHAREL[:12]}@{SHAREL}", "b39bba93", pr=None, sha=SHAREL)
    _old_stuck_item(state, f"Die-Namic-Systems/Nestor@{SHANES[:12]}@{SHANES}", "c9c76b99", pr=None,
                    sha=SHANES, repo="Die-Namic-Systems/Nestor")
    state["ci_legacy_cleared"] = {"at": "2026-09-20T23:12:32Z", "resolved": 163, "kept": 6, "build_sha": "c4deab5"}
    state["merged_synced"] = ["some/other#1"]
    state["scan"] = {"at": "2026-09-21T01:07:30Z", "open": 1, "filters": []}
    state["open"] = ["Die-Namic-Systems/Nestor#304"]


def _listing_client():
    """A fake that also answers the legacy pass's queue listing (empty), so
    `--force` — which re-runs both passes — can get to the backfill."""
    def answer(name, inputs, n):
        if name == "human_required_list":
            return {"items": []}
        return {"ok": True, "id": f"hr-{n}"}
    return _Client(result=answer)


def test_backfill_resolves_closed_stuck_items_and_names_the_orphans(home, monkeypatch):
    c = _listing_client()
    _use(monkeypatch, c)
    state = {}
    _live_box_state(state)
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_ci_legacy_clear(build_sha="26122d8")
    assert r["ran"] is False and r["detail"].startswith("already cleared")  # the legacy pass stays on record
    b = r["backfill"]
    assert b["ran"] is True and b["candidates"] == 4 and b["open_known"] is True
    assert b["resolved"] == [{"item_id": "ac816f29", "where": f"{MCP}#583", "why": "PR not open per the bot's scan"}]
    assert b["kept"] == [
        {"item_id": "a1949d40", "where": "Die-Namic-Systems/Nestor#304", "reason": "PR open"},
        {"item_id": "b39bba93", "where": f"{MCP}@{SHAREL[:12]}", "reason": "no branch on record"},
        {"item_id": "c9c76b99", "where": f"Die-Namic-Systems/Nestor@{SHANES[:12]}", "reason": "no branch on record"},
    ]
    assert b["refused"] == [] and b["remaining"] == 0 and b["recorded"] is True
    assert {"paced", "budget_spent", "calls"} <= set(b)
    (res,) = c.named("human_required_resolve")
    assert res["item_id"] == "ac816f29"
    assert res["note"].startswith("moot: PR not open per the bot's scan; stuck cancelled run, backfilled")
    saved = json.loads(state_path().read_text())
    assert saved["ci_items"][f"{MCP}#583@{SHA583}"]["resolved"]["how"] == "backfill"
    assert saved["ci_stuck_backfilled"] == {"at": r["at"], "resolved": 1, "kept": 3, "build_sha": "26122d8"}
    # Once: the next tick does neither pass.
    r2 = tick.run_ci_legacy_clear(build_sha="26122d8")
    assert r2["ran"] is False and r2["backfill"] == {"ran": False, "detail": f"already backfilled at {r['at']}"}
    assert len(c.named("human_required_resolve")) == 1
    # --force re-runs it; the resolved item is no longer a candidate.
    r3 = tick.run_ci_legacy_clear(build_sha="26122d8", force=True)
    assert r3["backfill"]["ran"] is True and r3["backfill"]["candidates"] == 3 and r3["backfill"]["resolved"] == []


def test_backfill_prefers_the_deposit_stream_words_over_the_scan(home, monkeypatch):
    c = _Client()
    _use(monkeypatch, c)
    state = {}
    _live_box_state(state)
    state["pr_closed"] = {f"{MCP}#583": {"at": "2026-09-20T22:00:00+00:00", "merged": True}}
    state["merged_synced"] = ["Die-Namic-Systems/Nestor#304"]
    state["open"] = []
    state["scan"] = {"at": "x", "open": 0, "filters": []}
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    b = tick.run_ci_legacy_clear(build_sha="abc")["backfill"]
    assert [(x["item_id"], x["why"]) for x in b["resolved"]] == [
        ("ac816f29", "PR merged per deposit stream"), ("a1949d40", "PR closed per host sync")]


def test_backfill_keeps_everything_when_the_open_set_is_unknown(home, monkeypatch):
    c = _Client()
    _use(monkeypatch, c)
    state = {}
    _live_box_state(state)
    state["scan"] = {"at": "x", "open": 0, "filters": ["loop"]}  # the old filtered scan: not trusted
    state["open"] = []
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    b = tick.run_ci_legacy_clear(build_sha="abc")["backfill"]
    assert b["open_known"] is False and b["resolved"] == []
    assert {k["reason"] for k in b["kept"] if "#" in k["where"]} == {"open set unknown (no unfiltered scan on record)"}
    assert c.named("human_required_resolve") == []


def test_backfill_does_not_touch_red_items_or_stuck_items_with_a_branch(home, monkeypatch):
    c = _Client()
    _use(monkeypatch, c)
    state = {}
    _live_box_state(state)
    # a red item on a closed PR: not a candidate
    state["ci_items"][f"{MCP}#600@" + "e" * 40] = {"id": "hr-red", "repo": MCP, "pr": 600, "head_sha": "e" * 40,
                                                    "legs": ["test"], "filed_at": "x"}
    state["ci_filed"]["e" * 40 + ":1"] = "hr-red"
    # a PR-less stuck item with a branch on record: the tick's route, not the backfill's
    _old_stuck_item(state, f"{MCP}@{'f' * 12}@" + "f" * 40, "hr-branch", pr=None, sha="f" * 40, branch="master")
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    b = tick.run_ci_legacy_clear(build_sha="abc")["backfill"]
    assert "hr-red" not in {x["item_id"] for x in b["resolved"] + b["kept"]}
    assert {"item_id": "hr-branch", "where": f"{MCP}@{'f' * 12}", "reason": "awaiting a successor on master"} in b["kept"]


def test_backfill_refusal_is_a_line_and_the_pass_is_not_recorded(home, monkeypatch):
    def answer(name, inputs, n):
        if name == "human_required_resolve":
            return {"error": "gate denied"}
        return {"ok": True, "id": f"hr-{n}"}

    _use(monkeypatch, _Client(result=answer))
    state = {}
    _live_box_state(state)
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    b = tick.run_ci_legacy_clear(build_sha="abc")["backfill"]
    assert b["refused"] == [{"item_id": "ac816f29", "where": f"{MCP}#583", "error": "gate denied"}]
    assert b["recorded"] is False and "ci_stuck_backfilled" not in json.loads(state_path().read_text())
    # and it runs again next tick
    assert tick.run_ci_legacy_clear(build_sha="abc")["backfill"]["ran"] is True


def test_backfill_runs_after_a_fresh_legacy_pass_too(home, monkeypatch):
    """A box that has never run the legacy pass gets both in one step."""
    def answer(name, inputs, n):
        if name == "human_required_list":
            return {"items": []}
        return {"ok": True, "id": f"hr-{n}"}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    state = {}
    _live_box_state(state)
    del state["ci_legacy_cleared"]
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state) + "\n")
    r = tick.run_ci_legacy_clear(build_sha="abc")
    assert r["ran"] is True and r["recorded"] is True
    assert [x["item_id"] for x in r["backfill"]["resolved"]] == ["ac816f29"]
    saved = json.loads(state_path().read_text())
    assert "ci_legacy_cleared" in saved and "ci_stuck_backfilled" in saved
