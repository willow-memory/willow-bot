"""Steward test guard: no test in this package may reach a real MCP client.

A test that sets WILLOW_BOT_MCP=1 and forgets to patch
``willow_bot.steward.mcp_client.call`` spawns the real stdio client, which
blocks on a server that is not there — two Kart runs hung that way on
2026-09-21 (Loki 095AF9DB). Every step-running test patches the call via
monkeypatch; this autouse fixture is the floor under them: the unpatched
call raises, loudly, naming the fix.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_mcp_client(monkeypatch):
    def _refuse(name, inputs):  # noqa: ARG001 — the signature is the client's
        raise AssertionError(
            f"test reached the real MCP client ({name}); patch "
            "willow_bot.steward.mcp_client.call with a fake before running a step"
        )

    monkeypatch.setattr("willow_bot.steward.mcp_client.call", _refuse)
