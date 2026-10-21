"""stream.py -- the one seeded stream, in the pile (flatten F1 = SPEC-rival R2).

``sha256-stream/v1``: SHA-256 over ``seed || counter`` (counter as 8 bytes
big-endian), the first four bytes big-endian as a u32; ``below`` by
rejection, no modulo bias; ``shuffle`` a Fisher-Yates from the end. The same
on every Python, which ``random.Random`` is not guaranteed to be.

This is the pile's home for it (willows-grove
``docs/design/one-script/flatten-2026-10-19.md`` §3, F1). It was three
copies: the-table's ``the_table/stream.py``, this package's ``beacon.Stream``
(now an import of this one) and ``play.py`` in the frozen predictions
experiment. ``tests/test_stream.py`` holds this one to the-table's draw for
draw while the-table is beside the bot; the table's copy retires when the
game pieces move in (F4). No game's default changes here: until F4 a game
runs on this stream only when a caller hands it ``rng_factory``.

``StreamRandom`` is the ``random.Random``-shaped surface every rng seam takes
(``randint``, ``choice``, in-place ``shuffle``, ``random``), with
``counter``, ``seed_id`` and ``generator_id`` so a draw can be recorded and
replayed.

The table's side came in with flatten F-14 (from the-table's
``the_table/stream.py``, which now imports it): ``LEGACY``, the opt-out to the
generator a recorded run was made with (C9); ``resolve_factory`` (None -> the
stream); ``seed_label``; and ``replay``, which refuses a record made by any
other generator or seed.

Flatten-2 F13 (randomness F-14 missed): the games' policies and the adapters'
fallbacks were still ``random.Random(seed)``. New runs now default to the
stream (``policy_factory``, ``adapter_rng``); the old Mersenne path is the
named ``MT19937_POLICY`` (``mt19937-policy/legacy``), used only to re-derive
the frozen records, which stay frozen (rule 12). ``draw_seed`` is where an
unpredictable seed comes from: drawn once, recorded, then the stream.
"""

from __future__ import annotations

import hashlib
import os
import random
from collections.abc import Sequence
from typing import Any

GENERATOR_ID = "sha256-stream/v1"


