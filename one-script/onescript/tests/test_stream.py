"""The one stream (flatten F1 = SPEC-rival R2).

`onescript/stream.py` is the pile's `sha256-stream/v1`. These tests pin it on
its own (known draws, the beacon's dice now an import of it), hold it to
the-table's `the_table/stream.py` draw for draw, and drive every table game
that takes an `rng_factory` with it: the same seed replays the same game, and
the game played on the pile's stream is the game played on the table's.

The table tests need safe-app-store beside the bot (`ONESCRIPT_SAFE_APP_STORE`
names it otherwise); they skip, said, when it is not there.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from onescript import beacon, stream
from onescript.stream import GENERATOR_ID, Stream, StreamRandom, seed_bytes

SAFE = Path(
    os.environ.get("ONESCRIPT_SAFE_APP_STORE")
    or Path(__file__).resolve().parents[3].parent / "safe-app-store"
).expanduser()
TABLE_APP = SAFE / "apps" / "the-table"

needs_table = pytest.mark.skipif(
    not (TABLE_APP / "the_table" / "stream.py").is_file(),
    reason=f"the-table is not beside the bot at {TABLE_APP}",
)


def _u32(seed: bytes, n: int) -> int:
    return int.from_bytes(
        hashlib.sha256(seed + n.to_bytes(8, "big")).digest()[:4], "big"
    )


# -- the stream, on its own -------------------------------------------------


def test_u32_is_sha256_over_seed_and_an_8_byte_counter():
    s = Stream(b"seed")
    assert [s.u32() for _ in range(5)] == [_u32(b"seed", n) for n in range(5)]
    assert s.n == 5


def test_below_rejects_the_biased_top_instead_of_taking_a_modulo():
    # k = 3 * 2**30 leaves the top quarter of u32 biased: a draw there is
    # skipped and the next taken, where a bare modulo would keep it.
    k = 3 << 30
    seed = next(bytes([i]) for i in range(256) if _u32(bytes([i]), 0) >= k)
    s = Stream(seed)
    first_good = next(n for n in range(1, 64) if _u32(seed, n) < k)
    assert s.below(k) == _u32(seed, first_good) % k
    assert s.n == first_good + 1


@pytest.mark.parametrize("k", [0, -1, (1 << 32) + 1])
def test_below_refuses_an_empty_or_oversized_range(k):
    with pytest.raises(ValueError):
        Stream(b"x").below(k)


def test_seed_bytes():
    assert seed_bytes(0) == b"\x00"
    assert seed_bytes(256) == b"\x01\x00"
    assert seed_bytes("ab01") == b"\xab\x01"
    assert seed_bytes(b"raw") == b"raw"
    for bad in (True, -1, 1.5):
        with pytest.raises((TypeError, ValueError)):
            seed_bytes(bad)


def test_from_seed_appends_the_label():
    assert Stream.from_seed(7, b"q1").seed == b"\x07q1"


def test_streamrandom_draws_through_the_stream():
    r = StreamRandom.from_seed(42)
    s = Stream.from_seed(42)
    assert r.randint(1, 6) == 1 + s.below(6)
    xs = list(range(10))
    r.shuffle(xs)
    assert xs == s.shuffle(list(range(10)))
    seq = "abcdefg"
    assert r.choice(seq) == seq[s.below(len(seq))]
    assert r.random() == s.u32() / 2**32
    assert r.counter == s.n
    assert r.mark() == {
        "seed_id": hashlib.sha256(b"\x2a").hexdigest()[:16],
        "generator_id": GENERATOR_ID,
        "counter": s.n,
    }
    with pytest.raises(ValueError):
        r.randint(2, 1)
    with pytest.raises(IndexError):
        r.choice([])


def test_the_beacons_dice_are_the_one_stream():
    assert beacon.Stream is stream.Stream
    assert beacon.GENERATOR_ID == GENERATOR_ID == "sha256-stream/v1"
    rnd = "ab" * 32
    s = Stream(bytes.fromhex(rnd), 3)
    assert beacon.roll_2d6(rnd, 3) == (1 + s.below(6), 1 + s.below(6), s.n)


# -- the stream against the-table's ----------------------------------------


@pytest.fixture(scope="module")
def table():
    if str(TABLE_APP) not in sys.path:
        sys.path.insert(0, str(TABLE_APP))
    import the_table.stream as ts

    return ts


SEEDS = [0, 1, 255, 256, 2**40 + 7, "ab" * 32, b"raw-bytes"]


def _script(rng) -> list:
    """Every call the games make, interleaved, recorded with the counter."""
    out = []
    for i in range(60):
        out.append(rng.randint(1, 6 + i % 15))
        out.append(rng.choice("abcdefghijklmnopqrstuvwxyz"[: 1 + i % 26]))
        xs = list(range(i % 9))
        rng.shuffle(xs)
        out.append(tuple(xs))
        out.append(rng.random())
        out.append(rng.counter)
    return out


@needs_table
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("label", [b"", b"q7"])
def test_parity_with_the_tables_stream_draw_for_draw(table, seed, label):
    mine, theirs = (
        StreamRandom.from_seed(seed, label),
        table.StreamRandom.from_seed(seed, label),
    )
    assert _script(mine) == _script(theirs)
    assert mine.mark() == theirs.mark()
    assert GENERATOR_ID == table.GENERATOR_ID
    assert seed_bytes(seed) == table.seed_bytes(seed)
    a, b = Stream.from_seed(seed, label), table.Stream.from_seed(seed, label)
    assert [a.die(20) for _ in range(50)] == [b.die(20) for _ in range(50)]
    assert a.shuffle(range(30)) == b.shuffle(range(30))


# -- every table game, through its rng seam --------------------------------

CAP = 3000


def _session(name):
    """The game's class, taking ``rng_factory``. Checkers and backgammon reuse
    game-lab's rules, which import python-chess: skipped, said, without it."""
    if name in ("backgammon", "checkers"):
        pytest.importorskip(
            "chess", reason="game-lab's baseline_core needs python-chess"
        )
    if name == "backgammon":
        from the_table.backgammon_adapter import BackgammonSession as cls
    elif name == "checkers":
        from the_table.checkers_adapter import CheckersSession as cls
    elif name == "crazy_eights":
        from the_table.crazy_eights_adapter import CrazyEightsSession as cls
    elif name == "hearts":
        from the_table.hearts_adapter import HeartsSession as cls
    elif name == "liars_dice":
        from the_table.liars_dice_adapter import LiarsDiceSession as cls
    else:
        from the_table.game_engine_adapter import SceneSession

        return lambda rng_factory: SceneSession(beats=6, rng_factory=rng_factory)
    return cls


