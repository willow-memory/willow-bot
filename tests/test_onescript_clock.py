"""The deterministic runner stamps rows from the one script's clock (flatten-2
F8; the operator: "Using the onescript at every path."). With
``ONESCRIPT_NOW`` pinned, ``_utc_now`` is the pinned instant in the named
``seconds_z`` format (``2026-10-20T12:00:00Z``), the bytes the old
``replace(microsecond=0).isoformat().replace("+00:00", "Z")`` wrote.

``willow_bot/onescript_reach.py`` is a byte copy of the one script's
``reach_bootstrap.py`` (willow-bot is the host, so the template sits in this
very checkout and the parity test always runs).
"""

from pathlib import Path

from willow_bot import onescript_reach
from willow_bot.deterministic import runner

TEMPLATE = Path(__file__).resolve().parents[1] / "one-script" / "onescript" / "reach_bootstrap.py"


def test_the_copy_is_the_template_byte_for_byte():
    assert Path(onescript_reach.__file__).read_bytes() == TEMPLATE.read_bytes()


def test_utc_now_is_the_pinned_instant_in_seconds_z(monkeypatch):
    monkeypatch.setenv("ONESCRIPT_NOW", "2026-10-20T12:00:00.123456Z")
    monkeypatch.setenv("ONESCRIPT_HOME", str(TEMPLATE.parents[1]))
    assert runner._utc_now() == "2026-10-20T12:00:00Z"
