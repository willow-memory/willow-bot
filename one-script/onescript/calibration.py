"""calibration — Brier, log score and reliability bins for wagers. Pure, stdlib.

Provenance (SPEC R2, box build 2026-10-17): `brier`, `log_score`, `BIN_EDGES`
and `bins` are vendored unchanged from the Forge, `Forge/forge/calibration.py`
(lines 37-74 there; itself a vendored copy of oakenscrolls-office's
`calibration.py`, b17: OAKOF, Apache-2.0). Vendored, not imported, so onescript
keeps importing only the standard library (`one-script/README.md`, "stdlib
only"). It is not a free third copy: `tests/test_calibration.py` imports the
Forge's file when it is in reach and asserts both give the same numbers on a
grid, so a drift on either side fails a test. If the Forge's math moves, diff
this against it and reconcile; never edit one alone.

A "pair" everywhere below is (confidence, outcome): the stated P(true) and
whether the claim turned out true. `bins` expects the house convention
(confidence in [0.5, 1.0]); `fold` restates a low-p pair to that side for bins
only (SPEC R5, C2). Brier and log score are unchanged by the fold.
"""

from __future__ import annotations

import math

_EPS = 1e-9

# House convention (Forge design D3): claims are stated in the direction
# believed, so confidence lives in [0.5, 0.99]. Bins cover that range.
BIN_EDGES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


# ── vendored from Forge/forge/calibration.py, unchanged ──────────────────────
def brier(confidence: float, outcome: bool) -> float:
    """Squared distance between what you said and what happened. 0 is prophecy,
    0.25 is coin-flipping at 50%, 1.0 is confidently wrong."""
    return (confidence - (1.0 if outcome else 0.0)) ** 2


def log_score(confidence: float, outcome: bool) -> float:
    """Negative log likelihood of the outcome under your stated belief.
    Punishes confident wrongness much harder than Brier does."""
    p = confidence if outcome else 1.0 - confidence
    return -math.log(max(p, _EPS))


def bins(pairs: list[tuple[float, bool]]) -> list[dict]:
    """Reliability table: for each stated-confidence band, how often were you
    actually right? Empty bands are returned with n=0 so the mirror shows the
    whole range, not just where you've dared to speak."""
    out = []
    for lo, hi in zip(BIN_EDGES, BIN_EDGES[1:]):
        members = [(c, o) for c, o in pairs if lo <= c < hi or (hi == 1.0 and c == 1.0)]
        n = len(members)
        out.append(
            {
                "lo": lo,
                "hi": hi,
                "n": n,
                "mean_confidence": sum(c for c, _ in members) / n if n else None,
                "hit_rate": sum(1 for _, o in members if o) / n if n else None,
            }
        )
    return out


# ── onescript's own, not vendored ────────────────────────────────────────────
def fold(pair: tuple[float, bool]) -> tuple[float, bool]:
    """A claim you think false is a true-claim restated (Forge D3): p < 0.5
    becomes (1 - p, not outcome). For reliability bins only (SPEC R5); Brier
    and log score give the same number either side of the fold."""
    p, o = pair
    return (1.0 - p, not o) if p < 0.5 else (p, o)
