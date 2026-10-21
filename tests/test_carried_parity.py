"""``willow_bot/_carried/`` held to the one script's bytes (flatten-2, gate 6 wheel boundary).

``willow_bot`` ships as a wheel WITHOUT ``one-script/`` and must not import it.
Operator ruling 2026-10-21, "carry copies": the installed package carries
parity-tested copies. willow-bot is the host, so the one script sits in this very
checkout, and each copy is held to it source byte for byte, then value for value.

This FAILS, never skips, when the one script is not beside: a parity test that
skips is no test (the same rule as willow-mcp's ``test_scrub_parity.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from willow_bot._carried import clock as carried_clock
from willow_bot._carried import scrub as carried_scrub

KERNEL = Path(__file__).resolve().parents[1] / "one-script" / "onescript"


def _one(name: str) -> Path:
    p = KERNEL / f"{name}.py"
    if not p.is_file():
        pytest.fail(f"the one script's {name}.py is not beside willow_bot ({p}): parity cannot be skipped")
    return p


@pytest.mark.parametrize("name", ["clock", "scrub"])
def test_the_carried_copy_is_the_one_scripts_bytes(name):
    copy = Path(__file__).resolve().parents[1] / "willow_bot" / "_carried" / f"{name}.py"
    assert copy.read_bytes() == _one(name).read_bytes()


def _load(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_one_{name}", _one(name))
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod  # dataclasses read the module by name
    spec.loader.exec_module(mod)
    return mod


def test_the_clock_formats_agree_on_a_pinned_now():
    one = _load("clock")
    at = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
    for fn in ("seconds_z", "isoformat_utc", "isoformat_seconds_utc"):
        assert getattr(carried_clock, fn)(at) == getattr(one, fn)(at), fn


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.invalid:8443/webhook",
        "https://user:pw@smee.io/AbC123?x=1#frag",
        "http://[::1]:9000/webhook",
        "nope",
        "",
    ],
)
def test_redact_url_agrees(url):
    assert carried_scrub.redact_url(url) == _load("scrub").redact_url(url)
