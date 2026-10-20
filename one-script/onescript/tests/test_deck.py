"""The predictions game's deck as a reader (flatten F6, SPEC-rival R1).

A deck forecast is a wager on one of the game's twelve questions for a drand
round still ahead; the reveal writes the beacon's answers as `deck` verdict
rows; code proposes each outcome and never seals it; the human's seal and
reconcile grade it. Every forbidden act is tried: a round already out, a role
on both sides, a row before the forecast's sealed tip, an answer the
randomness does not give, randomness the reader does not say.
"""
# ruff: noqa: F811

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from onescript import api, deck, record, reverse, table
from onescript.tests.test_table import (  # noqa: F401  (the box and its keys)
    HUMAN_KEY,
    RANDOMNESS,
    ROUND,
    cfg,
    key_file,
    proof,
    rows,
    seal_tip,
    tip_now,
)

PLAY = (
    Path(__file__).resolve().parents[4]
    / "willows-grove/seat/willow/experiments/predictions-2026-10-17/play.py"
)
Q = "3d6"
YES_ON_RANDOMNESS = deck.answer(RANDOMNESS, Q)


def _dist(p=0.3):
    return [{"path": "yes", "p": p}, {"path": "no", "p": round(1 - p, 10)}]


def forecast(cfg, wid="d1", q=Q, by="desk", p=0.3, rnd=ROUND):
    return api.forecast(
        cfg, wid, _dist(p), by=by, deck={"question": q, "beacon_round": rnd}
    )


def committed(cfg, **kw):
    f = forecast(cfg, **kw)
    assert f["code"] == 0, f
    assert seal_tip(cfg, f["tip"])["sealed"]
    return f


def revealed(cfg):
    cfg.beacon.fired = True
    got = deck.reveal(cfg, ROUND)
    assert got["state"] == "populated", got
    return got


# ── the deck itself ────────────────────────────────────────────────────────
def test_twelve_questions_each_with_a_draw_and_exact_odds():
    assert len(deck.DECK) == 12
    for q in deck.DECK:
        assert 0 < deck.odds(q) < 1
        o = deck.oracle(q)
        assert [e["path"] for e in o] == ["yes", "no"] and abs(
            sum(e["p"] for e in o) - 1
        ) < 1e-12
        assert isinstance(deck.answer(RANDOMNESS, q), bool)


@pytest.mark.skipif(
    not PLAY.is_file(), reason=f"play.py is not beside the bot at {PLAY}"
)
def test_every_draw_and_every_odds_is_play_pys():
    spec = importlib.util.spec_from_file_location("_play", PLAY)
    play = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(play)  # type: ignore[union-attr]
    assert set(play.DECK) == set(deck.DECK)
    for i in range(40):
        r = record.h256(f"seed {i}")
        for q in deck.DECK:
            theirs = bool(play.DECK[q][1](play.Stream(bytes.fromhex(r) + q.encode())))
            assert deck.answer(r, q) == theirs, (q, i)
    for q in deck.DECK:
        assert deck.odds(q) == play.DECK[q][2]() and deck.DECK[q][0] == play.DECK[q][0]


# ── predict ────────────────────────────────────────────────────────────────
def test_a_deck_forecast_is_a_wager_naming_its_question_and_round(cfg):
    f = forecast(cfg)
    assert f["code"] == 0
    w = f["row"]
    assert w["kind"] == "wager" and w["deck"] == {"question": Q, "beacon_round": ROUND}
    assert isinstance(w["beacon_latest"], int) and w["beacon_latest"] < ROUND


@pytest.mark.parametrize(
    "kw, said",
    [
        ({"rnd": 5}, "out already"),
        ({"q": "nope"}, "no deck question"),
    ],
)
def test_a_forecast_on_a_round_out_or_no_question_is_refused(cfg, kw, said):
    got = forecast(cfg, **kw)
    assert got["code"] == 1 and said in got["refused"]


def test_paths_other_than_yes_and_no_are_refused(cfg):
    got = api.forecast(cfg, "d1", [{"path": "maybe", "p": 1.0}], by="desk",
                       deck={"question": Q, "beacon_round": ROUND})  # fmt: skip
    assert got["code"] == 1 and "yes and no" in got["refused"]


