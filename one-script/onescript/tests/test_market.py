"""The honest forecast ledger for market calls (market.py).

A call is a wager over yes and no about one close; it counts only if it and
the tip the human sealed over it were made before the market opened on the
close date, by the drand reader's rounds; the close comes from the
operator's price reader as a ``market`` verdict row; code proposes the
outcome and never seals it; the human's seal and reconcile grade it, losers
and all. Every forbidden act is tried: a call too late, a role on both sides,
a tip sealed after the deadline, a row before the sealed tip, a close the
reader does not give, two closes for one day, and a sealed outcome the close
does not say.
"""
# ruff: noqa: F811

from __future__ import annotations

import dataclasses
import json

import pytest

from onescript import api, beacon, market, reverse
from onescript.tests.test_table import (  # noqa: F401  (the box and its keys)
    NOW,
    cfg,
    key_file,
    proof,
    rows,
    seal_tip,
)

DAY = "2026-10-19"  # a Monday, after NOW (2026-10-17 00:00 UTC)
UP = {"symbol": "SPY", "kind": "up", "close_date": DAY}
ABOVE = {"symbol": "SPY", "kind": "above", "close_date": DAY, "threshold": "580"}
CLOSES = {("SPY", DAY): {"close": "581.25", "prev_close": "579.10"}}


class Clock:
    """The drand reader, offline: its newest round is set by the test, so a
    tip can be sealed before or after a close date's deadline."""

    def __init__(self) -> None:
        self.latest = beacon.round_at(NOW)

    def __call__(self, rnd) -> dict:
        if rnd == "latest":
            return {"round": self.latest}
        raise LookupError(f"round {rnd} is not served by this clock")

    def at_deadline_of(self, day: str) -> None:
        """Newest round = the one firing at the deadline: too late."""
        self.latest = (market.deadline(day) - beacon.GENESIS) // beacon.PERIOD + 1


class Prices:
    """The price reader, offline: CLOSES, or a LookupError (not closed yet,
    or not a trading day)."""

    def __init__(self, closes: dict | None = None) -> None:
        self.closes = dict(CLOSES if closes is None else closes)

    def __call__(self, symbol: str, day: str) -> dict:
        if (symbol, day) not in self.closes:
            raise LookupError(f"{symbol} has no close on {day} in this reader")
        return dict(self.closes[(symbol, day)])


@pytest.fixture
def box(cfg):
    return dataclasses.replace(cfg, beacon=Clock(), market=Prices())


def _dist(p=0.6):
    return [{"path": "yes", "p": p}, {"path": "no", "p": round(1 - p, 10)}]


def call(cfg, wid="m1", m=UP, by="desk", p=0.6):
    return api.forecast(cfg, wid, _dist(p), by=by, market=dict(m))


def committed(cfg, **kw):
    f = call(cfg, **kw)
    assert f["code"] == 0, f
    assert seal_tip(cfg, f["tip"])["sealed"]
    return f


def resolved(cfg, day=DAY):
    got = market.reveal(cfg, day)
    assert got["state"] == "populated", got
    return got


# ── the call ────────────────────────────────────────────────────────────────
def test_a_call_is_a_wager_naming_its_question_and_the_drand_round_then(box):
    f = call(box)
    assert f["code"] == 0
    w = f["row"]
    assert w["kind"] == "wager" and w["market"] == UP
    assert w["beacon_latest"] == beacon.round_at(NOW)


@pytest.mark.parametrize(
    "m, said",
    [
        ({**UP, "kind": "down"}, "kind is one of"),
        ({**UP, "symbol": "spy"}, "symbol is a ticker"),
        ({**UP, "symbol": "SPY; rm"}, "symbol is a ticker"),
        ({**UP, "close_date": "19/10/2026"}, "close_date is a date"),
        ({**UP, "close_date": "2026-02-30"}, "close_date is a date"),
        ({**ABOVE, "threshold": -1}, "threshold is a positive price"),
        ({**ABOVE, "threshold": True}, "threshold is a positive price"),
        ({**ABOVE, "threshold": "NaN"}, "threshold is a positive price"),
        ({k: v for k, v in ABOVE.items() if k != "threshold"}, "names exactly"),
        ({**UP, "threshold": "580"}, "names exactly"),
        ({**UP, "extra": 1}, "names exactly"),
    ],
)
def test_a_call_that_is_not_one_question_is_refused(box, m, said):
    got = call(box, m=m)
    assert got["code"] == 1 and said in got["refused"]


def test_paths_other_than_yes_and_no_are_refused(box):
    got = api.forecast(box, "m1", [{"path": "up", "p": 1.0}], by="desk", market=UP)
    assert got["code"] == 1 and "yes and no" in got["refused"]


def test_a_call_after_the_open_is_refused(box):
    """Forbidden act: calling a day the market may already have shown."""
    got = call(box, m={**UP, "close_date": "2026-10-16"})
    assert got["code"] == 1 and "too late" in got["refused"]
    box.beacon.at_deadline_of(DAY)
    got = call(box, wid="m2")
    assert got["code"] == 1 and "too late" in got["refused"]


