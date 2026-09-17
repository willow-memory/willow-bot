"""
tests/test_quips.py — voice mode switching in quips.py

Covers PROPHET_MODE (and its interaction with FRANK_MODE) plus a sanity
check that the normal voice is unaffected when neither is set.
"""
import os
from contextlib import contextmanager

import pytest

import quips

EVENTS = [
    "pr_merged",
    "pr_opened",
    "ci_pass",
    "ci_fail",
    "push_to_main",
    "new_fork",
    "gap_filed",
]


@contextmanager
def env(**kwargs):
    saved = {k: os.environ.get(k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _clean_mode_env():
    # belt and suspenders: make sure no mode env leaks in or out of a test
    with env(FRANK_MODE=None, PROPHET_MODE=None):
        yield


def test_prophet_mode_produces_prophet_lines():
    with env(PROPHET_MODE="1"):
        for event in EVENTS:
            line = quips.pick(event, "someuser")
            assert line.startswith("PROPHET")


def test_frank_wins_when_both_set():
    with env(FRANK_MODE="1", PROPHET_MODE="1"):
        for event in EVENTS:
            line = quips.pick(event, "someuser")
            assert line.startswith("FRANK")
            assert "PROPHET" not in line


def test_neither_mode_set_normal_voice_unchanged():
    quips.load_config()
    cfg = quips._cfg()
    with env(FRANK_MODE=None, PROPHET_MODE=None):
        line = quips.pick("ci_pass", "someuser")
    voice_lines = cfg.get("voice", {}).get("ci_pass", [])
    chaos_lines = cfg.get("chaos_lines", [])
    assert line in voice_lines or line in chaos_lines


def test_prophet_unknown_event_returns_fallback_no_crash():
    with env(PROPHET_MODE="1"):
        line = quips.pick("something_totally_unknown", "someuser")
        assert line.startswith("PROPHET")
        assert "something_totally_unknown" in line


def test_frank_unknown_event_returns_fallback_no_crash():
    with env(FRANK_MODE="1"):
        line = quips.pick("something_totally_unknown", "someuser")
        assert line.startswith("FRANK")
        assert "something_totally_unknown" in line
