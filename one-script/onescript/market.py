"""market.py -- an honest forecast ledger for market calls (the operator: "c, ledger first").

A signal app shows its winners and drops its losers; nobody can check what it
called before the market moved. Here a market call is a wager like any other,
so its record can't be edited and its losers can't be dropped:

  call     ``api.forecast(id, [{path: yes, p}, {path: no, p}], market={symbol,
           kind, close_date[, threshold]})`` -- "SPY closes up on 2026-10-23",
           or "SPY closes above 580 on 2026-10-23"; one call per question and
           role, so no role holds both sides
  commit   the wager inside a tip the human seals, before the market opens on
           the close date
  resolve  ``reveal(cfg, close_date)`` -- one ``market`` verdict row per symbol
           a call names, written as the ``table`` identity, carrying the close
           and the close before it, as the operator's price reader gives them
  grade    ``outcome(cfg, wager)`` proposes the outcome (code never seals it);
           the human seals it; ``reconcile`` grades it, Brier and log score,
           losers and all

Paper only: nothing here trades, and no code here has an edge. What it gives
is a record that says, without being able to lie, whether a way of calling
the market has any.

The clock is drand's, as the deck's is. A call counts only if it was made, and
its tip sealed, before ``DEADLINE_UTC`` on the close date (13:00 UTC, before
the 09:30 New York open in summer and winter time): the reader's newest round
at the call (``beacon_latest`` on the wager) and at the tip seal (the
anchor's, R23a) must each be a round whose NEXT round fires before the
deadline. A drand round's time is fixed by its number, so this rests on the
reader, never on this box's clock. No beacon reader: no market call.

Prices are the operator's price reader's (``Config.market``: (symbol, date)
-> {"close", "prev_close"}), held as decimal strings so a comparison is exact.
``stooq_reader`` is a free one (stooq.com's daily CSV, no key). The proposal
asks the reader again and escalates if the closes it gives differ from the
row's; no reader, or one that fails, is ``unreachable``, never a guess.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from . import api, beacon, reverse, table
from .states import EMPTY, POPULATED, REPORT_UNREACHABLE, UNREACHABLE

YES, NO = "yes", "no"
KINDS = ("up", "above")  # up: close > the close before it; above: close > threshold
KEYS = frozenset({"symbol", "kind", "close_date"})
DEADLINE_UTC = 13  # the hour a call must be committed by, on its close date
SESSION = "market {}"  # a resolve's verdict rows, one session per close date
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.^=-]{0,14}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day(text: object) -> _date | None:
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        return None
    try:
        return _date.fromisoformat(text)
    except ValueError:
        return None


def _price(x: object) -> Decimal | None:
    """A positive finite price, from a number or a decimal string."""
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def deadline(close_date: str) -> int:
    """Unix seconds a call on ``close_date`` must be committed by."""
    d = _day(close_date)
    if d is None:
        raise ValueError(f"not a date: {close_date!r}")
    when = datetime(d.year, d.month, d.day, DEADLINE_UTC, tzinfo=timezone.utc)
    return int(when.timestamp())


def in_time(latest: object, close_date: str) -> bool:
    """True when ``latest`` (a reader's newest drand round) shows the moment
    it was read was before the deadline: its next round fires before it."""
    if isinstance(latest, bool) or not isinstance(latest, int):
        return False
    return beacon.round_time(latest + 1) <= deadline(close_date)


def check_forecast(market: object, distribution: list[dict]) -> str | None:
    """Why a market call does not stand, or None (``api.forecast`` asks)."""
    if not isinstance(market, dict):
        return "market is {symbol, kind, close_date[, threshold]}"
    want = KEYS | ({"threshold"} if market.get("kind") == "above" else set())
    if set(market) != want:
        return f"a {market.get('kind')!r} market call names exactly {sorted(want)}"
    if market["kind"] not in KINDS:
        return f"kind is one of {list(KINDS)}"
    if not isinstance(market["symbol"], str) or not _SYMBOL.fullmatch(market["symbol"]):
        return "symbol is a ticker: capitals, digits and . ^ = - (SPY, BRK.B, ^GSPC)"
    if _day(market["close_date"]) is None:
        return "close_date is a date, YYYY-MM-DD"
    if market["kind"] == "above" and _price(market["threshold"]) is None:
        return "threshold is a positive price"
    if {e.get("path") for e in distribution if isinstance(e, dict)} != {YES, NO}:
        return "a market call is over exactly the paths yes and no"
    return None


def answer(market: dict, close: str, prev_close: str) -> bool:
    """The call's answer from a close and the close before it."""
    c = Decimal(close)
    if market["kind"] == "up":
        return c > Decimal(prev_close)
    return c > Decimal(str(market["threshold"]))


def wagers_on(rows: list[dict], close_date: str) -> list[dict]:
    return [
        r
        for r in rows
        if r["kind"] == "wager"
        and isinstance(r.get("market"), dict)
        and r["market"].get("close_date") == close_date
    ]


def market_rows(rows: list[dict], symbol: str, close_date: str) -> list[dict]:
    return [
        r
        for r in rows
        if r["kind"] == "verdict"
        and r.get("who") == "table"
        and r.get("what") == "market"
        and r.get("symbol") == symbol
        and r.get("close_date") == close_date
    ]


def read_closes(read, symbol: str, close_date: str) -> tuple[dict | None, str | None]:
    """({"close", "prev_close"} as decimal strings, None) or (None, why)."""
    if read is None:
        return None, "no price reader on this box"
    try:
        got = read(symbol, close_date)
    except Exception as e:  # any failure of the reader: said, never guessed
        return (
            None,
            f"{symbol} on {close_date} could not be read: {type(e).__name__}: {e}",
        )
    if not isinstance(got, dict):
        return None, f"the reader gave no closes for {symbol} on {close_date}"
    c, p = _price(got.get("close")), _price(got.get("prev_close"))
    if c is None or p is None:
        return None, f"the reader gave no closes for {symbol} on {close_date}"
    return {"close": str(c), "prev_close": str(p)}, None


# ── resolve: the closes as verdict rows ─────────────────────────────────────
def reveal(
    cfg: api.Config, close_date: str, *, argv: Iterable[str] = ("market-reveal",)
) -> dict:
    """One ``market`` verdict row per symbol a call on ``close_date`` names
    and no row answers yet, as the ``table`` identity. A close the reader
    can't give (no reader, the market not closed yet, a holiday) is
    ``unreachable`` and nothing is written for it. Returns {"state":
    "populated", "rows"}, "empty" (no call names the date, or every symbol
    is answered) or "unreachable" with why."""
    if _day(close_date) is None:
        return api._refused("a resolve names a close date, YYYY-MM-DD", 1)
    run, info = api._open(cfg, "market-reveal", argv, write=False)
    if run is None:
        return info
    rows = run.rec.rows()
    asked = sorted({w["market"]["symbol"] for w in wagers_on(rows, close_date)})
    todo = [s for s in asked if not market_rows(rows, s, close_date)]
    if not todo:
        why = "no call names this date" if not asked else "every symbol is answered"
        return {"state": EMPTY, "why": why, "rows": [], "code": 0}
    written, missing = [], []
    for s in todo:
        closes, why = read_closes(cfg.market, s, close_date)
        if closes is None:
            missing.append(why)
            continue
        res = table.verdict(cfg, {
            "what": "market", "session": SESSION.format(close_date), "symbol": s,
            "close_date": close_date, **closes, "verdict": "closed",
            "reason": f"{s} closed {closes['close']} on {close_date} "
                      f"(the close before: {closes['prev_close']})",
        }, argv=argv)  # fmt: skip
        if res.get("code") != 0:
            return {**res, "state": "refused", "rows": written}
        written.append(res["row"])
    if not written:
        return {
            "state": UNREACHABLE,
            "why": "; ".join(missing),
            "rows": [],
            "code": 1,
        }
    out = {"state": POPULATED, "rows": written, "code": 0}
    return {**out, REPORT_UNREACHABLE: missing} if missing else out


# ── the outcome code proposes; the human seals it ──────────────────────────
def propose_outcome(
    rows: list[dict],
    wager: str,
    human_key: bytes | None,
    read_market: Callable[[str, str], dict] | None = None,
    seal_rounds: dict | None = None,
) -> dict:
    """Code proposes a market call's outcome from its ``market`` verdict row;
    it never seals it (R10). The row is the one closing the call's symbol on
    its date, written after a tip the human sealed that holds the call
    (``wager.n <= tip.n < tip_seal.n < verdict.n``, the proof re-checked
    against ``human_key``); the call and that tip seal were both made before
    the deadline by the drand reader's rounds (``in_time``); and the price
    reader, asked again, gives the row's closes. Anything else escalates; no
    key, no reader, or no reader round at the tip seal is ``unreachable``.
    Returns {"state": "proposed", "outcome": {"path"}, ...} or why not."""
    w = next((r for r in rows if r["kind"] == "wager" and r["hash"] == wager), None)
    if w is None:
        return table._escalate("no wager row on record has that hash")
    m = w.get("market")
    if not isinstance(m, dict) or check_forecast(m, w.get("distribution") or []):
        return table._escalate("the forecast is not a market call")
    if not reverse.in_place(rows):
        return {"state": "broken", "reason": "a row's n is not its place in these rows"}
    s, day = m["symbol"], m["close_date"]
    got = market_rows(rows, s, day)
    if not got:
        return table._escalate(f"no market row closes {s} on {day}")
    if len(got) != 1:
        return table._escalate(
            f"{len(got)} market rows close {s} on {day} (rows "
            f"{[r['n'] for r in got]}): one close per symbol and date"
        )
    v = got[0]
    if v["n"] < w["n"]:
        return table._escalate(
            f"market row {v['n']} comes before the call (row {w['n']}): "
            "wager.n < verdict.n is broken (T4)"
        )
    if human_key is None:
        return {"state": UNREACHABLE,
                "reason": "no human key handed over: the tip seal can't be checked"}  # fmt: skip
    tips = [(t, tn) for t, tn in reverse.tip_seals(rows, human_key) if w["n"] <= tn]
    under = next(((t, tn) for t, tn in sorted(tips) if t < v["n"]), None)
    if under is None:
        return table._escalate(
            f"no tip the human sealed holds the call before market row {v['n']} "
            "(wager.n <= tip.n < tip_seal.n < verdict.n, T5)"
        )
    tip_n = int(str(rows[under[0]].get("subject", "")).split(":")[1])
    seal_latest = (seal_rounds or {}).get(str(tip_n))
    if isinstance(seal_latest, bool) or not isinstance(seal_latest, int):
        return {"state": UNREACHABLE, "reason": (
            "the anchor holds no reader round for the tip seal: it can't be shown "
            f"to be before {DEADLINE_UTC}:00 UTC on {day} (R23a)")}  # fmt: skip
    for what, seen in (("call", w.get("beacon_latest")), ("tip seal", seal_latest)):
        if not in_time(seen, day):
            return table._escalate(
                f"the {what} was made after {DEADLINE_UTC}:00 UTC on {day} by the "
                f"drand reader (its newest round then: {seen!r}): the market may "
                "have been known"
            )
    closes, why = read_closes(read_market, s, day)
    if closes is None:
        return {"state": UNREACHABLE, "reason": why}
    if (v.get("close"), v.get("prev_close")) != (closes["close"], closes["prev_close"]):
        return table._escalate(
            f"market row {v['n']} says {s} closed {v.get('close')!r} (before: "
            f"{v.get('prev_close')!r}); the price reader says {closes['close']!r} "
            f"(before: {closes['prev_close']!r})"
        )
    a = YES if answer(m, v["close"], v["prev_close"]) else NO
    idx, entry = reverse._match_distribution(w, {"path": a})
    if entry is None:
        return table._escalate(f"the answer {a!r} is not a path the call declares")
    return {
        "state": "proposed",
        "outcome": {"path": entry["path"]},
        "path_index": idx,
        "basis": [v["n"]],
        "under": {"tip_seal": under[0], "tip": under[1]},
        "closes": closes,
        "subject": reverse.outcome_subject(wager, {"path": entry["path"]}),
    }


def outcome(
    cfg: api.Config,
    wager: str,
    *,
    human_key=None,
    argv: Iterable[str] = ("market-outcome",),
) -> dict:
    """``propose_outcome`` over the box's record, read-only (an ``invocation``
    row only); a broken chain is ``broken``, apart from escalate and
    unreachable."""
    key, why = api.read_human_key(human_key, cfg.box)
    if why is not None:
        return api._refused(why, 2)
    run, info = api._open(cfg, "market-outcome", argv)
    if run is None:
        return info
    breaks = run.rec.verify_chain() + run.rec.verify_anchor(key)
    if breaks:
        return {
            "state": "broken",
            "reason": f"the record is broken: {breaks}",
            "code": 1,
        }
    out = propose_outcome(run.rec.rows(), wager, key, cfg.market, run.seal_rounds())
    return {**out, "code": 0 if out["state"] == "proposed" else 1}


# ── a free price reader ─────────────────────────────────────────────────────
STOOQ = "https://stooq.com/q/d/l/?s={sym}&i=d&d1={d1}&d2={d2}"


def stooq_symbol(symbol: str) -> str:
    """stooq's name for a ticker: US stocks and funds take ``.us`` (SPY ->
    spy.us); an index (^GSPC -> ^spx is stooq's own) is passed as given."""
    s = symbol.lower()
    return s if s.startswith("^") or "." in s else f"{s}.us"


def parse_daily_csv(text: str, close_date: str) -> dict:
    """{"close", "prev_close"} for ``close_date`` from a Date,Open,High,Low,
    Close[,Volume] CSV; raises when the date is not a trading day in it."""
    days = []
    for line in text.strip().splitlines()[1:]:
        cells = line.split(",")
        if len(cells) >= 5 and _day(cells[0]) is not None:
            days.append((cells[0], cells[4]))
    days.sort()
    for i, (d, c) in enumerate(days):
        if d == close_date:
            if i == 0:
                raise LookupError(f"no close before {close_date} in the data")
            return {"close": c, "prev_close": days[i - 1][1]}
    raise LookupError(f"{close_date} is not a trading day in the data (yet)")


def stooq_reader(symbol: str, close_date: str) -> dict:
    """(symbol, date) -> {"close", "prev_close"} from stooq.com's daily CSV
    over HTTPS. Raises on any failure; the caller says ``unreachable``."""
    import urllib.request

    d = _day(close_date)
    if d is None:
        raise ValueError(f"not a date: {close_date!r}")
    start = (d - timedelta(days=10)).strftime("%Y%m%d")
    url = STOOQ.format(sym=stooq_symbol(symbol), d1=start, d2=d.strftime("%Y%m%d"))
    with urllib.request.urlopen(url, timeout=15) as r:
        return parse_daily_csv(r.read().decode("utf-8", "replace"), close_date)
