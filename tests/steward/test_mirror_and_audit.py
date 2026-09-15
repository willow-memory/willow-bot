"""The tick's other two act halves (2026-09-14): the deposit mirror and the
Loki audit dispatch. The webhook unit writes CI outcomes locally and never
mirrors them (506 local rows, 6 in the store); a new PR printed as
``new_pr`` woke nobody once the loop lived under systemd. Both now go
through the steward's MCP client on every tick. The client is a fake here.
"""
from __future__ import annotations

import json

import pytest

from willow_bot import deposits
from willow_bot.steward import tick


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    return tmp_path


class _Client:
    def __init__(self, *, fail_on=None, result=None):
        self.calls = []
        self.fail_on = fail_on or set()
        self.result = result

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        key = inputs.get("record_id") or inputs.get("summary", "")
        if any(f in key for f in self.fail_on):
            raise RuntimeError(f"gate denied for {key}")
        if callable(self.result):
            return self.result(name, inputs)
        return self.result if self.result is not None else {"ok": True}


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


def _row(repo, sha, cid):
    return deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid,
                                      check_name="test", conclusion="success")


# ── mirror ───────────────────────────────────────────────────────────────────

def test_mirror_is_absent_when_mcp_is_off_but_says_how_far_behind(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    for i in range(3):
        deposits.append_local(_row("o/r", "a" * 40, i))
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["status"] == "absent" and r["present"] is True and r["behind"] > 0
    assert c.calls == []


def test_mirror_puts_each_new_row_under_its_record_id_and_advances(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    rows = [_row("forge-play/Forge", "b" * 40, i) for i in range(3)]
    for r in rows:
        deposits.append_local(r)
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["status"] == "ok" and r["mirrored"] == 3 and r["behind"] == 0
    assert [n for n, _ in c.calls] == ["store_put"] * 3
    assert c.calls[0][1]["collection"] == deposits.COLLECTION
    assert c.calls[0][1]["record_id"] == deposits.record_id_for(rows[0])
    assert c.calls[0][1]["record"]["check_run_id"] == 0
    # a second run mirrors nothing new
    r2 = tick.run_mirror()
    assert r2["mirrored"] == 0 and len(c.calls) == 3


def test_mirror_stops_at_the_first_failure_and_keeps_the_offset_before_it(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    rows = [_row("o/r", "c" * 40, i) for i in range(4)]
    for r in rows:
        deposits.append_local(r)
    bad = deposits.record_id_for(rows[2])
    c = _Client(fail_on={bad})
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["status"] == "could-not-run" and r["mirrored"] == 2 and "gate denied" in r["detail"]
    assert r["behind"] > 0
    # the failed row is retried first next time
    c2 = _Client()
    _use(monkeypatch, c2)
    r2 = tick.run_mirror()
    assert r2["mirrored"] == 2 and c2.calls[0][1]["record_id"] == bad


def test_a_refusal_returned_as_a_dict_is_not_counted_as_mirrored(home, monkeypatch):
    """Planted: the live bug. willow-mcp returns a refusal as {'error': ...}
    and the client returns it without raising; the first live mirror counted
    200 of those as landed and moved its offset past them."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    rows = [_row("o/r", "f" * 40, i) for i in range(3)]
    for r in rows:
        deposits.append_local(r)
    c = _Client(result={"error": "orchestrator_session_attestation_missing: ..."})
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["status"] == "could-not-run" and r["mirrored"] == 0
    assert "attestation_missing" in r["detail"]
    assert r["new_offset"] == 0 and r["behind"] > 0
    assert len(c.calls) == 1, "stop at the first refusal; do not burn the rest"


class _MeteredClient:
    """The store's token bucket, in miniature: `burst` calls succeed, then
    `rate_limited` with retry_after until `clock` has advanced by it."""

    def __init__(self, clock, burst=3, retry_after=2):
        self.clock, self.burst, self.retry_after = clock, burst, retry_after
        self.calls, self.window_start, self.in_window = [], clock(), 0

    def __call__(self, name, inputs):
        now = self.clock()
        if now - self.window_start >= self.retry_after:
            self.window_start, self.in_window = now, 0
        self.calls.append(inputs["record_id"])
        if self.in_window >= self.burst:
            return {"error": "rate_limited", "retry_after": self.retry_after}
        self.in_window += 1
        return {"ok": True}


def _fake_time(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(tick, "_clock", lambda: t["now"])
    monkeypatch.setattr(tick, "_sleep", lambda s: t.__setitem__("now", t["now"] + s))
    return t


def test_mirror_paces_through_the_rate_limit_and_skips_no_row(home, monkeypatch):
    """Planted: the live refusal. Three land, the fourth is rate_limited;
    the mirror waits retry_after (fake clock), retries the SAME row, and
    every row lands in order — none skipped, none duplicated."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    t = _fake_time(monkeypatch)
    rows = [_row("o/r", "9" * 40, i) for i in range(8)]
    for r in rows:
        deposits.append_local(r)
    c = _MeteredClient(lambda: t["now"], burst=3, retry_after=2)
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["status"] == "ok" and r["mirrored"] == 8 and r["behind"] == 0
    assert r["paced"] >= 2
    landed = [rid for rid in c.calls]
    # the refused record ids are retried immediately after the wait
    ids = [deposits.record_id_for(x) for x in rows]
    assert [rid for rid in landed if landed.count(rid) >= 1][0] == ids[0]
    assert sorted(set(landed), key=ids.index) == ids


def test_mirror_stops_paced_when_the_time_budget_is_spent(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    t = _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_MIRROR_TIME_BUDGET_S", 3.0)
    rows = [_row("o/r", "8" * 40, i) for i in range(6)]
    for r in rows:
        deposits.append_local(r)
    _use(monkeypatch, _MeteredClient(lambda: t["now"], burst=2, retry_after=2))
    r = tick.run_mirror()
    assert r["status"] == "paced" and 2 <= r["mirrored"] < 6 and r["behind"] > 0
    assert "resumes next tick" in r["detail"]
    # a second tick with a fresh budget picks up exactly where it left off
    _fake_time(monkeypatch)
    monkeypatch.setattr(tick, "_MIRROR_TIME_BUDGET_S", 120.0)
    c2 = _MeteredClient(lambda: t["now"] + 1000, burst=10, retry_after=1)
    _use(monkeypatch, c2)
    r2 = tick.run_mirror()
    assert r2["status"] == "ok" and r["mirrored"] + r2["mirrored"] == 6


def test_every_step_leaves_a_receipt_the_seat_can_read(home, monkeypatch):
    """The journal is not readable from the seat; steward_ticks.jsonl is."""
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    tick.run_sweep()
    tick.run_mirror()
    tick.run_audit()
    lines = [json.loads(ln) for ln in tick._receipts_path().read_text().splitlines() if ln.strip()]
    assert [ln["event"] for ln in lines] == ["steward_sweep", "steward_mirror", "steward_audit"]
    assert all(ln["status"] in ("absent", "ok") for ln in lines)


def test_an_audit_refusal_dict_stays_pending_with_the_tools_reason(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    p = _state_with_pending(home, "o/r#7")
    _use(monkeypatch, _Client(result={"error": "EDQUOT: max_count exhausted"}))
    r = tick.run_audit()
    assert r["dispatched"] == [] and "EDQUOT" in r["refused"][0]["error"]
    st = json.loads(p.read_text())
    assert "EDQUOT" in st["pending_audit"][0]["last_error"]


def test_mirror_does_not_read_a_half_written_last_line(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    deposits.append_local(_row("o/r", "d" * 40, 1))
    with deposits.deposits_jsonl().open("a", encoding="utf-8") as fh:
        fh.write('{"kind":"ci_outcome","repo":"o/r","head_sha":"e"')  # no newline yet
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_mirror()
    assert r["mirrored"] == 1 and r["behind"] > 0


def test_mirror_reports_no_file_as_not_present(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _use(monkeypatch, _Client())
    r = tick.run_mirror()
    assert r["status"] == "ok" and r["present"] is False and r["mirrored"] == 0


# ── audit ────────────────────────────────────────────────────────────────────

def _state_with_pending(home, *keys):
    from willow_bot.steward.config import state_path

    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "seen": list(keys), "open": list(keys), "merged_synced": [], "inbox_consumed": [],
        "webhook_signals": [],
        "pending_audit": [{"repo_pr": k, "title": f"t {k}", "url": f"https://x/{k}"} for k in keys],
        "audit_dispatched": {},
    }))
    return p


def test_audit_is_absent_when_mcp_is_off_and_keeps_pending(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    p = _state_with_pending(home, "forge-play/Forge#31")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_audit()
    assert r["status"] == "absent" and r["pending"] == 1 and c.calls == []
    assert len(json.loads(p.read_text())["pending_audit"]) == 1


def test_audit_dispatches_each_pending_pr_to_loki_as_auditor(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    p = _state_with_pending(home, "forge-play/Forge#31", "willow-memory/willow-mcp#524")
    n = iter(range(100))
    c = _Client(result=lambda name, inputs: {"dispatch_id": f"D{next(n)}", "status": "pending"})
    _use(monkeypatch, c)
    r = tick.run_audit()
    assert r["status"] == "ok" and [d["repo_pr"] for d in r["dispatched"]] == [
        "forge-play/Forge#31", "willow-memory/willow-mcp#524"]
    assert r["refused"] == [] and r["remaining"] == 0
    name, inputs = c.calls[0]
    assert name == "dispatch_send"
    # the call args the verb gate cites: {to_agents: loki, task_class: auditor}
    assert inputs["to_app"] == "loki" and inputs["role"] == "auditor"
    assert inputs["reply_to"] == "willow"
    assert "# Audit forge-play/Forge#31" in inputs["assignment_md"]
    assert "/repos/forge-play/Forge/pulls/31/files" in inputs["assignment_md"]
    assert "handoff_write_v4" in inputs["assignment_md"]
    st = json.loads(p.read_text())
    assert st["pending_audit"] == []
    assert st["audit_dispatched"] == {"forge-play/Forge#31": "D0", "willow-memory/willow-mcp#524": "D1"}


def test_a_refused_dispatch_stays_pending_with_its_reason(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    p = _state_with_pending(home, "o/r#1", "o/r#2")
    c = _Client(fail_on={"o/r#1"}, result={"dispatch_id": "D9"})
    _use(monkeypatch, c)
    r = tick.run_audit()
    assert [d["repo_pr"] for d in r["dispatched"]] == ["o/r#2"]
    assert r["refused"][0]["repo_pr"] == "o/r#1" and "gate denied" in r["refused"][0]["error"]
    st = json.loads(p.read_text())
    assert [i["repo_pr"] for i in st["pending_audit"]] == ["o/r#1"]
    assert "gate denied" in st["pending_audit"][0]["last_error"]
    assert st["audit_dispatched"] == {"o/r#2": "D9"}


def test_a_result_without_a_dispatch_id_is_not_counted_as_dispatched(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    p = _state_with_pending(home, "o/r#1")
    _use(monkeypatch, _Client(result={"error": "ENOENT: no active dispatch envelope"}))
    r = tick.run_audit()
    assert r["dispatched"] == [] and "ENOENT" in r["refused"][0]["error"]
    assert len(json.loads(p.read_text())["pending_audit"]) == 1
    # and a result that is neither an error nor a packet is still not a dispatch
    _use(monkeypatch, _Client(result={"status": "weird"}))
    r2 = tick.run_audit()
    assert r2["dispatched"] == [] and "no dispatch_id" in r2["refused"][0]["error"]


def test_audit_is_capped_per_tick(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _state_with_pending(home, *[f"o/r#{i}" for i in range(8)])
    c = _Client(result={"dispatch_id": "D"})
    _use(monkeypatch, c)
    r = tick.run_audit()
    assert len(r["dispatched"]) == tick._AUDIT_PER_TICK and r["remaining"] == 8 - tick._AUDIT_PER_TICK


def test_run_once_queues_new_prs_for_audit_and_never_twice(home, monkeypatch):
    """The scan finds a PR; it becomes pending_audit once, and a PR already
    dispatched is not re-queued when the scan sees it again."""
    from willow_bot.steward.config import state_path

    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "seen": ["o/r#1"], "open": ["o/r#1"], "merged_synced": [], "inbox_consumed": [],
        "webhook_signals": [], "pending_audit": [], "audit_dispatched": {"o/r#1": "D0"},
    }))
    lines = ["o/r#1|old|https://x/1", "o/r#2|new one|https://x/2"]
    monkeypatch.setattr(tick.scan_mod, "main", lambda: print("\n".join(lines)))
    monkeypatch.setattr(tick.inbox_mod, "ingest", lambda path: 0)
    assert tick.run_once(do_host_sync=False) == 0
    st = json.loads(p.read_text())
    assert [i["repo_pr"] for i in st["pending_audit"]] == ["o/r#2"]
    assert st["pending_audit"][0]["title"] == "new one"
    # second scan, same set: nothing new is queued
    assert tick.run_once(do_host_sync=False) == 0
    assert [i["repo_pr"] for i in json.loads(p.read_text())["pending_audit"]] == ["o/r#2"]


def test_the_cli_knows_mirror_and_audit(home, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    assert tick.main(["mirror"]) == 0
    assert tick.main(["audit"]) == 0