def seed_bytes(seed: int | bytes | str) -> bytes:
    """int -> big-endian minimal bytes (0 -> b"\\x00"); str -> bytes.fromhex
    (a drand ``randomness``); bytes as given."""
    if isinstance(seed, bool):
        raise TypeError("seed must be int, bytes or hex str, not bool")
    if isinstance(seed, int):
        if seed < 0:
            raise ValueError("an int seed must be >= 0")
        return seed.to_bytes(max(1, (seed.bit_length() + 7) // 8), "big")
    if isinstance(seed, str):
        return bytes.fromhex(seed)
    if isinstance(seed, (bytes, bytearray)):
        return bytes(seed)
    raise TypeError(f"seed must be int, bytes or hex str, not {type(seed).__name__}")


class Stream:
    """SHA-256 over seed + counter; ``n`` is the draws consumed so far."""

    def __init__(self, seed: bytes, n: int = 0):
        self.seed, self.n = seed, n

    @classmethod
    def from_seed(cls, seed: int | bytes | str, label: bytes = b"") -> Stream:
        """``label`` is appended to the seed bytes, the way play.py appends
        the question name."""
        return cls(seed_bytes(seed) + label)

    def u32(self) -> int:
        d = hashlib.sha256(self.seed + self.n.to_bytes(8, "big")).digest()
        self.n += 1
        return int.from_bytes(d[:4], "big")

    def below(self, k: int) -> int:
        """Uniform in 0..k-1, by rejection: no modulo bias."""
        if not 1 <= k <= 1 << 32:
            raise ValueError(f"below(k) needs 1 <= k <= 2**32, got {k!r}")
        cap = (1 << 32) - (1 << 32) % k
        while (x := self.u32()) >= cap:
            pass
        return x % k

    def die(self, sides: int) -> int:
        return self.below(sides) + 1

    def shuffle(self, xs: list) -> list:
        """A shuffled copy; ``StreamRandom.shuffle`` makes the same swaps in place."""
        xs = list(xs)
        for i in range(len(xs) - 1, 0, -1):
            j = self.below(i + 1)
            xs[i], xs[j] = xs[j], xs[i]
        return xs


class StreamRandom:
    """A ``random.Random``-shaped shim over a ``Stream``."""

    generator_id = GENERATOR_ID

    def __init__(self, stream: Stream):
        self.stream = stream

    @classmethod
    def from_seed(cls, seed: int | bytes | str, label: bytes = b"") -> StreamRandom:
        return cls(Stream.from_seed(seed, label))

    @property
    def counter(self) -> int:
        return self.stream.n

    @property
    def seed_id(self) -> str:
        """Short hash of the seed bytes: names the seed without printing it."""
        return hashlib.sha256(self.stream.seed).hexdigest()[:16]

    def randint(self, a: int, b: int) -> int:
        if b < a:
            raise ValueError(f"empty range for randint({a}, {b})")
        return a + self.stream.below(b - a + 1)

    def choice(self, seq: Sequence) -> Any:
        if not len(seq):
            raise IndexError("cannot choose from an empty sequence")
        return seq[self.stream.below(len(seq))]

    def shuffle(self, xs: list) -> None:
        for i in range(len(xs) - 1, 0, -1):
            j = self.stream.below(i + 1)
            xs[i], xs[j] = xs[j], xs[i]

    def random(self) -> float:
        return self.stream.u32() / 2**32

    def mark(self) -> dict:
        """Where the stream stands now: enough, with the seed, to replay."""
        return {
            "seed_id": self.seed_id,
            "generator_id": self.generator_id,
            "counter": self.counter,
        }


# ── the table's side (flatten F-14): which generator a game gets, and replay ──
class _Legacy:
    """Opt-out marker: ``rng_factory=LEGACY`` keeps the old global-``random``
    path so recorded runs can still be reproduced (C9)."""

    generator_id = "random/global"

    def __repr__(self) -> str:
        return "LEGACY"


LEGACY = _Legacy()
LEGACY_ID = _Legacy.generator_id


def default_factory(seed):
    return StreamRandom.from_seed(seed)


# -- the policy side and the draw (flatten-2 F13: "randomness F-14 missed") ----
POLICY_LEGACY_ID = "mt19937-policy/legacy"


class _PolicyLegacy:
    """``mt19937-policy/legacy``: ``random.Random(seed)``, the Mersenne
    Twister the games' policies and adapters were seeded with before the
    stream was their default. It is named so that a frozen record
    (``baselines-N500.md``, ``pools-N200.json``) can be re-derived exactly
    (D-e: "bytes already persisted never change"; rule 12). New runs never
    use it unless asked by name. Not guaranteed the same on every Python,
    which is why the stream is the default."""

    generator_id = POLICY_LEGACY_ID

    def __call__(self, seed):
        return random.Random(seed)

    def __repr__(self) -> str:
        return "MT19937_POLICY"


MT19937_POLICY = _PolicyLegacy()

POLICY_LABEL = b"policy"


def policy_factory(seed):
    """The default for a policy's rng: the stream, under its own label, so a
    policy and the game it plays (both seeded ``i``) do not draw the same
    numbers."""
    return StreamRandom.from_seed(seed, POLICY_LABEL)


def resolve_policy_factory(factory):
    """None -> the stream's policy factory; ``MT19937_POLICY`` (or any
    callable) is handed back as given."""
    return policy_factory if factory is None else factory


def generator_pair(generator):
    """``(the games' rng_factory, the policy's factory)`` for a whole run.
    ``None`` -> the stream for both (``None`` for the games is each game's
    default, the stream). Two named old pairs re-derive the two frozen
    records, each made at a different time:

    * ``LEGACY`` -> ``LEGACY`` games and the ``MT19937_POLICY`` policy: the
      Aug 17 ``baselines-N500.md`` (card games on ``random.Random``, the scene
      on the seeded global ``random``).
    * ``MT19937_POLICY`` -> ``MT19937_POLICY`` for both: the Oct 19
      ``pools-N200.json`` (card games and the policy on ``random.Random``, the
      scene already on the stream).

    Nothing else is a generator."""
    if generator is None:
        return None, policy_factory
    if generator is LEGACY:
        return LEGACY, MT19937_POLICY
    if generator is MT19937_POLICY:
        return MT19937_POLICY, MT19937_POLICY
    raise ValueError(
        f"generator must be None (the stream), LEGACY or MT19937_POLICY, got {generator!r}"
    )


def adapter_rng(rng_factory, seed):
    """The rng a card or dice adapter plays a seed with. None -> the stream
    (``default_factory``); ``LEGACY`` or ``MT19937_POLICY`` -> the old
    ``random.Random(seed)``, by name; else the caller's factory."""
    if rng_factory is None:
        return default_factory(seed)
    if rng_factory is LEGACY or rng_factory is MT19937_POLICY:
        return random.Random(seed)
    return rng_factory(seed)


def draw_seed() -> int:
    """An unpredictable seed, drawn ONCE (``os.urandom``, 8 bytes), to be
    recorded by the caller and used to drive the whole run from the stream:
    the run is then replayable from its recorded seed. ``urandom`` is only the
    source of the number; every draw after it is the stream's."""
    return int.from_bytes(os.urandom(8), "big")


def resolve_factory(rng_factory):
    """None -> the stream (the default); LEGACY -> None (global random);
    ``MT19937_POLICY`` -> the stream too (it names the Mersenne policy and the
    card adapters, which a scene's dice never had; ``pools-N200.json`` was
    made so); else the caller's factory."""
    if rng_factory is None or rng_factory is MT19937_POLICY:
        return default_factory
    if rng_factory is LEGACY:
        return None
    return rng_factory


class ReplayRefused(ValueError):
    """A record cannot be replayed as written."""


class GeneratorMismatch(ReplayRefused):
    """A record names a generator other than the one asked to replay it."""


class EngineMismatch(ReplayRefused):
    """A record was rolled by a different engine source than the one loaded."""


def seed_label(rng, seed) -> str:
    """What a reset may narrate about its seed: the stream's hashed ``seed_id``
    (a beacon seed fixes every later die, so it is never narrated); a legacy
    or plain ``random.Random`` run keeps the old ``seed=<n>`` text."""
    if isinstance(rng, StreamRandom):
        return f"seed_id={rng.seed_id}"
    return f"seed={seed}"


def replay(seed: int | bytes | str, record: dict, label: bytes = b"") -> StreamRandom:
    """Rebuild the stream a recorded roll came from, positioned at
    ``record["counter_before"]``.

    Refuses (``ReplayRefused``) a record without ``seed_id``, with a counter
    that is not a non-negative int or has ``before > after``; refuses
    (``GeneratorMismatch``) a ``generator_id`` other than this one's and a seed
    whose id differs from the record's (C9, T16)."""
    got = record.get("generator_id")
    if got != GENERATOR_ID:
        raise GeneratorMismatch(
            f"record was made by generator {got!r}; this is {GENERATOR_ID!r} -- not replayed"
        )
    if "seed_id" not in record:
        raise ReplayRefused("record has no seed_id -- not replayed")
    before, after = record.get("counter_before"), record.get("counter")
    for name, v in (("counter_before", before), ("counter", after)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ReplayRefused(f"record {name} must be a non-negative int, got {v!r}")
    if before > after:
        raise ReplayRefused(f"record counter_before {before} is after counter {after}")
    rng = StreamRandom.from_seed(seed, label)
    if record["seed_id"] != rng.seed_id:
        raise GeneratorMismatch(
            f"seed id {rng.seed_id!r} does not match the record's {record['seed_id']!r}"
        )
    rng.stream.n = before
    return rng
