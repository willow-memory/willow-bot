"""beacon — the drand round a table forecast waits for, as arithmetic (W4 break 1).

A table forecast names a future drand round (`beacon_round`; SPEC-rival R1:
"the wager row naming the beacon round it awaits"; SPEC H7: one beacon per
forecast window). The session that answers it must be seeded by that round's
`randomness`, which nobody knows until the round fires, so code can't grind
seeds offline until the dice land on its forecast.

The constants and `round_time` are the house's: willows-grove
`seat/willow/experiments/predictions-2026-10-17/play.py:33-52` (the League of
Entropy default chain, 30 s rounds). `seed_id` is the one stream's
`StreamRandom.seed_id` (`stream.py`): the first 16 hex of SHA-256 over the
seed bytes, here for a drand `randomness` (hex), and taken from the stream
itself (flatten F5: one copy; it was restated here).

Not checked here, said (SPEC-rival T10): the beacon's BLS signature. A reader
(`Config.beacon`) hands back what drand says round R's randomness is; that is
what a session's randomness is compared with. The reader is the operator's,
fixed in `Config`, never the caller's.

What is trusted, said (R23a): the reader. No box clock is: a round is "still
ahead" only when the reader can't read it yet (`latest(read)` below the round
AND `read(round)` not answering), and the reader's newest round is recorded on
the wager row and, at each tip seal, in the anchor beside the keys (outside
the box). A reader that lies (or a drand whose BLS is not checked) wins;
nothing else does. The trusted reader must never report a `latest` older than
a round it serves.

Stated limit (R22, R23a): on a beat that is not a set piece the code rolls
the beat's suggested stat, and the replay holds it to that. On a set piece
the stat is a pick (the model's or the human's), and once the round is out
the dice are public, so a set-piece pick can be made after seeing them.

Stated limit (R23a): "one table forecast per round" holds per box. Two boxes
on one operator's reader can each forecast the same round and play only the
box whose forecast the dice favour; closing that needs a round registry
beside the operator's reader, which this module does not have.

The dice (R23a): `Stream` is the pile's one stream (`onescript/stream.py`,
generator `sha256-stream/v1`, flatten F1) so a roll can be REPLAYED from the
open row's randomness alone; `roll_2d6` replays
`engine_v1_7.Engine.roll`'s two `random.randint(1, 6)` draws
(`apps/game/engine_v1_7.py`, through `StreamRandom.randint`). e2e pins both
equal to the-table's.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from . import stream

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
    """The stream's `StreamRandom.seed_id` for a hex seed (one copy, F5)."""
    return stream.StreamRandom.from_seed(randomness).seed_id


# ── a day's round, and the u32 seed it gives (catgrid's daily board, F-14) ──
def round_for(day: str) -> int:
    """The round current at `day` (YYYY-MM-DD) 00:00 UTC. ValueError for a day
    before the beacon's first round, or not a date."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t = int(d.timestamp())
    if t < GENESIS:
        raise ValueError(f"{day} is before the beacon's first round")
    return (t - GENESIS) // PERIOD + 1


def seed_u32(randomness: str) -> int:
    """A u32 seed from a round's randomness: SHA-256's first four bytes, big
    endian (the seed a xorshift32 board takes, `xorshift.Xorshift32`)."""
    return int.from_bytes(hashlib.sha256(bytes.fromhex(randomness)).digest()[:4], "big")


def day_stamp(day: str, read=None) -> tuple[dict | None, str]:
    """(stamp, "") for `day`, or (None, why). A stamp is the day, its round,
    the round's randomness, and the u32 seed and seed_id they give: all a
    replay needs. A reader that fails, or answers for another round, gives no
    stamp and says why; there is no fallback seed (one from the clock would be
    one someone could know in advance)."""
    read = read or drand_reader
    try:
        rnd = round_for(day)
    except ValueError as e:
        return None, str(e)
    try:
        got = read(rnd)
    except Exception as e:  # any failure of the reader: said, never guessed
        return None, f"the beacon could not be read: {type(e).__name__}: {e}"
    if not isinstance(got, dict) or got.get("round") != rnd:
        said = got.get("round") if isinstance(got, dict) else got
        return None, f"the beacon answered for {said!r}, not round {rnd}"
    r = got.get("randomness")
    if not is_randomness(r):
        return None, f"round {rnd} carries no randomness (64 hex)"
    return {
        "day": day,
        "round": rnd,
        "randomness": r,
        "seed": seed_u32(r),
        "seed_id": seed_id(r),
        "bls": BLS_UNVERIFIED,
    }, ""


def check_day_stamp(st: dict) -> str:
    """Why this stamp is not its day's, or "" when it holds together: the round
    is the day's, and the seed is the randomness's."""
    try:
        if st["round"] != round_for(st["day"]):
            return f"round {st['round']} is not {st['day']}'s ({round_for(st['day'])})"
        if not is_randomness(st["randomness"]):
            return "the stamp carries no randomness (64 hex)"
        if st["seed"] != seed_u32(st["randomness"]) or st["seed_id"] != seed_id(
            st["randomness"]
        ):
            return "the seed is not the randomness's: the board was not seeded by the beacon"
    except (KeyError, TypeError, ValueError) as e:
        return f"not a daily stamp ({type(e).__name__}: {e})"
    return ""


def drand_reader(rnd: int | str) -> dict:
    """Round `rnd` (or "latest") from drand over HTTPS (play.py:40-43). Raises
    on any failure, and for a round not out yet; the caller says
    `unreachable`. BLS is not checked."""
    import json
    import urllib.request

    which = "latest" if rnd == "latest" else str(int(rnd))
    url = f"{DRAND}/{CHAIN}/public/{which}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def latest(read) -> tuple[int | None, str | None]:
    """(the newest round the reader can read, None) or (None, why). The reader
    is asked `read("latest")` and must answer {"round": int}."""
    if read is None:
        return None, "no beacon reader on this box"
    try:
        got = read("latest")
    except Exception as e:  # any failure of the reader: said, never guessed
        return None, f"the beacon reader failed: {type(e).__name__}: {e}"
    rnd = got.get("round") if isinstance(got, dict) else None
    if isinstance(rnd, bool) or not isinstance(rnd, int):
        return None, "the beacon reader gave no latest round"
    return rnd, None


# ── the dice, replayed (R23a) ────────────────────────────────────────────────
GENERATOR_ID = stream.GENERATOR_ID
Stream = stream.Stream  # the one stream (flatten F1); was a restatement here


def roll_2d6(randomness: str, counter_before: int) -> tuple[int, int, int]:
    """(die a, die b, counter after) for one `Engine.roll` replayed on the
    stream seeded by `randomness`, from `counter_before`."""
    s = Stream(bytes.fromhex(randomness), counter_before)
    a = 1 + s.below(6)
    b = 1 + s.below(6)
    return a, b, s.n
