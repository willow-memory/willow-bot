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
    """Rework of gap b7a4ccdc8bbb: `retired`/`kept_in_force` no longer ride
    the heartbeat verbatim (a live receipt measured ~40 KB in one row,
    bigger than the tail reader's window) — only counts + a bounded
    `retired` id sample land in the heartbeat entry; the full receipt goes
    to its own file every tick regardless of size."""
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
    assert "retired" not in e and "kept_in_force" not in e
    assert e["retired_count"] == 1
    assert e["retired_sample"] == ["env-1"]
    assert e["kept_in_force_count"] == 1
    assert e["dry_run"] is False


def test_heartbeat_writes_the_full_receipt_to_its_own_file(home, monkeypatch):
    """Gap b7a4ccdc8bbb: the full receipt (every row, every `why`) is
    appended whole to steward_heartbeat_receipts.jsonl every tick,
    unconditionally — not just when it happens to be large — and the
    heartbeat entry references it."""
    import json

    populated = {
        "state": "populated", "dry_run": False, "examined": 1, "truncated": False,
        "retired": [{"id": "env-1", "verb": "git.push", "reason": "branch_gone"}],
        "kept_standing": 0,
        "kept_in_force": [{"id": "env-2", "verb": "pr.open", "why": "master still exists"}],
        "unreachable": [],
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    r = heartbeat.run_heartbeat()
    e = _tool_entry(r, "envelope_retire_sweep")
    assert e["full_receipt"] == {"file": "steward_heartbeat_receipts.jsonl", "at": r["at"]}

    full_path = heartbeat._full_receipts_path()
    assert full_path.name == "steward_heartbeat_receipts.jsonl"
    rows = [json.loads(line) for line in full_path.read_text(encoding="utf-8").splitlines()]
    match = next(row for row in rows if row["tool"] == "envelope_retire_sweep")
    assert match["at"] == r["at"]
    assert match["result"] == populated  # the WHOLE receipt, untouched


def test_retired_sample_is_bounded_regardless_of_list_size(home, monkeypatch):
    rows = [{"id": f"env-{i:03d}", "verb": "git.push", "reason": "branch_gone"}
            for i in range(187)]
    populated = {
        "state": "populated", "dry_run": False, "examined": 187, "truncated": False,
        "retired": rows, "kept_standing": 0, "kept_in_force": [], "unreachable": [],
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["retired_count"] == 187
    assert e["retired_sample"] == [f"env-{i:03d}" for i in range(heartbeat._RETIRED_SAMPLE_N)]


def test_retired_sample_is_sorted_by_id_not_iteration_order(home, monkeypatch):
    """Rework of Loki's LOW finding on 7C899577: the first cut sampled
    `retired[:N]` in whatever order the cursor-rotated sweep happened to
    examine rows that tick, so two ticks at the same overall state could
    show two different samples. Deliberately out-of-order input here."""
    rows = [{"id": "env-c", "verb": "git.push", "reason": "branch_gone"},
            {"id": "env-a", "verb": "git.push", "reason": "branch_gone"},
            {"id": "env-b", "verb": "git.push", "reason": "branch_gone"}]
    populated = {
        "state": "populated", "dry_run": False, "examined": 3, "truncated": False,
        "retired": rows, "kept_standing": 0, "kept_in_force": [], "unreachable": [],
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["retired_sample"] == ["env-a", "env-b", "env-c"]


def test_retired_sample_skips_rows_with_no_id(home, monkeypatch):
    rows = [{"id": "env-b", "verb": "git.push", "reason": "branch_gone"},
            {"verb": "git.push", "reason": "branch_gone"}]  # malformed, no id
    populated = {
        "state": "populated", "dry_run": False, "examined": 2, "truncated": False,
        "retired": rows, "kept_standing": 0, "kept_in_force": [], "unreachable": [],
    }
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    e = _tool_entry(heartbeat.run_heartbeat(), "envelope_retire_sweep")
    assert e["retired_sample"] == ["env-b"]
    assert e["retired_count"] == 2  # count is unaffected — every row counted


# --------------------------------------------------------------------------
# Retention: a size cap with one rotated backup (rework of Loki's MEDIUM
# finding on 7C899577 — the receipts file was append-only, no cap, 60-200
# MB/week measured)
# --------------------------------------------------------------------------

def test_full_receipts_file_rotates_once_the_cap_is_reached(home, monkeypatch):
    import json

    # Narrowed to one tool so each tick writes exactly one row to the
    # receipts file — otherwise every curated tool's own append would
    # each independently check/trigger rotation within the same tick.
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "envelope_retire_sweep")
    monkeypatch.setattr(heartbeat, "_FULL_RECEIPTS_MAX_BYTES", 500)  # tiny cap for a fast test
    populated = {"state": "populated", "dry_run": False, "examined": 0, "truncated": False,
                 "retired": [], "kept_standing": 0, "kept_in_force": [], "unreachable": [],
                 "pad": "x" * 400}
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    heartbeat.run_heartbeat()  # first tick: writes past the 500-byte cap
    full_path = heartbeat._full_receipts_path()
    backup_path = full_path.with_name(full_path.name + ".1")
    assert not backup_path.exists()  # nothing to rotate yet on the first write

    heartbeat.run_heartbeat()  # second tick: sees the cap already exceeded, rotates first
    assert backup_path.exists()
    # the live file holds only this tick's row, not both
    rows = [json.loads(line) for line in full_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    backup_rows = [json.loads(line) for line in backup_path.read_text(encoding="utf-8").splitlines()]
    assert len(backup_rows) == 1  # the first tick's row, preserved in the backup


def test_full_receipts_file_rotation_keeps_only_one_backup_generation(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "envelope_retire_sweep")
    monkeypatch.setattr(heartbeat, "_FULL_RECEIPTS_MAX_BYTES", 500)
    populated = {"state": "populated", "dry_run": False, "examined": 0, "truncated": False,
                 "retired": [], "kept_standing": 0, "kept_in_force": [], "unreachable": [],
                 "pad": "x" * 400}
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    for _ in range(4):
        heartbeat.run_heartbeat()
    full_path = heartbeat._full_receipts_path()
    backup_path = full_path.with_name(full_path.name + ".1")
    assert backup_path.exists()
    assert not full_path.with_name(full_path.name + ".2").exists()


def test_full_receipts_file_stays_under_cap_growth_is_bounded(home, monkeypatch):
    """The point of the cap: disk usage for this file has a ceiling, not
    just a slower rate of growth."""
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "envelope_retire_sweep")
    monkeypatch.setattr(heartbeat, "_FULL_RECEIPTS_MAX_BYTES", 2000)
    populated = {"state": "populated", "dry_run": False, "examined": 0, "truncated": False,
                 "retired": [], "kept_standing": 0, "kept_in_force": [], "unreachable": [],
                 "pad": "x" * 300}
    _heartbeat_with(
        monkeypatch, lambda n: populated if n == "envelope_retire_sweep" else {"ok": True}
    )
    for _ in range(50):
        heartbeat.run_heartbeat()
    full_path = heartbeat._full_receipts_path()
    backup_path = full_path.with_name(full_path.name + ".1")
    total = full_path.stat().st_size + (backup_path.stat().st_size if backup_path.exists() else 0)
    # Two generations at roughly the cap each -- nowhere near 50 ticks'
    # worth of unbounded growth.
    assert total < heartbeat._FULL_RECEIPTS_MAX_BYTES * 3


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
    assert e["state"] == "empty" and e["examined"] == 0
    assert e["retired_count"] == 0 and e["retired_sample"] == []


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
