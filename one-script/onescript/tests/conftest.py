"""Tests that read the willows-grove checkout say so, and say it once.

The one script's law is willows-grove's (its constitution, its CI). Most of
this suite builds its own grove in a tmp dir; a few tests read the real one,
found the way the bot finds it (`ONESCRIPT_GROVE`, else `../willows-grove`
beside willow-bot, see `onescript.__main__.GROVE`). Those carry
`@pytest.mark.needs_grove`.

When the grove is genuinely absent, running them can only fail, and 23
FileNotFoundErrors read like broken code. So the run stops before the first
test with one line naming the path and the variable. It does not skip, and it
does not pass: the tests gate stays failing, with the cause as its last line.
Selecting only tests that don't need the grove still runs them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402


def grove_absence(items, grove: Path = None, law: Path = None) -> str | None:
    """The one line, or None when nothing selected needs the grove or it is there."""
    grove = cli.GROVE if grove is None else grove
    law = cli.CONSTITUTION if law is None else law
    need = [i for i in items if i.get_closest_marker("needs_grove")]
    if not need or law.is_file():
        return None
    return (
        f"willows-grove not found at {grove} (no governance/CONSTITUTION.md);"
        f" {len(need)} tests read it; set ONESCRIPT_GROVE or put the checkout"
        " beside willow-bot"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_grove: reads the willows-grove checkout; see conftest.py"
    )


@pytest.hookimpl(trylast=True)  # after -m/-k deselect: judge what will run
def pytest_collection_modifyitems(config, items):
    line = grove_absence(items)
    if line:
        raise pytest.UsageError(line)


@pytest.fixture(autouse=True)
def _no_sibling_index():
    """The CLI's default repo set is every clone beside willow-bot, and a
    check-in with no index builds one over it. A test box must never index the
    machine's real clones: the CLI sees no siblings here unless a test says so.
    Its own MonkeyPatch, so a test's `monkeypatch.undo()` can't lift it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli, "_siblings", lambda parent: {})
        yield


#: The operator's Nestor store, as the CLI finds it. A check-out proposes the
#: session to it, so a suite run on the operator's box (the tests gate's child
#: inherits their environment) would fill their queue with test sessions.
NESTOR_ENV = ("WILLOW_NESTOR_DB", "NESTOR_DB")


@pytest.fixture(autouse=True)
def _no_operator_nestor():
    """No test sees the operator's Nestor store; one that needs a store names
    its own (`monkeypatch.setenv` in the test, or `Config.nestor_db`). Its own
    MonkeyPatch, so a test's `monkeypatch.undo()` can't bring the store back."""
    with pytest.MonkeyPatch.context() as mp:
        for name in NESTOR_ENV:
            mp.delenv(name, raising=False)
        yield
