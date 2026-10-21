"""The compiled ideas: IDEAS.md's ideas built only from what's already here.

The operator, 2026-10-21: "compile, then we'll get into the loose chunks".
`scripts/scan/snippet_cover.py` found 19 ideas whose sketches call only code
that exists. Each is one function here, made of those calls and glue; nothing
new underneath. Where running it showed the sketch was wrong (a name exists
but its contract refuses the call), the function follows the contract, and
`BLOCKED` says what couldn't be compiled and why.

It sits outside the `onescript` package, as `deep_thought.py` does: it
imports the bot's own modules (`sigh`, `runes`, `horoscope`), which the
package never does. Network reads go only through the box's own readers
(`Config.fetch` via `snapshot`, `Config.beacon`, `Config.market`), so with
none of them set it is offline and says `unreachable`. Nothing here seals,
merges or decides: forecasts and verdicts go on the record through `api` and
`table`, and the human still seals.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1])]  # one-script, willow-bot

import horoscope  # noqa: E402
import runes  # noqa: E402
import sigh  # noqa: E402
from onescript import api, beacon, graph, pools, snapshot, table  # noqa: E402
from onescript.calibration import bins, brier  # noqa: E402
from onescript.states import POPULATED, UNREACHABLE  # noqa: E402
from onescript.stream import StreamRandom  # noqa: E402

#: What compiling showed can't be built from what's here as the sketch said.
BLOCKED = {
    4: "a verdict row's `what` is one of table.WHATS (open, roll, step, "
    "decision, result, agm_head, escalate, deck, market); a CI streak is none "
    "of them, and adding one is the operator's call (SPEC-documented §9)",
    87: "compiles, but only with the `dice` package installed (the `table` "
    "extra): dice5e imports it, which snippet_cover can't see",
    102: "a market call can only say what the market does (`up` or `above`); "
    "the bot's half of 'a release before SPY closes up' has no field, so this "
    "compiles as the SPY call alone",
}


def _yes_no(p: float, yes: str = "yes", no: str = "no") -> list[dict]:
    return [{"path": yes, "p": p}, {"path": no, "p": round(1 - p, 10)}]


def _hex_rng(randomness: str, label: str) -> StreamRandom:
    return StreamRandom.from_seed(bytes.fromhex(randomness), label=label.encode())


def _snap_json(answer: dict):
    return snapshot.as_json(answer) if answer.get("state") == POPULATED else None


# ── 4. the bot's personality data (read only: the rows are BLOCKED) ─────────
def ci_streaks(prs: list[tuple[str, int]], path: Path | None = None) -> dict:
    """{"repo#pr": red streak} from sigh's store, the half of idea 4 that
    compiles."""
    return {f"{repo}#{pr}": sigh.get(repo, pr, path=path) for repo, pr in prs}


# ── 5. bet on your own CI ───────────────────────────────────────────────────
def bet_ci(cfg, pr: int, p_green: float, by: str = "human") -> dict:
    """A wager that PR `pr` goes green on its first push."""
    return api.forecast(cfg, f"pr-{pr}-green-first-push", _yes_no(p_green), by=by)


def score(pairs: list[tuple[float, bool]]) -> dict:
    """Mean Brier score and reliability bins over (confidence, outcome) pairs."""
    if not pairs:
        return {"n": 0, "brier": None, "bins": []}
    mean = sum(brier(p, hit) for p, hit in pairs) / len(pairs)
    return {"n": len(pairs), "brier": mean, "bins": bins(pairs)}


# ── 6. verifiable fortunes ─────────────────────────────────────────────────
def fortune(randomness: str, login: str) -> dict:
    """The horoscope's line for `login`, picked by a drand round's randomness:
    the same round and login give the same line, and nobody picks it early."""
    rng = _hex_rng(randomness, login)  # StreamRandom has choice, not randrange
    return {"seed_id": rng.seed_id, "line": rng.choice(horoscope._TEMPLATES)}


def fortune_for(cfg, day: str, login: str) -> dict:
    """`fortune` for the day's drand round, read through the box's reader."""
    if cfg.beacon is None:
        return {"state": UNREACHABLE, "why": "no beacon reader on this box (--drand)"}
    rnd = beacon.round_for(day)
    try:
        randomness = cfg.beacon(rnd)["randomness"]
    except Exception as e:  # any failure of the reader: said, never guessed
        return {"state": UNREACHABLE, "why": f"round {rnd}: {type(e).__name__}: {e}"}
    return {"state": POPULATED, "round": rnd, **fortune(randomness, login)}


# ── 8. code graph across repos ──────────────────────────────────────────────
def code_graph(repos: list[str], cited_text: str) -> dict:
    """The graph's answer for what `cited_text` cites, per repo. No `cbm` is
    said by each read (`unreachable`), never an empty graph."""
    run = graph.cbm_runner()
    return {r: graph.symbols_for(cited_text, r, runner=run) for r in repos}


# ── 15 and 98. runes ────────────────────────────────────────────────────────
def session_rune(subject: str) -> str:
    """A sealed session's rune, cast from its `session:<sha256>` subject."""
    return runes.cast(subject.removeprefix("session:"))


