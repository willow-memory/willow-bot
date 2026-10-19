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
"""

from __future__ import annotations

import hashlib
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
