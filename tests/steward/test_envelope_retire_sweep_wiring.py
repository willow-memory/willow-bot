"""Steward wiring for envelope_retire_sweep (sealed decision 83faa340;
gap 4c7512c57a7e): the heartbeat calls it beside seal_drain /
net_authority_drain, live (dry_run=False — the tick IS the unattended
sweep), and mirrors its receipt honestly. Same fake-MCP-client pattern as
test_net_drain_wiring.py (patched on the real module's ``call`` attribute).
"""
from __future__ import annotations

import sys

import pytest

from willow_bot.steward import heartbeat


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "willow")
    monkeypatch.delenv("WILLOW_BOT_STEWARD_TOOLS", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    return tmp_path


def _patch_client(monkeypatch, fn):
    from willow_bot.steward import mcp_client as real

    monkeypatch.setattr(real, "call", fn)
    assert sys.modules["willow_bot.steward.mcp_client"].call is fn


def _heartbeat_with(monkeypatch, reply_for):
    seen = []

    def call(name, inputs):
        seen.append((name, inputs))
        return reply_for(name)

    _patch_client(monkeypatch, call)
    return seen


def _tool_entry(receipt, name):
    return next(e for e in receipt["tools"] if e["tool"] == name)


def test_envelope_retire_sweep_is_curated_after_net_authority_drain():
    names = [n for n, _ in heartbeat.DEFAULT_CURATED]
    assert names.index("envelope_retire_sweep") == names.index("net_authority_drain") + 1


def test_envelope_retire_sweep_tick_calls_live_not_dry_run():
    args = dict(heartbeat.DEFAULT_CURATED)["envelope_retire_sweep"]
    assert args == {"dry_run": False}


def test_heartbeat_calls_envelope_retire_sweep_with_dry_run_false(home, monkeypatch):
    seen = _heartbeat_with(monkeypatch, lambda n: {"ok": True})
    heartbeat.run_heartbeat()
    assert ("envelope_retire_sweep", {"dry_run": False, "app_id": "willow"}) in seen


def test_heartbeat_mirrors_a_populated_sweep_with_its_rows(home, monkeypatch):
    populated = {
        "state": "populated", "dry_run": False, "examined": 3, "truncated": False,
        "retired": [{"id": "env-1", "verb": "git.push", "reason": "branch_gone"}],
        "kept_standing": 1,
        "kept_in_force": [{"id": "env-2", "verb": "pr.open", "why": "master still exists"}],
        "unreachable": [],
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    r = heartbeat.run_heartbeat()
    assert r["status"] == "ok"
    e = _tool_entry(r, "envelope_retire_sweep")
    assert e["outcome"] == "ok" and e["state"] == "populated"
    assert e["examined"] == 3
    assert e["kept_standing"] == 1
    assert e["truncated"] is False
    assert e["retired"] == [{"id": "env-1", "verb": "git.push", "reason": "branch_gone"}]
    assert e["kept_in_force"] == [{"id": "env-2", "verb": "pr.open", "why": "master still exists"}]
    assert e["dry_run"] is False


def test_heartbeat_mirrors_unreachable_as_a_count_not_the_full_list(home, monkeypatch):
    """Rework of Loki's LOW finding: a chronically-unreachable row must not
    repeat, unbounded, in the JSONL every tick forever — only its count
    rides the heartbeat entry."""
    rows = [{"id": f"env-{i}", "verb": "git.push", "why": "unreachable"} for i in range(23)]
    populated = {
        "state": "populated", "dry_run": False, "examined": 23, "truncated": False,
        "retired": [], "kept_standing": 0, "kept_in_force": [], "unreachable": rows,
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["unreachable_count"] == 23
    assert "unreachable" not in e


def test_heartbeat_mirrors_an_empty_sweep_as_empty(home, monkeypatch):
    empty = {"state": "empty", "dry_run": False, "examined": 0, "retired": [],
             "kept_standing": 0, "kept_in_force": [], "unreachable": []}
    _heartbeat_with(monkeypatch, lambda n: empty if n == "envelope_retire_sweep" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["state"] == "empty" and e["examined"] == 0 and e["retired"] == []


def test_heartbeat_mirrors_an_unreachable_sweep_honestly(home, monkeypatch):
    unreachable = {"state": "unreachable", "reason": "registry_unreadable: OSError",
                   "dry_run": False, "examined": 0, "retired": [], "kept_standing": 0,
                   "kept_in_force": [], "unreachable": []}
    _heartbeat_with(monkeypatch, lambda n: unreachable if n == "envelope_retire_sweep" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["outcome"] == "ok" and e["state"] == "unreachable"
    assert e["reason"] == "registry_unreadable: OSError"


def test_heartbeat_reports_a_raising_sweep_as_could_not_run(home, monkeypatch):
    def call(name, inputs):
        if name == "envelope_retire_sweep":
            raise RuntimeError("MCP server did not initialize within 90s")
        return {"ok": True}

    _patch_client(monkeypatch, call)
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["outcome"] == "could-not-run" and "90s" in e["detail"]


def test_heartbeat_marks_a_gate_denial_as_denied_not_ok(home, monkeypatch):
    """Rework of Loki's LOW finding (probe 1SHYZKWT): a permission refusal
    is an ordinary dict with no `state` field — mcp_client.call does not
    raise on it, so it used to fall through to outcome="ok"."""
    denial = {"error": "gate denied: 'hanuman' not permitted for 'envelope_retire_sweep'"}
    _heartbeat_with(
        monkeypatch, lambda n: denial if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["outcome"] == "denied"
    assert "gate denied" in e["error"]
    assert "state" not in e


def test_a_three_state_result_with_error_in_a_sub_field_is_still_ok(home, monkeypatch):
    """The denial guard keys on a top-level `error` with no `state` —
    a normal three-state receipt that happens to carry per-row `why`/
    `reason` text is not mistaken for a gate denial."""
    populated = {"state": "populated", "dry_run": False, "examined": 1, "truncated": False,
                 "retired": [], "kept_standing": 0,
                 "kept_in_force": [{"id": "env-1", "why": "still active"}], "unreachable": []}
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["outcome"] == "ok"


def test_narrowed_tool_list_preserves_envelope_retire_sweeps_dry_run_false(home, monkeypatch):
    """Rework of Loki's LOW finding: WILLOW_BOT_STEWARD_TOOLS narrowing
    used to hand every named tool bare {app_id}, silently dropping
    envelope_retire_sweep's dry_run=False."""
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "fleet_health,envelope_retire_sweep")
    calls = heartbeat.curated_calls()
    assert ("envelope_retire_sweep", {"dry_run": False, "app_id": "willow"}) in calls
    assert ("fleet_health", {"app_id": "willow"}) in calls


def test_narrowed_tool_list_still_bare_args_a_tool_this_module_does_not_curate(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "some_other_tool")
    calls = heartbeat.curated_calls()
    assert calls == [("some_other_tool", {"app_id": "willow"})]


def test_retired_rows_never_leak_secrets_only_the_named_receipt_fields(home, monkeypatch):
    """Keys-only guard mirrors test_net_drain_wiring's "rows stay out"
    check: only the fields envelope_retire_sweep's receipt is documented to
    carry (INVARIANTS §1 three-state + the named lists) ride into the
    heartbeat, via result_keys, not the raw result object."""
    populated = {
        "state": "populated", "dry_run": False, "examined": 1,
        "retired": [{"id": "env-1", "verb": "git.push", "reason": "branch_gone"}],
        "kept_standing": 0, "kept_in_force": [], "unreachable": [],
    }
    _heartbeat_with(monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert set(e["result_keys"]) == set(populated.keys())