def tarot(oldest: str, latest: str, drawn: str) -> dict:
    """Rebase shame tarot: past, present and future, each a rune from a SHA."""
    return {
        k: runes.cast(s)
        for k, s in zip(("past", "present", "future"), (oldest, latest, drawn))
    }


# ── 33. out-forecast the weather forecaster ────────────────────────────────
def rain_bet(cfg, day: str, lat: float, lon: float, p_rain: float) -> dict:
    """Your wager on rain, made beside a snapshot of Open-Meteo's forecast."""
    q = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "precipitation_sum",
            "timezone": "auto",
        }
    )
    snap = snapshot.get(
        cfg,
        f"rain-{day}",
        f"https://api.open-meteo.com/v1/forecast?{q}",
        license="CC-BY-4.0",
    )
    bet = api.forecast(cfg, f"rain-{day}", _yes_no(p_rain), by="human")
    return {"snapshot": {k: v for k, v in snap.items() if k != "data"}, "forecast": bet}


# ── 34. the earthquake base rate ───────────────────────────────────────────
def quake_count(cfg, start: str, end: str, minmag: float = 6) -> dict:
    """How many quakes of `minmag` or more USGS counts between two days."""
    q = urllib.parse.urlencode(
        {"starttime": start, "endtime": end, "minmagnitude": minmag}
    )
    snap = snapshot.get(
        cfg,
        f"quakes-m{minmag:g}-{start}",
        f"https://earthquake.usgs.gov/fdsnws/event/1/count?{q}",
        license="LicenseRef-us-public-domain",
    )
    if snap.get("state") != POPULATED:
        return snap
    return {"state": POPULATED, "count": int(snapshot.text(snap).strip())}


# ── 37. the front page ─────────────────────────────────────────────────────
def hn_front(cfg, day: str) -> dict:
    """The titles on Hacker News's front page, as Algolia's search gives them."""
    snap = snapshot.get(
        cfg,
        f"hn-front-{day}",
        "https://hn.algolia.com/api/v1/search?tags=front_page",
        license="LicenseRef-unverified",
    )
    got = _snap_json(snap)
    if got is None:
        return snap
    return {"state": POPULATED, "titles": [h.get("title") for h in got.get("hits", [])]}


# ── 38. the tide, the control ──────────────────────────────────────────────
def tides(cfg, station: str, day: str) -> dict:
    """NOAA's high and low tide predictions for a station and day."""
    q = urllib.parse.urlencode(
        {
            "product": "predictions", "station": station, "begin_date": day,
            "range": 24, "datum": "MLLW", "interval": "hilo", "units": "metric",
            "time_zone": "lst_ldt", "format": "json",
        }
    )  # fmt: skip
    snap = snapshot.get(
        cfg,
        f"tides-{station}-{day}",
        f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?{q}",
        license="LicenseRef-us-public-domain",
    )
    got = _snap_json(snap)
    if got is None:
        return snap
    return {"state": POPULATED, "predictions": got.get("predictions", [])}


# ── 42. old books ──────────────────────────────────────────────────────────
def book(cfg, n: int) -> dict:
    """A Project Gutenberg book's plain text."""
    snap = snapshot.get(
        cfg,
        f"gutenberg-{n}",
        f"https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt",
        license="LicenseRef-gutenberg",
    )
    if snap.get("state") != POPULATED:
        return snap
    return {"state": POPULATED, "text": snapshot.text(snap)}


# ── 45. art of the day ─────────────────────────────────────────────────────
MET = "https://collectionapi.metmuseum.org/public/collection/v1/objects"


