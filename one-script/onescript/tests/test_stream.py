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


# -- the policy side and the draw (flatten-2 F13) ------------------------------


def test_the_legacy_policy_is_named_and_is_mersenne_twister():
    """D-e: the old generator survives as a name, `random.Random(seed)`, so the
    frozen records can be re-derived."""
    import random

    assert (
        stream.MT19937_POLICY.generator_id
        == stream.POLICY_LEGACY_ID
        == "mt19937-policy/legacy"
    )
    a, b = stream.MT19937_POLICY(7), random.Random(7)
    assert [a.randint(1, 6) for _ in range(20)] == [b.randint(1, 6) for _ in range(20)]
    assert stream.MT19937_POLICY.generator_id != GENERATOR_ID


def test_a_new_policy_defaults_to_the_stream_under_its_own_label():
    r = stream.resolve_policy_factory(None)(5)
    assert isinstance(r, StreamRandom) and r.generator_id == GENERATOR_ID
    # not the game's own draws: a policy and its game, both seeded 5, differ
    game = StreamRandom.from_seed(5)
    assert [r.stream.u32() for _ in range(4)] != [game.stream.u32() for _ in range(4)]
    assert stream.resolve_policy_factory(stream.MT19937_POLICY) is stream.MT19937_POLICY


def test_adapter_rng_defaults_to_the_stream_and_names_the_legacy_one():
    import random

    assert isinstance(stream.adapter_rng(None, 3), StreamRandom)
    assert isinstance(stream.adapter_rng(stream.LEGACY, 3), random.Random)
    assert isinstance(stream.adapter_rng(stream.MT19937_POLICY, 3), random.Random)
    mine = StreamRandom.from_seed(9, b"x")
    assert stream.adapter_rng(lambda s: mine, 3) is mine


def test_a_drawn_seed_replays_the_run():
    """The urandom seed is drawn once; the run from it is the stream's, so the
    same recorded seed gives the same run."""
    seed = stream.draw_seed()
    assert isinstance(seed, int) and 0 <= seed < 1 << 64
    a, b = StreamRandom.from_seed(seed), StreamRandom.from_seed(seed)
    assert [a.randint(1, 20) for _ in range(30)] == [
        b.randint(1, 20) for _ in range(30)
    ]
    assert stream.draw_seed() != seed  # unpredictable: a second draw differs


@needs_table
def test_the_tables_stream_is_this_one(table):
    """F-14/F-16: the-table imports the one script's stream and its table
    side; the names are this module's objects, not copies."""
    from onescript import stream as one

    for name in ("Stream", "StreamRandom", "seed_bytes", "LEGACY", "resolve_factory",
                 "replay", "seed_label", "ReplayRefused", "GeneratorMismatch"):  # fmt: skip
        assert getattr(table, name) is getattr(one, name), name


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


ADAPTERS = {
    "backgammon": ("backgammon_adapter", "BackgammonSession"),
    "checkers": ("checkers_adapter", "CheckersSession"),
    "crazy_eights": ("crazy_eights_adapter", "CrazyEightsSession"),
    "hearts": ("hearts_adapter", "HeartsSession"),
    "liars_dice": ("liars_dice_adapter", "LiarsDiceSession"),
    "scene": ("game_engine_adapter", "SceneSession"),
}


def _session(name):
    """The game's class, taking ``rng_factory``. Checkers and backgammon reuse
    game-lab's rules, which import python-chess; an adapter the checked-out
    the-table does not have yet is skipped, said, not failed."""
    if name in ("backgammon", "checkers"):
        pytest.importorskip(
            "chess", reason="game-lab's baseline_core needs python-chess"
        )
    module, cls = ADAPTERS[name]
    mod = pytest.importorskip(
        f"the_table.{module}", reason=f"this the-table has no {module} yet"
    )
    if name == "scene":
        return lambda rng_factory: mod.SceneSession(beats=6, rng_factory=rng_factory)
    return getattr(mod, cls)


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
