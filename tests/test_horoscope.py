"""
tests/test_horoscope.py — determinism and shape checks for horoscope.reading().
"""
from __future__ import annotations

from datetime import datetime, timezone

import horoscope


def test_deterministic_same_login_same_date():
    a = horoscope.reading("alice", date="2026-09-17")
    b = horoscope.reading("alice", date="2026-09-17")
    assert a == b


def test_different_date_usually_differs():
    base = horoscope.reading("alice", date="2026-09-17")
    diffs = sum(
        horoscope.reading("alice", date=f"2026-09-{day:02d}") != base
        for day in range(1, 29)
    )
    assert diffs > 0


def test_different_login_usually_differs():
    base = horoscope.reading("alice", date="2026-09-17")
    logins = ["bob", "carol", "dave", "erin", "frank", "grace", "heidi"]
    diffs = sum(horoscope.reading(login, date="2026-09-17") != base for login in logins)
    assert diffs > 0


def test_date_none_uses_today_utc():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert horoscope.reading("alice") == horoscope.reading("alice", date=today)


def test_title_kwarg_formats():
    line = horoscope.reading("alice", date="2026-09-17", title="karl")
    assert line.startswith("_Today, Karl:")
    assert line.endswith("._")


def test_no_title_formats():
    line = horoscope.reading("alice", date="2026-09-17")
    assert line.startswith("_Today:")
    assert line.endswith("._")
