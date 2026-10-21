"""The one clock (flatten-2 F8): one injectable "now", and every format that was
in use, pinned byte for byte from the helpers it replaced (D-e)."""

from datetime import datetime, timedelta, timezone

import pytest

from onescript import clock

WHEN = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
WHOLE = WHEN.replace(microsecond=0)


def _old_seconds_z(t):  # api._now
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _old_micro_zeroed(t):  # willow-mcp / willow-bot _now_iso, _utc_now
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _old_timespec_z(t):  # Grove journal_writer._now_iso
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.mark.parametrize("t", [WHEN, WHOLE])
def test_seconds_z_is_every_spelling_it_replaced(t):
    got = clock.seconds_z(t)
    assert got == _old_seconds_z(t) == _old_micro_zeroed(t) == _old_timespec_z(t)


def test_golden_strings():
    assert clock.seconds_z(WHEN) == "2026-10-20T12:00:00Z"
    assert clock.isoformat_utc(WHEN) == "2026-10-20T12:00:00.123456+00:00"
    assert clock.isoformat_utc(WHOLE) == "2026-10-20T12:00:00+00:00"
    assert clock.isoformat_seconds_utc(WHEN) == "2026-10-20T12:00:00+00:00"


def test_isoformat_utc_is_isoformat_of_the_utc_instant():
    other = timezone(timedelta(hours=-7))
    t = WHEN.astimezone(other)
    assert clock.isoformat_utc(t) == t.astimezone(timezone.utc).isoformat()


@pytest.mark.parametrize(
    "pin",
    ["2026-10-20T12:00:00.123456Z", "2026-10-20T05:00:00.123456-07:00",
     "2026-10-20T12:00:00.123456", "1792497600.123456"],
)
def test_env_pin_forms_name_the_same_instant(pin):
    got = clock.now(env={clock.ENV_NOW: pin})
    assert got == WHEN and got.tzinfo is not None


def test_pin_through_the_environment_pins_every_format(monkeypatch):
    monkeypatch.setenv(clock.ENV_NOW, "2026-10-20T12:00:00Z")
    assert clock.seconds_z() == "2026-10-20T12:00:00Z"
    assert clock.isoformat_utc() == "2026-10-20T12:00:00+00:00"
    assert clock.isoformat_seconds_utc() == "2026-10-20T12:00:00+00:00"


def test_unset_or_empty_pin_is_the_wall_clock(monkeypatch):
    monkeypatch.setenv(clock.ENV_NOW, "  ")
    before = datetime.now(timezone.utc)
    assert before <= clock.now() <= datetime.now(timezone.utc)
    assert clock.pinned(env={}) is None


@pytest.mark.parametrize("bad", ["yesterday", "2026-13-40T00:00:00Z", "1e99", "Z"])
def test_bad_pin_is_an_error_not_the_real_time(bad):
    with pytest.raises(clock.BadPin):
        clock.now(env={clock.ENV_NOW: bad})
    with pytest.raises(clock.BadPin):
        clock.seconds_z(bad)


def test_an_explicit_at_beats_the_environment(monkeypatch):
    monkeypatch.setenv(clock.ENV_NOW, "2000-01-01T00:00:00Z")
    assert clock.seconds_z(WHEN) == "2026-10-20T12:00:00Z"


def test_the_records_clock_is_the_pinned_one(monkeypatch):
    from onescript import api

    monkeypatch.setenv(clock.ENV_NOW, "2026-10-20T12:00:00Z")
    assert api._now() == "2026-10-20T12:00:00Z"
