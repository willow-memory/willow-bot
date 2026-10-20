"""A day's round and seed (flatten F-14): catgrid's daily board came into the
beacon. The board stays catgrid's; the round, the u32 seed, the stamp and its
check are here. No network: the reader is a stand-in."""

from __future__ import annotations

import hashlib

import pytest

from onescript import beacon

R1 = "a" * 64


def reader(randomness=R1, off=0):
    def read(rnd):
        return {"round": rnd + off, "randomness": randomness}

    return read


def test_a_days_round_is_the_round_current_at_its_midnight():
    assert beacon.round_for("2026-10-19") == beacon.round_at("2026-10-19T00:00:00Z")
    assert (
        beacon.round_for("2026-10-20") - beacon.round_for("2026-10-19") == 86400 // 30
    )
    with pytest.raises(ValueError):
        beacon.round_for("2019-01-01")
    with pytest.raises(ValueError):
        beacon.round_for("not a day")


def test_the_seed_is_shas_first_four_bytes():
    want = int.from_bytes(hashlib.sha256(bytes.fromhex(R1)).digest()[:4], "big")
    assert beacon.seed_u32(R1) == want and 0 <= want < 2**32


def test_a_stamp_holds_together_and_says_bls_is_unchecked():
    st, why = beacon.day_stamp("2026-10-19", reader())
    assert why == "" and beacon.check_day_stamp(st) == ""
    assert st["round"] == beacon.round_for("2026-10-19")
    assert st["seed"] == beacon.seed_u32(R1) and st["seed_id"] == beacon.seed_id(R1)
    assert "not checked" in st["bls"]


@pytest.mark.parametrize(
    "read, said",
    [
        (reader(off=1), "answered for"),
        (reader(randomness="xyz"), "no randomness"),
        (lambda rnd: (_ for _ in ()).throw(OSError("down")), "could not be read"),
    ],
)
def test_no_stamp_when_the_beacon_cannot_say(read, said):
    st, why = beacon.day_stamp("2026-10-19", read)
    assert st is None and said in why


@pytest.mark.parametrize(
    "edit, said",
    [
        ({"round": 1}, "is not"),
        ({"seed": 7}, "not seeded by the beacon"),
        ({"seed_id": "0" * 16}, "not seeded by the beacon"),
        ({"randomness": "q"}, "no randomness"),
    ],
)
def test_a_stamp_that_does_not_hold_together_says_why(edit, said):
    st, _ = beacon.day_stamp("2026-10-19", reader())
    assert said in beacon.check_day_stamp({**st, **edit})
    assert "not a daily stamp" in beacon.check_day_stamp({"day": "2026-10-19"})
