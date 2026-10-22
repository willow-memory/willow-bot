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

import base64
import csv
import difflib
import hashlib
import io
import json
import re
import statistics
import sys
import urllib.parse
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAN = HERE.parent / "scripts" / "scan"
# one-script, willow-bot, and the scan scripts (script_match's jaccard)
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1]), str(SCAN)]

import horoscope  # noqa: E402
import runes  # noqa: E402
import sigh  # noqa: E402
import script_match  # noqa: E402
from onescript import api, beacon, graph, pile, pools, seed, session  # noqa: E402
from onescript import snapshot, table  # noqa: E402
from onescript.calibration import bins, brier  # noqa: E402
from onescript.states import EMPTY, POPULATED, UNREACHABLE  # noqa: E402
from onescript.stream import StreamRandom  # noqa: E402

#: What compiling showed can't be built from what's here as the sketch said.
BLOCKED = {
    4: "a verdict row's `what` is one of table.WHATS (open, roll, step, "
    "decision, result, agm_head, escalate, deck, market); a CI streak is none "
    "of them, and adding one is the operator's call (SPEC-documented §9)",
    87: "compiles, but only with the `dice` package installed (the `table` "
    "extra): dice5e imports it, which snippet_cover can't see",
    7: "the pick compiles; serving it doesn't: serve only takes chunks a seed "
    "ranked (`serve.chunk_tables`), so two random chunks need a new table "
    "source, which is the operator's call",
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


# ── the loose chunks (IDEAS.md "How much is already here"), each built on the
#    piece the pile pointed at when asked in plain words ────────────────────


# 1. your own quotes as a corpus (the pile pointed at seed.Texts)
SAID = re.compile(
    r"[Tt]he operator,? \(?(\d{4}-\d{2}-\d{2})\)?:?\s*\*?[\"“]([^\"”\n]{3,})[\"”]"
)


def said(index: dict, repos: dict) -> list[dict]:
    """Every "the operator, <date>: "<quote>"" in the indexed files, read at
    their blobs through `seed.Texts`: the date, where, and the quote's hash,
    never its words. Oldest first, then by place."""
    out = []
    for name, f, text in seed.Texts(index, repos):
        for m in SAID.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append(
                {
                    "date": m.group(1),
                    "where": f"{name}:{f['path']}:{line}",
                    "quote_sha256": hashlib.sha256(m.group(2).encode()).hexdigest(),
                }
            )
    return sorted(out, key=lambda r: (r["date"], r["where"]))


def said_in_box(cfg) -> dict:
    """`said` over the box's own index, as the record vouches for it."""
    run, info = api._open(cfg, "said", ("said",), write=False)
    if run is None:
        return info
    index, _, why = seed.read_index(run.rec)
    if index is None:
        return {"state": UNREACHABLE, "why": why}
    rows = said(index, api._repos(cfg, index))
    return {"state": POPULATED if rows else EMPTY, "said": rows}


# 7. serendipity: the pick (the pile pointed at api.scope; serving: BLOCKED)
def serendipity(index: dict, repos: dict, randomness: str) -> dict:
    """One chunk from each of two repos, both picked by a drand round's
    randomness: where they sit and their SHA-256, the same for the same round."""
    by_repo: dict[str, list] = {}
    for name, f, text in seed.Texts(index, repos):
        for line, body, _ in pile.chunks(text):
            by_repo.setdefault(name, []).append((f["path"], line, body))
    if len(by_repo) < 2:
        return {"state": EMPTY, "why": "serendipity needs two repos with chunks"}
    rng = _hex_rng(randomness, "serendipity")
    a, b = _two(rng, by_repo)  # StreamRandom has choice, not sample
    picks = []
    for name in (a, b):
        path, line, body = rng.choice(by_repo[name])
        picks.append(
            {
                "where": f"{name}:{path}:{line}",
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
        )
    return {"state": POPULATED, "pair": picks, "question": "what joins these?"}


def _two(rng, by_repo: dict) -> tuple[str, str]:
    names = sorted(by_repo)
    a = rng.choice(names)
    return a, rng.choice([n for n in names if n != a])


# 11. how sessions end (the pile pointed at session.bundle)
def session_rounds(rows: list[dict], held: set[str]) -> list[dict]:
    """Each checked-out session as a round `pools.measure` reads: its steps
    (the actions taken) and its ending (sealed, or still waiting)."""
    out = []
    for start, subj in sorted(session.subjects(rows).items()):
        acts = session.actions(rows, start)
        out.append(
            {
                "steps": len(acts),
                "terminal": True,  # checked out
                "ending": "sealed" if subj in held else "waiting",
                "winners": [],
                "mid_leader": None,
            }
        )
    return out


def how_sessions_end(rows: list[dict], held: set[str]) -> dict:
    rounds = session_rounds(rows, held)
    if not rounds:
        return {"state": EMPTY, "why": "no session has been checked out"}
    return {"state": POPULATED, **pools.measure(rounds, seats=1)}


# 36. you against the crowd
def crowd(p_yes: float) -> list[dict]:
    """A market's probability as a two-path forecast."""
    return _yes_no(round(float(p_yes), 6))


def against_the_crowd(cfg, market_id: str, mine: float) -> dict:
    """Your probability and Manifold's, as two wagers on one question. The
    crowd forecasts as the `oracle` role: a forecast is by a role, never a
    name."""
    snap = snapshot.get(
        cfg,
        f"manifold-{market_id.lower()}",
        f"https://api.manifold.markets/v0/market/{market_id}",
        license="LicenseRef-unverified",
    )
    got = _snap_json(snap)
    if got is None or "probability" not in got:
        return snap if got is None else {"state": UNREACHABLE, "why": "no probability"}
    you = api.forecast(cfg, f"manifold-{market_id}", _yes_no(mine), by="human")
    them = api.forecast(
        cfg, f"manifold-{market_id}-crowd", crowd(got["probability"]), by="oracle"
    )
    return {"state": POPULATED, "you": you, "crowd": them, "p": got["probability"]}


# 40. what's rare to you (the pile pointed at pile.Cached.counts)
def store_count(cached, word: str) -> int:
    """How many chunks of the pile hold `word` (`Cached.df`)."""
    return cached.df(word)


def dialect(cached, words: list[str]) -> dict:
    """Your share of each word beside the world's (wordfreq's Zipf scale, the
    optional half: `unreachable` without it)."""
    mine = {w: store_count(cached, w) / max(1, cached.n_all) for w in sorted(words)}
    try:
        from wordfreq import zipf_frequency
    except ImportError as e:
        return {"state": UNREACHABLE, "why": f"wordfreq: {e}", "mine": mine}
    world = {w: zipf_frequency(w, "en") for w in mine}
    return {"state": POPULATED, "mine": mine, "world": world}


# 47. the 90 percent quiz (the pile pointed at market.parse_daily_csv)
def answer(csv_text: str, entity: str, year: int) -> Decimal | None:
    """The value in an Our World in Data grapher CSV (Entity, Code, Year,
    <value>) for one entity and year, exact, or None."""
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) >= 4 and row[0] == entity and row[2] == str(year):
            try:
                return Decimal(row[3])
            except InvalidOperation:
                return None
    return None


def quiz(cfg, slug: str, entity: str, year: int, low, high) -> dict:
    """Did your 90% range hold the answer? The outcome; scoring it is
    `calibration` over many such."""
    text = snapshot.text(
        snapshot.get(
            cfg,
            f"owid-{slug}",
            f"https://ourworldindata.org/grapher/{slug}.csv",
            license="CC-BY-4.0",
        )
    )
    if text is None:
        return {"state": UNREACHABLE, "why": f"no snapshot of {slug}"}
    got = answer(text, entity, year)
    if got is None:
        return {"state": EMPTY, "why": f"no {entity} {year} in {slug}"}
    return {"state": POPULATED, "answer": str(got),
            "hit": Decimal(str(low)) <= got <= Decimal(str(high))}  # fmt: skip


# 59. the typo rung's flood guard (the pile pointed at Cached.repair)
REPAIR_FLOOD = 10  # placeholder; the operator's number


def guard_repairs(seed_answer: dict, flood: float = REPAIR_FLOOD) -> dict:
    """Rung 2's repairs, minus any whose fix sits in more than `flood` times
    the bite's median chunks: a rare name traded for a common word (gource ->
    source). Pure; the pile's default is untouched."""
    counts = [k["chunks"] for k in seed_answer.get("keys", []) if k.get("chunks")]
    median = statistics.median(counts) if counts else 1
    reps = (seed_answer.get("repair") or {}).get("repairs", [])
    kept = [r for r in reps if r[2] <= flood * max(1, median)]
    dropped = [r for r in reps if r not in kept]
    return {"median_chunks": median, "kept": kept, "dropped": dropped}


# 62. a cap per repo (the pile pointed at the rank and its RRF fuse)
def cap_per_repo(rank: list[dict], n: int = 10, most: int = 5) -> list[dict]:
    """The top `n` of a rank with no repo taking more than `most`, in rank
    order: a big corpus can't win a code bite on volume alone."""
    out, seen = [], {}
    for h in rank:
        if seen.get(h["repo"], 0) < most:
            out.append(h)
            seen[h["repo"]] = seen.get(h["repo"], 0) + 1
        if len(out) == n:
            break
    return out


# 63 and 64. the world-wide IDs the pile already has
def swhid(blob: str, origin: str, path: str, a: int, b: int) -> str:
    """A chunk's Software Heritage ID: its file's git blob, qualified."""
    return f"swh:1:cnt:{blob};origin={origin};path=/{path};lines={a}-{b}"


def cid(chunk_sha256: str) -> str:
    """A chunk's SHA-256 as an IPFS CIDv1 (raw, sha2-256, base32)."""
    raw = bytes([1, 0x55, 0x12, 0x20]) + bytes.fromhex(chunk_sha256)
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")


# 86. compare the snippets (the pile pointed at script_match and diverge)
NEAR = 0.5  # placeholder; the operator's number


def shingles(text: str, k: int = 3) -> frozenset:
    """Word k-shingles, by the pile's own word rule (`seed.WORD`)."""
    ws = seed.WORD.findall(text.lower())
    return frozenset(" ".join(ws[i : i + k]) for i in range(max(1, len(ws) - k + 1)))


def diff_hunks(a: str, b: str) -> list[str]:
    """The unified diff's hunks between two snippets (difflib, no context)."""
    lines = difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
    return [x for x in lines if not x.startswith(("---", "+++"))]


def relate(a: str, b: str, near: float = NEAR) -> dict:
    """same (one hash), near (shingle Jaccard at `near` or over, with the
    diff), or apart. `close` (same meaning) needs the embedder: not here."""
    if hashlib.sha256(a.encode()).digest() == hashlib.sha256(b.encode()).digest():
        return {"rel": "same", "score": 1.0}
    score = script_match.jaccard(shingles(a), shingles(b))
    if score >= near:
        return {"rel": "near", "score": round(score, 3), "diff": diff_hunks(a, b)}
    return {"rel": "apart", "score": round(score, 3)}


# 96. your decisions as a Gource movie (the pile pointed at beacon.epoch)
def gource_log(rows: list[dict]) -> list[str]:
    """The record as Gource's custom log: time|who|A|kind/n."""
    out = []
    for r in rows:
        t = beacon.epoch(r.get("ts", ""))
        if t is not None:
            out.append(f"{t}|{r.get('who', 'run')}|A|{r['kind']}/{r['n']}")
    return out


# 97. the pile writes poetry (the pile pointed at the NPMI pair rule)
def strongest_pairs(seed_answer: dict, n: int = 12) -> list[str]:
    """A seed's kept word pairs, strongest NPMI first. Hashed in the record;
    the answer the caller holds still has them as words."""
    pairs = seed_answer.get("pairs") or {}
    return [p for p, _ in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))][:n]