def art_of_the_day(cfg, randomness: str) -> dict:
    """One of the Met's objects, picked by a drand round's randomness."""
    ids = _snap_json(snapshot.get(cfg, "met-ids", MET, license="CC0-1.0"))
    if not ids or not ids.get("objectIDs"):
        return {"state": UNREACHABLE, "why": "the Met's object list could not be read"}
    pick = _hex_rng(randomness, "met").choice(ids["objectIDs"])
    art = _snap_json(
        snapshot.get(cfg, f"met-{pick}", f"{MET}/{pick}", license="CC0-1.0")
    )
    if art is None:
        return {
            "state": UNREACHABLE,
            "why": f"the Met's object {pick} could not be read",
        }
    return {
        "state": POPULATED,
        "object": pick,
        "title": art.get("title"),
        "public_domain": art.get("isPublicDomain"),
    }


# ── 87. roll for merge ─────────────────────────────────────────────────────
def roll_for_merge(
    cfg,
    repo: str,
    pr: int,
    randomness: str,
    green_runs: int,
    sigh_path: Path | None = None,
) -> dict:
    """A d20 for PR `pr`: plus its green runs, minus sigh's red streak, rolled
    from a drand round's randomness and kept as a `roll` verdict. Advisory:
    the roll merges nothing. Needs the `dice` package (the `table` extra),
    which `dice5e` imports: said as `unreachable` when it's absent."""
    try:
        from onescript import dice5e
    except ImportError as e:
        return {"state": UNREACHABLE, "why": f"dice5e needs the table extra: {e}"}
    rng = _hex_rng(randomness, f"pr-{pr}")
    mod = green_runs - sigh.get(repo, pr, path=sigh_path)
    total, nat = dice5e.d20(rng, mod=mod)
    row = {
        "what": "roll",
        "session": f"merge roll {repo} {pr}",
        "roll": "1d20",
        "mod": mod,
        "total": total,
        "nat": nat,
        "seed_id": rng.seed_id,
    }
    return {"total": total, "nat": nat, **table.verdict(cfg, row)}


# ── 88, 89 and 95. forecasts nobody should take seriously ──────────────────
def grade_horoscope(cfg, day: str, login: str) -> dict:
    """The day's horoscope as a coin-flip forecast by the `oracle` role (a
    forecast is by a role, never a name: not `stars`), to be settled by CI."""
    line = horoscope.reading(login, day)
    bet = api.forecast(
        cfg, f"horoscope-{day}", _yes_no(0.5, "tests-red", "tests-green"), by="oracle"
    )
    return {"line": line, **bet}


def sigh_forecast(streak: int) -> float:
    """The sigh's h's read as a probability that the next push is red too."""
    line = sigh.sigh_line(streak) or "sig"
    return min(0.95, 0.5 + 0.1 * (len(line) - 3))


def spurious(cfg, claim: str) -> dict:
    """A correlation the pile found, logged as a forecast that must come true
    next time, so the calibration curve can show how many were noise."""
    return api.forecast(cfg, claim, _yes_no(0.5, "holds", "noise"), by="desk")


# ── 92. earthquakes as dice ────────────────────────────────────────────────
def quake_dice(magnitudes: list[float]) -> list[dict]:
    """The last digit of each magnitude, binned: a fair die would be flat."""
    digits = [int(f"{m:.1f}"[-1]) for m in magnitudes]
    return pools.hist(digits, bins=10) if digits else []


# ── 94. rainy-day scope ────────────────────────────────────────────────────
def rainy_day_scope(cfg, rainy_days: list[str]) -> dict:
    """The stack code proposes for each day it rained: the record's piles whose
    `when` is that day. A proposal only; the human seals a scope or nothing."""
    return {
        d: api.scope(cfg, by=("when",), match={"when": d}) for d in sorted(rainy_days)
    }


# ── 102. the market bets on the bot (the SPY half: see BLOCKED) ────────────
def bot_vs_market(cfg, close_date: str, p_up: float) -> dict:
    """The SPY half of the cross-world wager, as a market call."""
    m = {"symbol": "SPY", "kind": "up", "close_date": close_date}
    return api.forecast(cfg, f"spy-up-{close_date}", _yes_no(p_up), by="desk", market=m)


COMPILED = {
    4: ci_streaks, 5: bet_ci, 6: fortune_for, 8: code_graph, 15: session_rune,
    33: rain_bet, 34: quake_count, 37: hn_front, 38: tides, 42: book,
    45: art_of_the_day, 87: roll_for_merge, 88: grade_horoscope,
    89: sigh_forecast, 92: quake_dice, 94: rainy_day_scope, 95: spurious,
    98: tarot, 102: bot_vs_market,
}  # fmt: skip


if __name__ == "__main__":
    print(json.dumps({n: f.__name__ for n, f in COMPILED.items()}, indent=1))
    print(json.dumps({"blocked": BLOCKED}, indent=1))
