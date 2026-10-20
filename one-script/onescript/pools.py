"""pools.py -- where a run's endings pool, and where its outer layer is.

Came in from the-table's ``the_table/pools.py`` (flatten F-14): the half that
is about randomness and not about any one game. Given the rounds of a run,
each as what a seat could see of it -- how many steps, whether it reached a
real end, the ending named plainly, the winners, and who led at the halfway
step -- ``measure`` keeps the shape of what fell out:

* **pools**: the endings, each counted and banded with ``odds.band_of`` (the
  ``likely`` / ``middle`` / ``outlier`` read the story forecaster uses;
  ``outlier``, below ``odds.MIDDLE_FLOOR``, is the outer layer);
* **lumps**: how long rounds run, as a histogram (``hist``), with the cap-hits
  (a round the cap cut off) said apart;
* **the funnel**: where a leader can be read, how often the halfway leader is
  among the winners, beside chance (one over the seats).

Playing the games, naming each game's endings and reading its leader stay
with the-table, which hands the rounds here.
"""

from __future__ import annotations

import statistics
from collections import Counter
from fractions import Fraction

from .odds import band_of

BINS = 12


def hist(xs: list[int], bins: int = BINS) -> list[dict]:
    """``bins`` equal-width bins (at least 1 wide) from min to max, each
    ``{from, to, n}``; every value lands in one."""
    lo, hi = min(xs), max(xs)
    width = max(1, -(-(hi - lo + 1) // bins))
    counts = Counter((x - lo) // width for x in xs)
    return [
        {"from": lo + i * width, "to": lo + (i + 1) * width - 1, "n": counts.get(i, 0)}
        for i in range((hi - lo) // width + 1)
    ]


def measure(rounds: list[dict], seats: int) -> dict:
    """The run's shape. Each round: ``steps``, ``terminal``, ``ending``,
    ``winners``, ``mid_leader`` (None where no leader can be read)."""
    n = len(rounds)
    pools: Counter = Counter()
    lengths, cap, agree, read = [], 0, 0, 0
    for r in rounds:
        lengths.append(r["steps"])
        cap += not r["terminal"]
        pools[r["ending"]] += 1
        mid = r["mid_leader"]
        if mid is not None and r["winners"]:
            read += 1
            agree += mid in r["winners"]
    top = max(pools.values())
    rows = sorted(pools.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "rounds": n,
        "pools": [
            {
                "ending": k,
                "n": c,
                "p": round(c / n, 4),
                "band": band_of(Fraction(c, n), Fraction(top, n)),
            }
            for k, c in rows
        ],
        "lengths": {
            "min": min(lengths),
            "median": statistics.median(lengths),
            "max": max(lengths),
            "hist": hist(lengths),
        },
        "cap_hits": cap,
        "funnel": (
            {
                "read": read,
                "midpoint_leader_wins": round(agree / read, 4),
                "chance": round(1 / seats, 4),
            }
            if read
            else None
        ),
    }
