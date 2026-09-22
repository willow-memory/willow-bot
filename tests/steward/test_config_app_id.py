"""config.app_id() — F1 (Loki 738DB24E): the code default stays what main
had (`willow`), so an already-installed unit (no WILLOW_BOT_MCP_APP_ID
line, WILLOW_HUMAN_ORCHESTRATOR=1 still set) keeps working unchanged after
a merge + pull + restart. The steward becomes its own principal only when
the DEPLOY UNIT explicitly sets WILLOW_BOT_MCP_APP_ID=willow-bot — the gate
lives on the unit's env, never on this default.
"""
from __future__ import annotations

import pytest

from willow_bot.steward import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_MCP_APP_ID", raising=False)
    return tmp_path


def test_env_unset_defaults_to_willow(home):
    assert config.app_id() == "willow"


def test_env_set_to_willow_bot_is_honoured(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "willow-bot")
    assert config.app_id() == config.STEWARD_APP_ID == "willow-bot"


def test_env_set_to_an_arbitrary_value_is_honoured_too(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "some-other-seat")
    assert config.app_id() == "some-other-seat"


def test_steward_app_id_constant_is_willow_bot_regardless_of_the_default():
    # The sender constant used when app_id IS willow-bot must not drift
    # from the default's own value — they can legitimately differ (that is
    # the whole point of F1), but STEWARD_APP_ID itself is fixed.
    assert config.STEWARD_APP_ID == "willow-bot"