def test_a_role_cannot_hold_both_sides(cfg):
    """Forbidden act: one role forecasting a question twice for a round."""
    assert forecast(cfg, wid="d1", p=0.9)["code"] == 0
    got = forecast(cfg, wid="d2", p=0.1)
    assert (
        got["code"] == 1
        and "one forecast per round, question and role" in got["refused"]
    )
    assert forecast(cfg, wid="d3", by="human", p=0.1)["code"] == 0  # another role may


def test_a_forecast_is_a_table_world_or_a_deck_question_not_both(cfg):
    got = api.forecast(cfg, "d1", _dist(), by="desk",
                       table={"world": "w", "sheets_sha": "s", "beacon_round": ROUND},
                       deck={"question": Q, "beacon_round": ROUND})  # fmt: skip
    assert got["code"] == 1 and "not both" in got["refused"]


# ── reveal ─────────────────────────────────────────────────────────────────
def test_reveal_before_the_round_fires_writes_nothing_and_says_why(cfg):
    committed(cfg)
    n = len(rows(cfg))
    got = deck.reveal(cfg, ROUND)
    assert got["state"] == "unreachable" and "could not be read" in got["why"]
    assert len(rows(cfg)) == n


def test_reveal_writes_one_deck_row_per_question_asked_as_table(cfg):
    committed(cfg, wid="d1", q=Q)
    committed(cfg, wid="d2", q="birthday")
    committed(cfg, wid="d3", q=Q, by="human")  # the same question: one row
    got = revealed(cfg)
    assert sorted(r["question"] for r in got["rows"]) == ["3d6", "birthday"]
    for r in got["rows"]:
        assert r["kind"] == "verdict" and r["who"] == "table" and r["what"] == "deck"
        assert r["randomness"] == RANDOMNESS and r["beacon_round"] == ROUND
        assert r["verdict"] == (
            "yes" if deck.answer(RANDOMNESS, r["question"]) else "no"
        )
    again = deck.reveal(cfg, ROUND)
    assert again["state"] == "empty" and "answered" in again["why"]


def test_reveal_with_no_forecast_on_the_round_is_empty(cfg):
    cfg.beacon.fired = True
    got = deck.reveal(cfg, ROUND)
    assert got["state"] == "empty" and "no forecast" in got["why"]


# ── the outcome, proposed; the seal, the human's; the grade ─────────────────
def test_the_outcome_is_proposed_from_the_deck_row_and_never_sealed(cfg, key_file):
    f = committed(cfg)
    revealed(cfg)
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "proposed", got
    assert got["outcome"] == {"path": "yes" if YES_ON_RANDOMNESS else "no"}
    assert not [r for r in rows(cfg) if r["kind"] == "seal"
                and str(r.get("subject", "")).startswith(reverse.WAGER)]  # fmt: skip


def test_the_human_seals_it_and_reconcile_grades_it(cfg, key_file):
    f = committed(cfg)
    revealed(cfg)
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    so = api.seal_outcome(
        cfg, f["subject"], got["outcome"], proof=proof(got["subject"])
    )
    assert so["sealed"], so
    assert seal_tip(cfg, so["tip"])["sealed"]
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "reconciled" and "brier" in w and w["graded_by"] is None


def test_a_sealed_outcome_the_deck_row_does_not_say_escalates(cfg, key_file):
    """The human may seal what they like; it is not the deck's outcome."""
    f = committed(cfg)
    revealed(cfg)
    wrong = {"path": "no" if YES_ON_RANDOMNESS else "yes"}
    sub = reverse.outcome_subject(f["subject"], wrong)
    so = api.seal_outcome(cfg, f["subject"], wrong, proof=proof(sub))
    assert seal_tip(cfg, so["tip"])["sealed"]
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "escalate" and "not what the deck row says" in w["reason"]


def test_a_deck_row_before_the_sealed_tip_escalates(cfg, key_file):
    """Forbidden act: revealed before the human committed the forecast."""
    f = forecast(cfg)
    revealed(cfg)
    assert seal_tip(cfg, tip_now(cfg))["sealed"]
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "T5" in got["reason"]


