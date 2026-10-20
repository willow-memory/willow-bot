"""The three states, once (flatten F5, F-18)."""

from __future__ import annotations

from onescript import box_lookup, states


def test_three_and_never_collapsed():
    assert states.STATES == ("populated", "empty", "unreachable")
    assert len(set(states.STATES)) == 3


def test_no_rows_is_empty_and_no_reader_is_unreachable():
    assert states.state_of([1]) == states.POPULATED
    assert states.state_of([]) == states.EMPTY
    assert states.state_of(None) == states.UNREACHABLE


def test_a_state_nobody_can_name_reads_unreachable():
    for s in states.STATES:
        assert states.checked(s) == s
    for bad in ("", "ok", None, "Populated", 0):
        assert states.checked(bad) == states.UNREACHABLE


def test_the_box_lookup_uses_these():
    assert box_lookup.POPULATED is states.POPULATED
    assert box_lookup.UNREACHABLE is states.UNREACHABLE
