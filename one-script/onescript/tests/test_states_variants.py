"""F9: the three states, once; ranked/held/none stay as named variants (D-e)."""

from __future__ import annotations

import re
from pathlib import Path

from onescript import boot, jeles_store, states, writeback

PKG = Path(states.__file__).parent

#: Modules that restated a three-state word as a constant before F9.
RESTATERS = ("boot", "jeles_store", "writeback")


def test_variants_map_onto_the_three_and_the_row_words_are_unchanged():
    assert (states.RANKED, states.HELD, states.NONE) == ("ranked", "held", "none")
    assert states.VARIANTS == {
        "ranked": "populated",
        "held": "populated",
        "none": "empty",
    }
    for w in states.STATES:
        assert states.base_of(w) == w
    assert states.base_of("ranked") == states.POPULATED
    assert states.base_of("held") == states.POPULATED
    assert states.base_of("none") == states.EMPTY
    assert states.base_of("loading") == states.UNREACHABLE  # not a state (D-f)
    assert states.base_of(None) == states.UNREACHABLE


def test_modules_reuse_the_constants_not_restate_them():
    assert boot.UNREACHABLE is states.UNREACHABLE
    assert jeles_store.UNREACHABLE is states.UNREACHABLE
    assert jeles_store.EMPTY is states.EMPTY
    assert jeles_store.RANKED is states.RANKED
    assert writeback.HELD is states.HELD
    assert writeback.NONE is states.NONE
    for name in RESTATERS:
        src = (PKG / f"{name}.py").read_text()
        assert not re.search(
            r'^[A-Z_, ]+= *"(unreachable|empty|ranked|held|none)"', src, re.M
        ), name


def test_old_rows_read_the_same():
    # persisted/served row words, as written before F9
    assert jeles_store.UNREACHABLE == "unreachable"
    assert jeles_store.EMPTY == "empty"
    assert jeles_store.RANKED == "ranked"
    assert (writeback.HELD, writeback.NONE) == ("held", "none")
    assert (
        states.checked("ranked") == states.UNREACHABLE
    )  # unchanged: checked() is strict