def poem(pairs: list[str], randomness: str, lines: int = 3) -> list[str]:
    """Lines of two pairs each, picked by a drand round's randomness."""
    if len(pairs) < 2:
        return []
    rng = _hex_rng(randomness, "poem")
    return [f"{rng.choice(pairs)}, {rng.choice(pairs)}." for _ in range(lines)]


COMPILED = {
    4: ci_streaks, 5: bet_ci, 6: fortune_for, 8: code_graph, 15: session_rune,
    33: rain_bet, 34: quake_count, 37: hn_front, 38: tides, 42: book,
    45: art_of_the_day, 87: roll_for_merge, 88: grade_horoscope,
    89: sigh_forecast, 92: quake_dice, 94: rainy_day_scope, 95: spurious,
    98: tarot, 102: bot_vs_market,
    # the loose chunks
    1: said_in_box, 7: serendipity, 11: how_sessions_end, 36: against_the_crowd,
    40: dialect, 47: quiz, 59: guard_repairs, 62: cap_per_repo, 63: swhid,
    64: cid, 86: relate, 96: gource_log, 97: poem,
}  # fmt: skip


if __name__ == "__main__":
    print(json.dumps({n: f.__name__ for n, f in COMPILED.items()}, indent=1))
    print(json.dumps({"blocked": BLOCKED}, indent=1))