def _forge_deck_row(cfg, **over):
    row = {"what": "deck", "session": deck.SESSION.format(ROUND), "question": Q,
           "beacon_round": ROUND, "randomness": RANDOMNESS, "verdict": "yes",
           "reason": "x", **over}  # fmt: skip
    assert table.verdict(cfg, row)["code"] == 0


def test_a_row_whose_answer_the_randomness_does_not_give_escalates(cfg, key_file):
    """Forbidden act: a deck row saying the other answer."""
    f = committed(cfg)
    cfg.beacon.fired = True
    _forge_deck_row(cfg, verdict="no" if YES_ON_RANDOMNESS else "yes")
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "answers" in got["reason"]


def test_randomness_the_reader_does_not_say_escalates(cfg, key_file):
    """Forbidden act: a reseeded row (another randomness)."""
    f = committed(cfg)
    cfg.beacon.fired = True
    other = "ab" * 32
    _forge_deck_row(
        cfg, randomness=other, verdict="yes" if deck.answer(other, Q) else "no"
    )
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "not round" in got["reason"]


def test_two_answers_for_one_question_escalate(cfg, key_file):
    f = committed(cfg)
    revealed(cfg)
    _forge_deck_row(cfg, verdict="yes" if YES_ON_RANDOMNESS else "no")
    got = deck.outcome(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "one answer per question" in got["reason"]


def test_no_human_key_is_unreachable_not_escalate(cfg):
    f = committed(cfg)
    revealed(cfg)
    got = deck.propose_outcome(rows(cfg), f["subject"], None, cfg.beacon, {})
    assert got["state"] == "unreachable"


def test_a_plain_wager_is_not_the_decks(cfg, key_file):
    w = api.forecast(cfg, "plain", _dist(), by="desk")
    got = deck.outcome(cfg, w["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "no deck question" in got["reason"]


def test_the_cli_forecasts_reveals_and_proposes(
    cfg, key_file, capsys, monkeypatch, tmp_path
):
    from onescript import __main__ as cli

    for k, v in (("CONSTITUTION", cfg.constitution), ("CI", cfg.ci),
                 ("ROOT", cfg.root), ("GROVE", cfg.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    monkeypatch.setattr(cli.beacon, "drand_reader", cfg.beacon)
    (tmp_path / "dist.json").write_text(json.dumps(_dist()))
    (tmp_path / "deck.json").write_text(
        json.dumps({"question": Q, "beacon_round": ROUND})
    )
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone",
            "--drand", "--now", "2026-10-17T00:00:00Z"]  # fmt: skip
    assert cli.main([*base, "forecast", "d1", "--dist", str(tmp_path / "dist.json"),
                     "--deck", str(tmp_path / "deck.json")]) == 0  # fmt: skip
    out = capsys.readouterr().out.splitlines()
    wager = out[0].split()[1]
    assert seal_tip(cfg, out[1].split()[1])["sealed"]

    cfg.beacon.fired = True
    assert cli.main([*base, "deck-reveal", str(ROUND)]) == 0
    out = capsys.readouterr().out
    assert f"deck {Q}" in out
    assert json.loads(out.splitlines()[-1])["state"] == "populated"

    assert cli.main([*base, "deck-outcome", wager, "--human-key", str(key_file)]) == 0
    out = capsys.readouterr().out.splitlines()
    said = json.loads(out[-1])
    assert said["state"] == "proposed" and out[0] == f"seal {said['subject']}"
    assert cli.main([*base, "deck-reveal", str(ROUND)]) == 0  # answered: empty, said
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["state"] == "empty"


def test_the_cli_says_unreachable_with_exit_1(cfg, capsys, monkeypatch):
    from onescript import __main__ as cli

    for k, v in (("CONSTITUTION", cfg.constitution), ("CI", cfg.ci),
                 ("ROOT", cfg.root), ("GROVE", cfg.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone",
            "--now", "2026-10-17T00:00:00Z"]  # no --drand: offline  # fmt: skip
    assert cli.main([*base, "deck-reveal", str(ROUND)]) == 1
    assert (
        json.loads(capsys.readouterr().out.splitlines()[-1])["state"] == "unreachable"
    )
