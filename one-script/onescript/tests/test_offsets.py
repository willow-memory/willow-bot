"""compiled/offsets.py: the Discord group's two asymmetric-offset solutions, the
bound every "only one" carries, and why the primes have to line up."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "compiled" / "offsets.py"
spec = importlib.util.spec_from_file_location("offsets", SCRIPT)
off = importlib.util.module_from_spec(spec)
spec.loader.exec_module(off)


def test_the_two_claims_hold_as_far_as_this_looks():
    assert off.solve(3, 2, 300, 40) == [(6, 9, 18, 5)]
    assert off.solve(8, 5, 300, 40) == [(20, 5, 80, 3)]
    assert 3 * 6**9 == 16 * 18**5 == 30_233_088
    assert 12 * 20**5 == 75 * 80**3 == 38_400_000


def test_only_one_within_a_bound_is_not_only_one():
    small = off.solve(9, 2, 100, 20)
    assert small == [(12, 3, 18, 2)]
    assert (54, 22, 162, 17) in off.solve(9, 2, 100, 41)  # 40 digits


def test_every_prime_lines_up_on_both_sides():
    v = off.valuations(3, 2, 6, 9, 18, 5)
    assert v == {2: (9, 9), 3: (10, 10)}
    assert off.valuations(8, 5, 20, 5, 80, 3) == {2: (12, 12), 3: (1, 1), 5: (5, 5)}
    assert off.digit_and_zeros(16, 18, 5) == "(16)00000 base 18"
