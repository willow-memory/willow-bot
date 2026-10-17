"""beacon — the drand round a table forecast waits for, as arithmetic (W4 break 1).

A table forecast names a future drand round (`beacon_round`; SPEC-rival R1:
"the wager row naming the beacon round it awaits"; SPEC H7: one beacon per
forecast window). The session that answers it must be seeded by that round's
`randomness`, which nobody knows until the round fires, so code can't grind
seeds offline until the dice land on its forecast.

The constants and `round_time` are the house's: willows-grove
`seat/willow/experiments/predictions-2026-10-17/play.py:33-52` (the League of
Entropy default chain, 30 s rounds). `seed_id` is the-table's
`StreamRandom.seed_id` (`the_table/stream.py`): the first 16 hex of SHA-256
over the seed bytes, here for a drand `randomness` (hex). Both are restated,
not imported, so onescript stays stdlib-only; e2e pins them equal.

Not checked here, said (SPEC-rival T10): the beacon's BLS signature. A reader
(`Config.beacon`) hands back what drand says round R's randomness is; that is
what a session's randomness is compared with. The reader is the operator's,
fixed in `Config`, never the caller's.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

DRAND = "https://api.drand.sh"
CHAIN = "8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce"
GENESIS, PERIOD = 1595431050, 30  # play.py:35

_RANDOMNESS = re.compile(r"[0-9a-f]{64}")
BLS_UNVERIFIED = (
    "the beacon's BLS signature is not checked (T10): the randomness is "
    "compared with what the operator's beacon reader says round R is"
)


def round_time(rnd: int) -> int:
    """When round `rnd` fires, in Unix seconds (play.py:51-52)."""
    return GENESIS + (rnd - 1) * PERIOD


def epoch(ts: str) -> int | None:
    """A record stamp (`2026-10-17T00:00:00Z`) as Unix seconds, or None."""
    try:
        when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return int(when.replace(tzinfo=timezone.utc).timestamp())


def round_at(ts: str) -> int | None:
    """The round current at stamp `ts` (drand's own formula, play.py:46-48)."""
    t = epoch(ts)
    return None if t is None else (t - GENESIS) // PERIOD + 1


def is_randomness(value: object) -> bool:
    return isinstance(value, str) and bool(_RANDOMNESS.fullmatch(value))


def seed_id(randomness: str) -> str:
    """the-table's `StreamRandom.seed_id` for a hex seed."""
    return hashlib.sha256(bytes.fromhex(randomness)).hexdigest()[:16]


def drand_reader(rnd: int) -> dict:
    """Round `rnd` from drand over HTTPS (play.py:40-43). Raises on any
    failure; the caller says `unreachable`. BLS is not checked."""
    import json
    import urllib.request

    url = f"{DRAND}/{CHAIN}/public/{int(rnd)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())
