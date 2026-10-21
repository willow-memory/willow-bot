"""deck.py -- the predictions game's twelve questions, read from a beacon (flatten F6, R1).

The forecasting game (willows-grove ``seat/willow/experiments/
predictions-2026-10-17/play.py``, a frozen record) asks twelve yes/no
questions about random data -- 23 birthdays, 3d6, 20 coin flips -- and answers
them from a drand round's randomness once it fires: question ``q``'s data is
drawn from the one stream seeded by the randomness bytes and then ``q``'s
name. Its round files kept the forecasts and the answers. SPEC-rival R1 asks
for the questions to become a reader that writes the answers as ``verdict``
rows the wagers line up against, so the game's forecasts go through the one
primitive every table forecast does:

  predict  ``api.forecast(id, [{path: yes, p}, {path: no, p}], deck={question,
           beacon_round})`` -- a round still ahead by the operator's reader,
           one forecast per round, question and role
  commit   the wager inside a tip the human seals, before the round fires
  reveal   ``reveal(cfg, round)`` -- one ``deck`` verdict row per question a
           wager names, written as the ``table`` identity, carrying the round,
           its randomness (the operator's reader's) and the answer
  grade    ``outcome(cfg, wager)`` proposes the outcome (code never seals it);
           the human seals it; ``reconcile`` grades it, Brier and log score

The questions, their draws and their exact odds are ``play.py``'s, held here
once on the one stream (``tests/test_deck.py`` holds every draw to
``play.py``'s while willows-grove is beside the bot). The game's round files
are its record as played and stay as they are (rule 12).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from fractions import Fraction
from itertools import product

from . import api, beacon, reverse, table
from .states import EMPTY, POPULATED, UNREACHABLE
from .stream import Stream

YES, NO = "yes", "no"
DECK_KEYS = frozenset(
    {"question", "beacon_round"}
)  # a deck forecast names exactly these
SESSION = "deck round {}"  # a reveal's verdict rows, one session per round


# ── the deck: each question, how its data is drawn, and its exact odds ───────
def _dice_odds(n: int, sides: int, test) -> Fraction:
    hits = sum(1 for r in product(range(1, sides + 1), repeat=n) if test(r))
    return Fraction(hits, sides**n)


def _no_run(n: int, k: int) -> int:
    """How many n-flip sequences have no run of k heads."""
    ways = [0] * k  # ways[j]: sequences ending in exactly j heads
    ways[0] = 1
    for _ in range(n):
        new = [0] * k
        new[0] = sum(ways)
        for j in range(1, k):
            new[j] = ways[j - 1]
        ways = new
    return sum(ways)


def _longest(seq) -> int:
    best = run = 0
    for x in seq:
        run = run + 1 if x else 0
        best = max(best, run)
    return best


def _birthday(n: int, days: int) -> Fraction:
    p = Fraction(1)
    for i in range(n):
        p *= Fraction(days - i, days)
    return 1 - p


def _derangements(n: int) -> int:
    d = [1, 0]
    for i in range(2, n + 1):
        d.append((i - 1) * (d[-1] + d[-2]))
    return d[n]


DECK = {
    "birthday": (
        "23 people, birthdays drawn from 365 days: do any two share one?",
        lambda s: len({s.die(365) for _ in range(23)}) < 23,
        lambda: _birthday(23, 365),
    ),
    "3d6": (
        "Roll 3d6: is the sum 13 or more?",
        lambda s: sum(s.die(6) for _ in range(3)) >= 13,
        lambda: _dice_odds(3, 6, lambda r: sum(r) >= 13),
    ),
    "streak": (
        "Flip 20 coins: is there a run of 4 or more heads?",
        lambda s: _longest(s.below(2) for _ in range(20)) >= 4,
        lambda: 1 - Fraction(_no_run(20, 4), 2**20),
    ),
    "pair": (
        "Deal 5 cards from a shuffled deck: do two share a rank?",
        lambda s: len({c // 4 for c in s.shuffle(range(52))[:5]}) < 5,
        lambda: 1 - Fraction(math.comb(13, 5) * 4**5, math.comb(52, 5)),
    ),
    "ten": (
        "Roll 10d10: does a 10 come up?",
        lambda s: any(s.die(10) == 10 for _ in range(10)),
        lambda: 1 - Fraction(9, 10) ** 10,
    ),
    "fixed": (
        "Shuffle 8 numbered cards: is any card in its own place?",
        lambda s: any(c == i for i, c in enumerate(s.shuffle(range(8)))),
        lambda: 1 - Fraction(_derangements(8), math.factorial(8)),
    ),
    "2d20": (
        "Roll 2d20: is the sum over 25?",
        lambda s: s.die(20) + s.die(20) > 25,
        lambda: _dice_odds(2, 20, lambda r: sum(r) > 25),
    ),
    "six": (
        "Flip 12 coins: exactly 6 heads?",
        lambda s: sum(s.below(2) for _ in range(12)) == 6,
        lambda: Fraction(math.comb(12, 6), 2**12),
    ),
    "distinct": (
        "Roll 6d6: are all six faces different?",
        lambda s: len({s.die(6) for _ in range(6)}) == 6,
        lambda: Fraction(math.factorial(6), 6**6),
    ),
    "d100": (
        "Roll a d100: 37 or under?",
        lambda s: s.die(100) <= 37,
        lambda: Fraction(37, 100),
    ),
    "gap": (
        "Roll 2d6 twice: do the two sums differ by 3 or more?",
        lambda s: abs((s.die(6) + s.die(6)) - (s.die(6) + s.die(6))) >= 3,
        lambda: _dice_odds(4, 6, lambda r: abs(r[0] + r[1] - r[2] - r[3]) >= 3),
    ),
    "max": (
        "Draw 5 numbers from 1–100: is the largest 90 or more?",
        lambda s: max(s.die(100) for _ in range(5)) >= 90,
        lambda: 1 - Fraction(89, 100) ** 5,
    ),
}


def answer(randomness: str, question: str) -> bool:
    """``question``'s answer for a round's randomness (64 hex): its data drawn
    from the stream seeded by the randomness bytes, then the question's name."""
    return bool(
        DECK[question][1](Stream(bytes.fromhex(randomness) + question.encode()))
    )


