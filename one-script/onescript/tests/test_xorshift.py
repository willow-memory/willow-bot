"""The second generator (flatten F-15): `onescript/xorshift.py`, held draw for
draw to the copies catgrid and bureau keep for their JS ports.

The game tests need safe-app-store beside the bot (`ONESCRIPT_SAFE_APP_STORE`
names it otherwise); they skip, said, when it is not there.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from onescript.xorshift import GENERATOR_ID, GOLDEN, M32, Xorshift32

SAFE = Path(
    os.environ.get("ONESCRIPT_SAFE_APP_STORE")
    or Path(__file__).resolve().parents[3].parent / "safe-app-store"
).expanduser()
GAMES = {
    "catgrid": SAFE / "apps" / "catgrid" / "catgrid" / "rng.py",
    "bureau": SAFE / "apps" / "bureau" / "bureau" / "rng.py",
}


def _load(name: str):
    path = GAMES[name]
    if not path.is_file():
        pytest.skip(f"{name}'s rng.py is not beside the bot at {path}")
    spec = importlib.util.spec_from_file_location(f"_{name}_rng", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _ref(seed: int, n: int) -> list[int]:
    """xorshift32 written out once more, from its definition."""
    s = (seed * 2654435761 + 1) & M32 or GOLDEN
    out = []
    for _ in range(n):
        s ^= (s << 13) & M32
        s ^= s >> 17
        s ^= (s << 5) & M32
        s &= M32
        out.append(s)
    return out


def test_it_is_xorshift32_with_the_games_seed_mix():
    for seed in (0, 1, 7, 2**31, 2**32 - 1, 123456789):
        r = Xorshift32(seed)
        assert [r.u32() for _ in range(40)] == _ref(seed, 40)
    assert GENERATOR_ID == "xorshift32/v1" == Xorshift32.generator_id


def test_a_seed_that_mixes_to_zero_starts_at_golden():
    # (seed * 2654435761 + 1) % 2**32 == 0 -> the dead state is replaced
    seed = (-pow(2654435761, -1, 2**32)) % 2**32  # solves seed * mix == -1 mod 2**32
    assert (seed * 2654435761 + 1) & M32 == 0
    assert Xorshift32(seed).s == GOLDEN


def test_below_between_pick_shuffled():
    r, ref = Xorshift32(5), _ref(5, 6)
    assert r.below(10) == ref[0] % 10
    assert r.between(3, 9) == 3 + ref[1] % 6
    assert r.pick("abcde") == "abcde"[ref[2] % 5]
    out = list(range(4))
    for i, x in zip(range(3, 0, -1), ref[3:6]):
        j = x % (i + 1)
        out[i], out[j] = out[j], out[i]
    assert r.shuffled(range(4)) == out


@pytest.mark.parametrize("game", sorted(GAMES))
def test_parity_with_the_games_copy_draw_for_draw(game):
    rng = _load(game)
    assert (rng.M32, rng.GOLDEN) == (M32, GOLDEN)
    for seed in (0, 1, 42, 2**31 + 3, 2**32 - 1):
        mine, theirs = Xorshift32(seed), rng.Rng(seed)
        assert mine.s == theirs.s
        assert [mine.u32() for _ in range(200)] == [theirs.u32() for _ in range(200)]
        assert [mine.below(13) for _ in range(50)] == [
            theirs.below(13) for _ in range(50)
        ]
        assert mine.pick("abcdefg") == theirs.pick("abcdefg")
        if hasattr(theirs, "shuffled"):
            assert mine.shuffled(range(30)) == theirs.shuffled(range(30))
        if hasattr(theirs, "between"):
            assert [mine.between(4, 11) for _ in range(30)] == [
                theirs.between(4, 11) for _ in range(30)
            ]
