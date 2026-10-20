"""The dice (flatten F-14): `onescript/dice.py` swaps an engine's `random` for
one roll and records it; `onescript/dice5e.py` rolls 5e notation on a
generator handed in. Tested with a stand-in engine, so no game is needed;
the-table's own suite drives both on its real engine.
"""

from __future__ import annotations

import importlib.util
import random
import sys

import pytest

from onescript import dice
from onescript.stream import EngineMismatch, ReplayRefused, StreamRandom

ENGINE_SRC = """
import random


class Engine:
    def __init__(self):
        self.stats = {"Grit": 1}

    def roll(self, stat):
        total = random.randint(1, 6) + random.randint(1, 6) + self.stats[stat]
        return total, ("strong" if total >= 10 else "weak" if total >= 7 else "miss")
"""


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "toy_engine.py"
    path.write_text(ENGINE_SRC)
    spec = importlib.util.spec_from_file_location("toy_engine", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    sys.modules["toy_engine"] = mod
    yield mod.Engine
    sys.modules.pop("toy_engine", None)


def test_a_roll_draws_from_the_stream_and_the_engine_gets_its_random_back(engine):
    g = engine.roll.__globals__
    before = g["random"]
    rng = StreamRandom.from_seed(7)
    result, status, rec = dice.roll(engine(), "Grit", rng, 1)
    s = StreamRandom.from_seed(7)
    assert result == s.randint(1, 6) + s.randint(1, 6) + 1
    assert g["random"] is before is random
    assert rec["counter_before"] == 0 and rec["counter"] == rng.counter == 2
    assert rec["generator_id"] == "sha256-stream/v1" and rec["engine_hash"]
    assert rec["seed_id"] == rng.seed_id and "seed" not in rec


def test_a_recorded_roll_replays_and_a_wrong_one_is_refused(engine):
    rng = StreamRandom.from_seed("ab" * 32)
    _, _, rec = dice.roll(engine(), "Grit", rng, 1)
    assert dice.replay_roll("ab" * 32, rec, engine, sheet={"Grit": 1}).counter == 2
    with pytest.raises(ReplayRefused):
        dice.replay_roll("ab" * 32, {**rec, "result": rec["result"] + 1}, engine,
                         sheet={"Grit": 1})  # fmt: skip
    with pytest.raises(ReplayRefused):
        dice.replay_roll("ab" * 32, rec, engine)  # no sheet: the modifier is unbound
    with pytest.raises(EngineMismatch):
        dice.replay_roll("ab" * 32, {**rec, "engine_hash": "0" * 64}, engine,
                         sheet={"Grit": 1})  # fmt: skip


def test_a_swap_changed_under_the_roll_is_refused_and_put_back(engine):
    g = engine.roll.__globals__
    with pytest.raises(RuntimeError, match="changed under rolled_with"):
        with dice.rolled_with(StreamRandom.from_seed(1), engine):
            g["random"] = object()
    assert g["random"] is random


def test_a_rolls_own_error_is_kept_with_the_swap_mismatch_noted(engine):
    g = engine.roll.__globals__
    with pytest.raises(KeyError) as e:
        with dice.rolled_with(StreamRandom.from_seed(1), engine):
            g["random"] = object()
            raise KeyError("the roll's own")
    assert isinstance(e.value.swap_mismatch, RuntimeError)
    assert "changed under rolled_with" in "".join(e.value.__notes__)
    assert g["random"] is random


def test_the_engine_hash_moves_with_its_source(engine, tmp_path):
    h = dice.engine_hash(engine)
    (tmp_path / "toy_engine.py").write_text(ENGINE_SRC + "\n# changed\n")
    assert dice.engine_hash(engine) != h


def test_5e_dice_roll_on_the_generator_handed_in():
    dice5e = pytest.importorskip(
        "onescript.dice5e", reason="the dice library is not installed"
    )
    a = [dice5e.total("2d20h1+3", StreamRandom.from_seed(9)) for _ in range(3)]
    assert len(set(a)) == 1 and 4 <= a[0] <= 23
    total, nat = dice5e.d20(StreamRandom.from_seed(9), 2, adv=True)
    assert 1 <= nat <= 20 and total == nat + 2
    assert dice5e.crit_expr("1d10+3") == "2d10+3"