def odds(question: str) -> Fraction:
    """The exact chance ``question`` comes out yes (the game's oracle)."""
    return DECK[question][2]()


def oracle(question: str) -> list[dict]:
    """The oracle's forecast for ``question``: the exact odds as a distribution."""
    p = float(odds(question))
    return [{"path": YES, "p": p}, {"path": NO, "p": 1 - p}]


def check_forecast(deck: object, distribution: list[dict]) -> str | None:
    """Why a deck forecast does not stand, or None (``api.forecast`` asks)."""
    if not isinstance(deck, dict) or set(deck) != DECK_KEYS:
        return f"deck names exactly {sorted(DECK_KEYS)}"
    if deck["question"] not in DECK:
        return f"no deck question {deck['question']!r}: one of {sorted(DECK)}"
    if {e.get("path") for e in distribution if isinstance(e, dict)} != {YES, NO}:
        return "a deck forecast is over exactly the paths yes and no"
    return None


def wagers_on(rows: list[dict], rnd: int) -> list[dict]:
    return [
        r
        for r in rows
        if r["kind"] == "wager"
        and isinstance(r.get("deck"), dict)
        and r["deck"].get("beacon_round") == rnd
    ]


def deck_rows(rows: list[dict], question: str, rnd: int) -> list[dict]:
    return [
        r
        for r in rows
        if r["kind"] == "verdict"
        and r.get("who") == "table"
        and r.get("what") == "deck"
        and r.get("question") == question
        and r.get("beacon_round") == rnd
    ]


# ── reveal: the beacon's answers as verdict rows ────────────────────────────
def reveal(
    cfg: api.Config, rnd: int, *, argv: Iterable[str] = ("deck-reveal",)
) -> dict:
    """One ``deck`` verdict row per question a wager on round ``rnd`` names
    and no row answers yet, as the ``table`` identity (``table.verdict``).
    The randomness is the operator's reader's (``Config.beacon``, BLS not
    checked); no reader, or a round not out yet: nothing is written, said.
    Returns {"state": "populated", "rows"} (rows written now), or "empty"
    (no wager names the round, or every question is answered already), or
    "unreachable" with why."""
    if isinstance(rnd, bool) or not isinstance(rnd, int) or rnd < 1:
        return api._refused("a reveal names a drand round number", 1)
    if cfg.beacon is None:
        return {
            "state": UNREACHABLE,
            "why": "no beacon reader on this box",
            "code": 1,
        }
    try:
        got = cfg.beacon(rnd)
    except Exception as e:  # any failure of the reader: said, never guessed
        return {"state": UNREACHABLE, "why": f"round {rnd} could not be read: "
                f"{type(e).__name__}: {e}", "code": 1}  # fmt: skip
    rand = got.get("randomness") if isinstance(got, dict) else None
    if not beacon.is_randomness(rand) or got.get("round") not in (rnd, None):
        return {"state": UNREACHABLE, "why": f"the reader gave no randomness for "
                f"round {rnd}", "code": 1}  # fmt: skip
    run, info = api._open(cfg, "deck-reveal", argv, write=False)
    if run is None:
        return info
    rows = run.rec.rows()
    asked = sorted({w["deck"]["question"] for w in wagers_on(rows, rnd)})
    todo = [q for q in asked if q in DECK and not deck_rows(rows, q, rnd)]
    if not todo:
        why = (
            "no forecast names this round"
            if not asked
            else "every question is answered"
        )
        return {"state": EMPTY, "why": why, "rows": [], "code": 0}
    written = []
    for q in todo:
        a = YES if answer(rand, q) else NO
        res = table.verdict(cfg, {
            "what": "deck", "session": SESSION.format(rnd), "question": q,
            "beacon_round": rnd, "randomness": rand, "verdict": a,
            "reason": f"{DECK[q][0]} {a} ({beacon.BLS_UNVERIFIED})",
        }, argv=argv)  # fmt: skip
        if res.get("code") != 0:
            return {**res, "state": "refused", "rows": written}
        written.append(res["row"])
    return {"state": POPULATED, "rows": written, "code": 0}


