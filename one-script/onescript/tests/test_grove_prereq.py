"""A missing willows-grove is one line naming the path, not 23 tracebacks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from onescript.tests.conftest import grove_absence

ONE_SCRIPT = Path(__file__).resolve().parents[2]


class Item:
    def __init__(self, needs: bool):
        self.needs = needs

    def get_closest_marker(self, name):
        return object() if self.needs and name == "needs_grove" else None


def test_absent_grove_names_the_path_the_variable_and_the_count(tmp_path):
    grove = tmp_path / "willows-grove"
    line = grove_absence([Item(True), Item(True), Item(False)], grove, grove / "c.md")
    assert str(grove) in line and "ONESCRIPT_GROVE" in line
    assert "2 tests" in line and "\n" not in line


def test_present_grove_or_no_dependents_is_silent(tmp_path):
    law = tmp_path / "CONSTITUTION.md"
    law.write_text("x")
    assert grove_absence([Item(True)], tmp_path, law) is None
    assert grove_absence([Item(False)], tmp_path, tmp_path / "missing.md") is None


def test_the_suite_stops_with_that_one_line_and_does_not_skip(tmp_path):
    """End to end, in a child: the gate's last line is the cause."""
    gone = tmp_path / "nowhere"
    env = {"ONESCRIPT_GROVE": str(gone), "PATH": "/usr/bin:/bin"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "onescript/tests/test_incoming.py"],
        cwd=ONE_SCRIPT, env=env, capture_output=True, text=True,
    )  # fmt: skip
    # the gate reads stdout + stderr and keeps the last line (boot._run)
    out = [x for x in (p.stdout + p.stderr).splitlines() if x.strip()]
    assert p.returncode != 0
    assert str(gone) in out[-1] and out[-1].startswith("ERROR: willows-grove"), out
    assert not any("test_" in x for x in out[:-1]), out  # no per-test lines
    assert "skipped" not in p.stdout and "FileNotFoundError" not in p.stdout


def test_tests_that_do_not_need_the_grove_still_run_without_it(tmp_path):
    env = {"ONESCRIPT_GROVE": str(tmp_path / "nowhere"), "PATH": "/usr/bin:/bin"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "onescript/tests/test_incoming.py", "-m", "not needs_grove"],
        cwd=ONE_SCRIPT, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert p.returncode == 0 and "4 passed" in p.stdout, p.stdout
