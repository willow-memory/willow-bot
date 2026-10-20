"""xorshift.py -- the second generator, beside the one stream (flatten F-15).

catgrid's and bureau's boards come from xorshift32 with an explicit seed mix,
written in the few operations that mean the same thing in Python and JS, so
their browser ports deal the same board from the same seed (``random.Random``
is Mersenne Twister, reproducible only inside CPython). The two games' copies
are the same generator (catgrid's is a port of bureau's at 88deb47); this is
the one script's, named ``xorshift32/v1`` the way the stream is named
``sha256-stream/v1``.

The games keep their own ``rng.py`` for their JS ports (the operator: "3 keep
both"); ``tests/test_xorshift.py`` is the middle that holds this to each of
them draw for draw while safe-app-store is beside the bot.

Every shift is masked back to 32 bits so Python's unbounded ints track JS's
``>>> 0``. Do not "simplify" the masking. ``below`` is a bare modulo, as the
games' is: the bias is irrelevant at board sizes, and changing it would change
every board. For a fair draw, use the stream.
"""

from __future__ import annotations

GENERATOR_ID = "xorshift32/v1"
M32 = 0xFFFFFFFF
GOLDEN = 0x9E3779B9


class Xorshift32:
    __slots__ = ("s",)

    generator_id = GENERATOR_ID

    def __init__(self, seed: int) -> None:
        s = (seed * 2654435761 + 1) & M32
        self.s = s if s else GOLDEN  # xorshift32 is dead at zero

    def u32(self) -> int:
        x = self.s
        x ^= (x << 13) & M32
        x ^= x >> 17
        x ^= (x << 5) & M32
        self.s = x & M32
        return self.s

    def below(self, n: int) -> int:
        """[0, n), by modulo (the games' rule; see the module docstring)."""
        return self.u32() % n

    def between(self, lo: int, hi: int) -> int:
        """[lo, hi), matching ``random.randrange``'s half-open convention."""
        return lo + self.below(hi - lo)

    def pick(self, seq):
        return seq[self.below(len(seq))]

    def shuffled(self, seq) -> list:
        """Fisher-Yates from the end, drawing from this generator only."""
        out = list(seq)
        for i in range(len(out) - 1, 0, -1):
            j = self.below(i + 1)
            out[i], out[j] = out[j], out[i]
        return out
