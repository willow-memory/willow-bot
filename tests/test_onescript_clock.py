"""The deterministic runner stamps rows in the one script's ``seconds_z`` format
(flatten-2 F8; the operator: "Using the onescript at every path."). With
``ONESCRIPT_NOW`` pinned, ``_utc_now`` is the pinned instant in the named
``seconds_z`` format (``2026-10-20T12:00:00Z``), the bytes the old
``replace(microsecond=0).isoformat().replace("+00:00", "Z")`` wrote.

The installed ``willow_bot`` ships without the one script, so it carries a
parity-tested byte copy of ``onescript/clock.py`` (``willow_bot/_carried/``;
operator: "carry copies"); ``tests/test_carried_parity.py`` holds it to the bytes.
"""

import os
import subprocess
import sys
from pathlib import Path

from willow_bot.deterministic import runner

ROOT = Path(__file__).resolve().parents[1]


def test_utc_now_is_the_pinned_instant_in_seconds_z(monkeypatch):
    monkeypatch.setenv("ONESCRIPT_NOW", "2026-10-20T12:00:00.123456Z")
    assert runner._utc_now() == "2026-10-20T12:00:00Z"


def test_utc_now_reaches_nothing_of_the_one_script():
    """Gate 5, absence: with ONESCRIPT_HOME pointing nowhere the clock still
    answers from its carried copy and no ``onescript*`` module was imported."""
    code = (
        "import sys\n"
        "from willow_bot.deterministic import runner\n"
        "print(runner._utc_now())\n"
        "print([m for m in sys.modules if m.split('.')[0].startswith('onescript')])\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "ONESCRIPT_HOME": "/nonexistent/no-one-script",
        "ONESCRIPT_NOW": "2026-10-20T12:00:00Z",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    p = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd="/", check=False
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ["2026-10-20T12:00:00Z", "[]"]