#: Games whose deal or dice draw from the seam (checkers takes the seam for
#: the registry's shape; nothing in it is random).
DRAWS = {"backgammon", "crazy_eights", "hearts", "liars_dice", "scene"}


def _play(make, seed, factory):
    made = []

    def counted(s):
        made.append(factory(s))
        return made[-1]

    game = make(rng_factory=counted)
    game.reset(seed)
    mover = StreamRandom.from_seed(seed, b"mover")
    moves = []
    while not game.is_terminal() and len(moves) < CAP:
        seat = game.current_seat()
        move = mover.choice(list(game.legal_moves(seat)))
        game.step(seat, move)
        moves.append(repr(move))
    res = game.result()
    drawn = sum(r.counter for r in made)
    return (moves, res.summary, sorted(res.scores.items()), list(res.winners)), drawn


@needs_table
@pytest.mark.parametrize(
    "name", ["backgammon", "checkers", "crazy_eights", "hearts", "liars_dice", "scene"]
)
def test_each_game_replays_on_the_pile_stream_as_on_the_tables(table, name):
    make = _session(name)
    for seed in range(4):
        pile, drawn = _play(make, seed, StreamRandom.from_seed)
        again, _ = _play(make, seed, StreamRandom.from_seed)
        theirs, _ = _play(make, seed, table.StreamRandom.from_seed)
        assert pile == again, f"{name} seed {seed}: the same seed played twice differed"
        assert pile == theirs, (
            f"{name} seed {seed}: the pile's stream and the table's differ"
        )
        if name in DRAWS:
            assert drawn > 0, f"{name} took the factory but drew nothing from it"


@needs_table
@pytest.mark.parametrize("name", sorted(DRAWS))
def test_the_stream_drives_the_game_not_a_hidden_generator(table, name):
    """Different seeds give different games, and a stream seeded off the
    game's seed changes the game: the seam is load-bearing."""
    make = _session(name)
    games = {repr(_play(make, s, StreamRandom.from_seed)[0]) for s in range(4)}
    assert len(games) > 1
    shifted = _play(make, 0, lambda s: StreamRandom.from_seed(s, b"other"))[0]
    assert shifted != _play(make, 0, StreamRandom.from_seed)[0]