def test_no_drand_reader_no_call(box):
    box = dataclasses.replace(box, beacon=None)
    got = call(box)
    assert got["code"] == 1 and "timed by the drand reader" in got["refused"]


def test_a_role_cannot_hold_both_sides(box):
    """Forbidden act: one role calling the same question twice (a hedge
    whose loser would be dropped)."""
    assert call(box, wid="m1", p=0.9)["code"] == 0
    got = call(box, wid="m2", p=0.1)
    assert got["code"] == 1 and "one call per question and role" in got["refused"]
    assert call(box, wid="m3", by="human", p=0.1)["code"] == 0  # another role may
    assert call(box, wid="m4", m=ABOVE)["code"] == 0  # another question may


def test_a_forecast_is_about_one_thing(box):
    got = api.forecast(box, "m1", _dist(), by="desk", market=UP,
                       deck={"question": "3d6", "beacon_round": 10**9})  # fmt: skip
    assert got["code"] == 1 and "one of" in got["refused"]


# ── resolve ─────────────────────────────────────────────────────────────────
def test_no_price_reader_writes_nothing_and_says_why(box):
    committed(box)
    box = dataclasses.replace(box, market=None)
    n = len(rows(box))
    got = market.reveal(box, DAY)
    assert got["state"] == "unreachable" and "no price reader" in got["why"]
    assert len(rows(box)) == n


def test_a_day_with_no_close_yet_writes_nothing(box):
    committed(box)
    box = dataclasses.replace(box, market=Prices({}))
    got = market.reveal(box, DAY)
    assert got["state"] == "unreachable" and "could not be read" in got["why"]


def test_one_row_per_symbol_and_date_with_both_closes(box):
    committed(box, wid="m1", m=UP)
    committed(box, wid="m2", m=ABOVE)  # the same symbol and day: one row
    got = resolved(box)
    (r,) = got["rows"]
    assert r["kind"] == "verdict" and r["who"] == "table" and r["what"] == "market"
    assert (r["symbol"], r["close_date"]) == ("SPY", DAY)
    assert (r["close"], r["prev_close"]) == ("581.25", "579.10")
    again = market.reveal(box, DAY)
    assert again["state"] == "empty" and "answered" in again["why"]


def test_a_date_no_call_names_is_empty(box):
    got = market.reveal(box, DAY)
    assert got["state"] == "empty" and "no call" in got["why"]


# ── the outcome, proposed; the seal, the human's; the grade ─────────────────
@pytest.mark.parametrize(
    "m, closes, want",
    [
        (UP, {"close": "581.25", "prev_close": "579.10"}, "yes"),
        (UP, {"close": "579.10", "prev_close": "579.10"}, "no"),  # flat is not up
        (ABOVE, {"close": "580.00", "prev_close": "570"}, "no"),  # equal is not above
        (ABOVE, {"close": "580.01", "prev_close": "590"}, "yes"),
    ],
)
def test_the_outcome_is_the_close_exactly_and_never_sealed(
    box, key_file, m, closes, want
):
    box = dataclasses.replace(box, market=Prices({("SPY", DAY): closes}))
    f = committed(box, m=m)
    resolved(box)
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert got["state"] == "proposed", got
    assert got["outcome"] == {"path": want}
    assert not [r for r in rows(box) if r["kind"] == "seal"
                and str(r.get("subject", "")).startswith(reverse.WAGER)]  # fmt: skip


def test_the_human_seals_it_and_reconcile_grades_it_losers_and_all(box, key_file):
    win = committed(box, wid="win", m=UP, p=0.8)  # yes, and it was
    lose = committed(box, wid="lose", m=ABOVE, p=0.1)  # 10% yes, and it was yes
    resolved(box)
    for f in (win, lose):
        got = market.outcome(box, f["subject"], human_key=key_file)
        so = api.seal_outcome(
            box, f["subject"], got["outcome"], proof=proof(got["subject"])
        )
        assert so["sealed"], so
        assert seal_tip(box, so["tip"])["sealed"]
    graded = {
        r["id"]: r for r in api.reconcile(box, human_key=key_file)["wagers"]["rows"]
    }
    assert graded["win"]["state"] == graded["lose"]["state"] == "reconciled"
    assert graded["win"]["brier"] < graded["lose"]["brier"]  # the loser is graded too
    assert graded["lose"]["graded_by"] is None


