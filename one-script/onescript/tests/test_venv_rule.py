"""One venv rule (flatten F-17): the one script finds the bot's venv by the
bot's own box rule (`willow_bot.paths`), and with no box it names no venv
and the toolchain gate says why; never a guessed `~`."""

from __future__ import annotations

import sys
from pathlib import Path

from onescript import __main__ as cli
from onescript import api, boot


def test_the_rule_is_the_bots(monkeypatch, tmp_path):
    paths = api.bot_paths()
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    assert cli._bot_venv() == paths.venvs_dir() / "willow-bot"
    (tmp_path / "venvs" / "willow-bot").mkdir(parents=True)
    assert (
        cli._bot_venv() == paths.legacy_venvs_dir() / "willow-bot"
    )  # only the old one


def test_no_box_is_no_venv_never_home(monkeypatch, tmp_path):
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "willow-bot" / "venvs" / "willow-bot").mkdir(parents=True)
    assert cli._bot_venv() is None


def test_the_gate_with_no_venv_says_why_and_finds_no_tool():
    g = api._gate_cfg(True, "pip install ruff==0.16.7", None, Path("/r"), Path("/g"))
    assert g["version_of"]("ruff") is None
    assert api.bot_paths().BOX_NOT_CONFIGURED in g["found_in"]


def test_a_checkin_config_with_no_venv_does_not_raise_and_says_why(monkeypatch):
    monkeypatch.delenv(api.NESTED, raising=False)
    g = api._gate_cfg(False, "", None, Path("/r"), Path("/g"))
    (suite,) = g["tests"]
    assert suite["argv"][0] == sys.executable and "python" not in suite
    assert api.bot_paths().BOX_NOT_CONFIGURED in suite["ran_on"]
    (row,) = boot.tests_gate([{**suite, "needs": [], "cwd": "/nope"}])
    assert api.bot_paths().BOX_NOT_CONFIGURED in row["why"]
