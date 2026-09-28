"""The autouse home sandbox (tests/conftest.py, gap 9) holds for every test
and fails loudly when a writer would resolve outside tmp_path."""
from __future__ import annotations

import pytest

from willow_bot import paths
from willow_bot.deposits import deposits_dir
from willow_bot.steward import config, tick


def test_every_writer_root_is_under_tmp_path(tmp_path):
    for p in (paths.willow_home(), paths.bot_dir(), deposits_dir(), config.state_path(),
              tick._receipts_path(), tick._ci_offset_path()):
        assert str(p).startswith(str(tmp_path.resolve())), p


def test_a_home_pointed_at_the_box_fails_the_test(monkeypatch, tmp_path, tmp_path_factory):
    # A real, existing directory outside this test's tmp_path: the box rule
    # refuses a missing one before the sandbox guard is even reached.
    elsewhere = tmp_path_factory.mktemp("some-other-box")
    monkeypatch.setenv("WILLOW_HOME", str(elsewhere))
    with pytest.raises(pytest.fail.Exception, match="live steward home"):
        paths.willow_home()
    with pytest.raises(pytest.fail.Exception, match="live steward home"):
        deposits_dir()
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", "/etc/elsewhere.json")
    with pytest.raises(pytest.fail.Exception, match="state_path"):
        config.state_path()