def test_a_sealed_outcome_the_close_does_not_say_escalates(box, key_file):
    """The human may seal what they like; it is not the market's outcome."""
    f = committed(box)
    resolved(box)
    wrong = {"path": "no"}
    so = api.seal_outcome(box, f["subject"], wrong,
                          proof=proof(reverse.outcome_subject(f["subject"], wrong)))  # fmt: skip
    assert seal_tip(box, so["tip"])["sealed"]
    w = api.reconcile(box, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "escalate" and "not what the market row says" in w["reason"]


def test_a_tip_sealed_after_the_open_escalates(box, key_file):
    """Forbidden act: calling in time but committing once the market was known."""
    f = call(box)
    box.beacon.at_deadline_of(DAY)
    assert seal_tip(box, f["tip"])["sealed"]
    resolved(box)
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "tip seal was made after" in got["reason"]


def test_a_row_before_the_sealed_tip_escalates(box, key_file):
    """Forbidden act: the close on record before the human committed the call."""
    f = call(box)
    resolved(box)
    assert seal_tip(box, f["tip"])["sealed"]
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "T5" in got["reason"]


def _forge_market_row(cfg, **over):
    from onescript import table

    row = {"what": "market", "session": market.SESSION.format(DAY), "symbol": "SPY",
           "close_date": DAY, **CLOSES[("SPY", DAY)], "verdict": "closed",
           "reason": "x", **over}  # fmt: skip
    assert table.verdict(cfg, row)["code"] == 0


def test_a_close_the_reader_does_not_give_escalates(box, key_file):
    """Forbidden act: a row saying another close."""
    f = committed(box)
    _forge_market_row(box, close="500.00")
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "the price reader says" in got["reason"]


def test_a_close_on_record_before_the_call_escalates(box, key_file):
    """Forbidden act: calling a close already on record (T4)."""
    _forge_market_row(box)
    f = committed(box)
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "T4" in got["reason"]


def test_two_closes_for_one_day_escalate(box, key_file):
    f = committed(box)
    resolved(box)
    _forge_market_row(box)
    got = market.outcome(box, f["subject"], human_key=key_file)
    assert (
        got["state"] == "escalate" and "one close per symbol and date" in got["reason"]
    )


def test_no_price_reader_at_the_outcome_is_unreachable(box, key_file):
    f = committed(box)
    resolved(box)
    box = dataclasses.replace(box, market=None)
    assert (
        market.outcome(box, f["subject"], human_key=key_file)["state"] == "unreachable"
    )


def test_no_human_key_is_unreachable_not_escalate(box):
    f = committed(box)
    resolved(box)
    got = market.propose_outcome(rows(box), f["subject"], None, box.market, {})
    assert got["state"] == "unreachable"


def test_a_plain_wager_is_not_a_market_call(box, key_file):
    w = api.forecast(box, "plain", _dist(), by="desk")
    got = market.outcome(box, w["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "not a market call" in got["reason"]


# ── the free price reader ───────────────────────────────────────────────────
CSV = """Date,Open,High,Low,Close,Volume
2026-10-15,575,578,574,577.50,1
2026-10-16,577,580,576,579.10,1
2026-10-19,580,582,579,581.25,1
"""


def test_the_csv_gives_a_day_and_the_trading_day_before_it():
    assert market.parse_daily_csv(CSV, DAY) == {
        "close": "581.25",
        "prev_close": "579.10",
    }


@pytest.mark.parametrize("day", ["2026-10-17", "2026-10-20", "2026-10-15"])
def test_a_weekend_a_future_day_or_no_day_before_raises(day):
    with pytest.raises(LookupError):
        market.parse_daily_csv(CSV, day)


def test_stooq_names():
    assert market.stooq_symbol("SPY") == "spy.us"
    assert market.stooq_symbol("BRK.B") == "brk.b"
    assert market.stooq_symbol("^SPX") == "^spx"


def test_the_deadline_is_before_the_new_york_open_in_both_seasons():
    for day, open_utc in (
        ("2026-07-01", 13 * 3600 + 1800),
        ("2026-12-01", 14 * 3600 + 1800),
    ):
        midnight = market.deadline(day) - market.DEADLINE_UTC * 3600
        assert market.deadline(day) < midnight + open_utc


def test_the_cli_calls_resolves_and_proposes(
    box, key_file, capsys, monkeypatch, tmp_path
):
    from onescript import __main__ as cli

    for k, v in (("CONSTITUTION", box.constitution), ("CI", box.ci),
                 ("ROOT", box.root), ("GROVE", box.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    monkeypatch.setattr(cli.beacon, "drand_reader", box.beacon)
    monkeypatch.setattr(cli.market, "stooq_reader", box.market)
    (tmp_path / "dist.json").write_text(json.dumps(_dist()))
    (tmp_path / "market.json").write_text(json.dumps(UP))
    base = ["--box", str(box.box), "--keys", str(box.keys), "--no-tests", "--phone",
            "--drand", "--prices", "--now", NOW]  # fmt: skip
    assert cli.main([*base, "forecast", "m1", "--dist", str(tmp_path / "dist.json"),
                     "--market", str(tmp_path / "market.json")]) == 0  # fmt: skip
    out = capsys.readouterr().out.splitlines()
    wager = out[0].split()[1]
    assert seal_tip(box, out[1].split()[1])["sealed"]

    assert cli.main([*base, "market-reveal", DAY]) == 0
    out = capsys.readouterr().out
    assert "market SPY 2026-10-19 closed 581.25" in out
    assert json.loads(out.splitlines()[-1])["state"] == "populated"

    assert cli.main([*base, "market-outcome", wager, "--human-key", str(key_file)]) == 0
    out = capsys.readouterr().out.splitlines()
    said = json.loads(out[-1])
    assert said["state"] == "proposed" and out[0] == f"seal {said['subject']}"