# ── the outcome code proposes; the human seals it ──────────────────────────
def propose_outcome(
    rows: list[dict],
    wager: str,
    human_key: bytes | None,
    read_beacon: Callable[[int], dict] | None = None,
    seal_rounds: dict | None = None,
) -> dict:
    """Code proposes a deck forecast's outcome from its ``deck`` verdict row;
    it never seals it (R10). The row is the one answering the wager's
    question for its round, written after a tip the human sealed that holds
    the wager (``wager.n <= tip.n < tip_seal.n < verdict.n``, the tip seal's
    proof re-checked against ``human_key``); the round was ahead of the
    forecast and of that tip seal, and the reader says its randomness is the
    row's (``table.beacon_problem``, the table's own check); and the answer,
    drawn again from that randomness, is the row's. Anything else escalates;
    no key or no reader is ``unreachable``, never collapsed into escalate.
    Returns {"state": "proposed", "outcome": {"path"}, ...} or why not."""
    w = next((r for r in rows if r["kind"] == "wager" and r["hash"] == wager), None)
    if w is None:
        return table._escalate("no wager row on record has that hash")
    d = w.get("deck")
    if not isinstance(d, dict) or d.get("question") not in DECK:
        return table._escalate("the forecast names no deck question")
    if not reverse.in_place(rows):
        return {"state": "broken", "reason": "a row's n is not its place in these rows"}
    q, rnd = d["question"], d.get("beacon_round")
    got = deck_rows(rows, q, rnd)
    if not got:
        return table._escalate(f"no deck row answers {q!r} for round {rnd}")
    if len(got) != 1:
        return table._escalate(
            f"{len(got)} deck rows answer {q!r} for round {rnd} (rows "
            f"{[r['n'] for r in got]}): one answer per question and round"
        )
    v = got[0]
    if v["n"] < w["n"]:
        return table._escalate(
            f"deck row {v['n']} comes before the forecast (row {w['n']}): "
            "wager.n < verdict.n is broken (T4)"
        )
    if human_key is None:
        return {"state": UNREACHABLE,
                "reason": "no human key handed over: the tip seal can't be checked"}  # fmt: skip
    tips = [(t, tn) for t, tn in reverse.tip_seals(rows, human_key) if w["n"] <= tn]
    under = next(((t, tn) for t, tn in sorted(tips) if t < v["n"]), None)
    if under is None:
        return table._escalate(
            f"no tip the human sealed holds the forecast before deck row {v['n']} "
            "(wager.n <= tip.n < tip_seal.n < verdict.n, T5)"
        )
    tip_n = int(str(rows[under[0]].get("subject", "")).split(":")[1])
    seal_latest = (seal_rounds or {}).get(str(tip_n))
    seeded = table.beacon_problem(
        v, w, rows[under[0]], read_beacon, seal_latest, rnd=rnd
    )
    if seeded is not None:
        state, why = seeded
        if state == UNREACHABLE:
            return {"state": UNREACHABLE, "reason": why}
        return table._escalate(why)
    a = YES if answer(v["randomness"], q) else NO
    if v.get("verdict") != a:
        return table._escalate(
            f"deck row {v['n']} says {v.get('verdict')!r}; round {rnd}'s randomness "
            f"answers {a!r}"
        )
    idx, entry = reverse._match_distribution(w, {"path": a})
    if entry is None:
        return table._escalate(f"the answer {a!r} is not a path the forecast declares")
    return {
        "state": "proposed",
        "outcome": {"path": entry["path"]},
        "path_index": idx,
        "basis": [v["n"]],
        "under": {"tip_seal": under[0], "tip": under[1]},
        "beacon": {
            "round": rnd,
            "checked": "randomness matches the operator's reader",
            "bls": beacon.BLS_UNVERIFIED,
        },  # fmt: skip
        "subject": reverse.outcome_subject(wager, {"path": entry["path"]}),
    }


def outcome(
    cfg: api.Config,
    wager: str,
    *,
    human_key=None,
    argv: Iterable[str] = ("deck-outcome",),
) -> dict:
    """``propose_outcome`` over the box's record, read-only (an ``invocation``
    row only); a broken chain is ``broken``, apart from escalate and
    unreachable."""
    key, why = api.read_human_key(human_key, cfg.box)
    if why is not None:
        return api._refused(why, 2)
    run, info = api._open(cfg, "deck-outcome", argv)
    if run is None:
        return info
    breaks = run.rec.verify_chain() + run.rec.verify_anchor(key)
    if breaks:
        return {
            "state": "broken",
            "reason": f"the record is broken: {breaks}",
            "code": 1,
        }
    out = propose_outcome(run.rec.rows(), wager, key, run.beacon, run.seal_rounds())
    return {**out, "code": 0 if out["state"] == "proposed" else 1}
