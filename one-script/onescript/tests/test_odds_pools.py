"""The odds and the pools (flatten F-14), game-free.

`onescript/odds.py` is the-table's exact-odds forecaster (its world tests stay
with the-table, which imports this); `onescript/pools.py` keeps the shape of a
run's endings. Both read data handed in: no game is needed here.
"""

from __future__ import annotations

import itertools
from fractions import Fraction

from onescript import odds, pools


def test_the_buckets_are_the_36_outcomes_counted():
    for stat in range(-8, 10):
        got = odds.bucket_probs(stat)
        brute = {b: 0 for b in odds.BUCKETS}
        for a, b in itertools.product(range(1, 7), repeat=2):
            brute[odds.bucket(a + b + stat)] += 1
        assert got == {k: Fraction(v, 36) for k, v in brute.items()}


def test_bands_cut_at_the_middle_floor():
    top = Fraction(1, 2)
    assert odds.band_of(top, top) == "likely"
    assert odds.band_of(odds.MIDDLE_FLOOR, top) == "middle"
    assert odds.band_of(odds.MIDDLE_FLOOR - Fraction(1, 1000), top) == "outlier"


def test_hist_puts_every_value_in_one_bin():
    xs = [3, 3, 4, 10, 41, 41, 99]
    h = pools.hist(xs)
    assert sum(b["n"] for b in h) == len(xs) and len(h) <= pools.BINS
    assert h[0]["from"] == 3 and h[-1]["from"] <= 99 <= h[-1]["to"]
    assert pools.hist([5, 5, 5]) == [{"from": 5, "to": 5, "n": 3}]


def _round(ending, winners=(0,), mid=0, steps=10, terminal=True):
    return {"steps": steps, "terminal": terminal, "ending": ending,
            "winners": list(winners), "mid_leader": mid}  # fmt: skip


def test_measure_pools_bands_and_the_funnel():
    rounds = [_round("a") for _ in range(30)] + [
        _round("b", winners=(1,)) for _ in range(9)
    ]
    rounds += [_round("cut", winners=(), mid=None, terminal=False, steps=500)]
    m = pools.measure(rounds, seats=2)
    assert m["rounds"] == 40 and m["cap_hits"] == 1
    assert [p["ending"] for p in m["pools"]] == ["a", "b", "cut"]
    assert [p["band"] for p in m["pools"]] == ["likely", "middle", "outlier"]
    assert m["pools"][0]["p"] == 0.75
    assert m["funnel"] == {"read": 39, "midpoint_leader_wins": round(30 / 39, 4),
                           "chance": 0.5}  # fmt: skip
    assert m["lengths"]["max"] == 500 and m["lengths"]["median"] == 10


def test_no_leader_read_is_no_funnel_not_a_zero():
    m = pools.measure([_round("a", mid=None) for _ in range(5)], seats=4)
    assert m["funnel"] is None


def test_ties_order_by_name_after_count():
    m = pools.measure([_round("z"), _round("a")], seats=1)
    assert [p["ending"] for p in m["pools"]] == ["a", "z"]
