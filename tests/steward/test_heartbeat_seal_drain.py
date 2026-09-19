"""The seal watch runs on the steward tick (sealed decision 72292afd).

Pins: ``seal_drain`` is in the curated heartbeat calls, and its three-state
receipt fields ride into the steward journal so ``bot_status`` can show
whether seals are propagating without anyone opening a second unit's log.
"""
from __future__ import annotations

import json
from pathlib import Path

import willow_bot.steward
from willow_bot.steward import heartbeat


def _stub_client(monkeypatch, call):
    """Replace the MCP client for the duration of a test.

    ``heartbeat.run_heartbeat`` does ``from willow_bot.steward import
    mcp_client``. Once any earlier test has imported the real module, that
    resolves the PACKAGE ATTRIBUTE, not ``sys.modules`` — so a stub in
    ``sys.modules`` alone is bypassed in a full run and the real client blocks
    waiting for a broker (PR #31's five matrix legs each died at ~9 min on
    exactly this). Patch both.
    """
    import sys
    import types

    fake = types.ModuleType("willow_bot.steward.mcp_client")
    fake.call = call
    monkeypatch.setitem(sys.modules, "willow_bot.steward.mcp_client", fake)
    monkeypatch.setattr(willow_bot.steward, "mcp_client", fake, raising=False)
    return fake


def test_seal_drain_is_on_the_tick() -> None:
    names = [name for name, _ in heartbeat.DEFAULT_CURATED]
    assert "seal_drain" in names
    # Beside fleet_health, as the decision says — not instead of it.
    assert "fleet_health" in names


def test_curated_calls_carry_the_seat_app_id(monkeypatch) -> None:
    monkeypatch.delenv("WILLOW_BOT_STEWARD_TOOLS", raising=False)
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "steward-seat")
    calls = dict(heartbeat.curated_calls())
    assert calls["seal_drain"] == {"app_id": "steward-seat"}


def test_receipt_carries_the_drain_three_state(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "w"))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.delenv("WILLOW_BOT_STEWARD_TOOLS", raising=False)

    answers = {
        "fleet_health": {"total": 0, "workers": []},
        "commitment_surface": {"status": "ok", "count": 0},
        "human_required_list": {"count": 0, "items": []},
        "diagnostic_summary": {"verdict": "ok"},
        "seal_drain": {
            "state": "populated", "drained": 3, "malformed": 0,
            "results": {"upgraded": 1, "already": 0, "unmatched": 2, "skipped": 0, "error": 0},
            "upgraded": ["pair-9"], "offset_after": 1234, "ledger": "/x/ledger.jsonl",
        },
    }

    _stub_client(monkeypatch, lambda name, args: answers[name])

    r = heartbeat.run_heartbeat()

    assert r["status"] == "ok"
    drain = next(t for t in r["tools"] if t["tool"] == "seal_drain")
    assert drain["outcome"] == "ok"
    assert drain["state"] == "populated"
    assert drain["drained"] == 3
    assert drain["upgraded"] == ["pair-9"]
    assert drain["results"]["upgraded"] == 1
    assert drain["offset_after"] == 1234
    # Non-receipt fields stay keys-only.
    assert "ledger" not in drain and "ledger" in drain["result_keys"]

    # And it is in the journal the same way.
    line = (tmp_path / "w" / "willow-bot" / "steward_heartbeat.jsonl").read_text().splitlines()[-1]
    assert json.loads(line)["tools"][-1]["state"] == "populated"


def test_unreachable_drain_is_visible_not_collapsed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "w"))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_STEWARD_TOOLS", "seal_drain")

    _stub_client(monkeypatch, lambda name, args: {"state": "unreachable",
                                                  "reason": "ledger_missing",
                                                  "ledger": "/nope"})

    r = heartbeat.run_heartbeat()
    drain = r["tools"][0]
    assert drain["outcome"] == "ok"                     # the call answered
    assert drain["state"] == "unreachable"              # the answer says it could not read
    assert drain["reason"] == "ledger_missing"
