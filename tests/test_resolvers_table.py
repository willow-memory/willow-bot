"""The table act at D0 (box-build SPEC ruling R12; SPEC-documented §11 J6, C13).

``table.roll`` / ``table.rules`` / ``table.state`` read served record rows and
close in code or escalate. No resolver here draws a number, and ``G6`` is not
a class. stdlib only, no network.
"""
from __future__ import annotations

import json
import random

import pytest

from willow_bot.deterministic import resolvers
from willow_bot.deterministic.resolvers import (
    STATUS_ESCALATE,
    STATUS_RESOLVED,
    resolve_fixture,
    score_resolution,
)


def roll_row(turn: int, total: int, stat: int = 2, where: str = "s1") -> dict:
    v = resolvers.bucket = "strong" if total >= 10 else "weak" if total >= 7 else "miss"
    band = "10+" if v == "strong" else "7-9" if v == "weak" else "6-"
    return {
        "who": "table",
        "what": "verdict",
        "when": "2026-10-17",
        "where": where,
        "turn": turn,
        "total": total,
        "verdict": v,
        "reason": f"2d6+{stat} = {total}: {band}",
    }


def excerpt(eid: str, *rows: dict) -> dict:
    return {"id": eid, "text": "\n".join(json.dumps(r, sort_keys=True) for r in rows)}


def act(cls: str, brief: str, *excerpts: dict, expected: dict | None = None) -> dict:
    return {"id": "t", "class": cls, "brief": brief, "excerpts": list(excerpts),
            "expected": expected or {}}


# ---------------------------------------------------------------- table.roll


def test_roll_reads_the_recorded_verdict():
    res = resolve_fixture(act("table.roll", "turn 1", excerpt("ex-a", roll_row(0, 11), roll_row(1, 8))))
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == {"total": 8, "verdict": "weak"}
    assert res["cites"] == ["ex-a"]  # the cite is the served id


def test_roll_total_falls_back_to_the_reason_when_the_row_has_none():
    r = roll_row(3, 5)
    del r["total"]
    res = resolve_fixture(act("table.roll", "turn 3", excerpt("ex-a", r)))
    assert res["status"] == STATUS_RESOLVED and res["answer"] == {"total": 5, "verdict": "miss"}


def test_roll_absent_escalates_not_invents():
    res = resolve_fixture(act("table.roll", "turn 7", excerpt("ex-a", roll_row(0, 11))))
    assert res["status"] == STATUS_ESCALATE
    assert res["reason"] == "no_roll_on_record" and res["answer"] is None


def test_two_verdicts_one_turn_escalates():
    res = resolve_fixture(
        act("table.roll", "turn 0", excerpt("ex-a", roll_row(0, 11)), excerpt("ex-b", roll_row(0, 4)))
    )
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "conflicting_field"


def test_the_same_roll_served_twice_is_not_a_conflict():
    res = resolve_fixture(
        act("table.roll", "turn 0", excerpt("ex-a", roll_row(0, 11)), excerpt("ex-b", roll_row(0, 11)))
    )
    assert res["status"] == STATUS_RESOLVED and res["cites"] == ["ex-a", "ex-b"]


def test_roll_with_no_turn_in_the_brief_escalates():
    res = resolve_fixture(act("table.roll", "the last one", excerpt("ex-a", roll_row(0, 11))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_turn_in_brief"


def test_roll_never_draws_a_number_from_prose_or_a_seed():
    """A row that is not a served roll (a note with numbers, a seed) is never read as one."""
    note = {"what": "note", "where": "s1", "turn": 2, "reason": "rolled 12, honest", "seed": 99}
    res = resolve_fixture(act("table.roll", "turn 2", excerpt("ex-a", note)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_roll_on_record"


# --------------------------------------------------------------- table.rules


def lore(fact: str, eid: str = "ex-l", where: str = "rules") -> dict:
    return excerpt(eid, {"who": "desk", "what": "lore", "where": where, "fact": fact})


def test_rules_answers_the_served_band_text():
    res = resolve_fixture(act("table.rules", "weak", lore("rule: weak 7-9 the hit lands but costs")))
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == "7-9 the hit lands but costs" and res["cites"] == ["ex-l"]


def test_rules_reads_the_closed_grammar_rule_key_too():
    ex = {"id": "ex-r", "text": "title: the bands; rule: strong 10+ clean"}
    res = resolve_fixture(act("table.rules", "what does strong mean", ex))
    assert res["status"] == STATUS_RESOLVED and res["answer"] == "10+ clean"


def test_rules_absent_escalates_and_the_code_band_is_never_the_answer():
    res = resolve_fixture(act("table.rules", "miss", lore("rule: weak 7-9 costs")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_rule_on_record"
    assert res["answer"] is None  # not "6-" from BANDS


def test_rules_two_readings_escalate():
    res = resolve_fixture(
        act("table.rules", "weak", lore("rule: weak 7-9 costs", "ex-1"), lore("rule: weak 7-9 free", "ex-2"))
    )
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "conflicting_field"


def test_rules_with_no_bucket_in_the_brief_escalates():
    res = resolve_fixture(act("table.rules", "tell me", lore("rule: weak 7-9 costs")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_bucket_in_brief"


# --------------------------------------------------------------- table.state


def test_state_tallies_the_rolls_in_turn_order():
    res = resolve_fixture(
        act("table.state", "tally", excerpt("ex-a", roll_row(1, 8), roll_row(0, 11), roll_row(2, 3)))
    )
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == {"strong": 1, "weak": 1, "miss": 1, "turns": 3, "path": "strong-weak-miss"}
    assert res["cites"] == ["ex-a"]


def test_state_with_a_gap_in_turns_escalates():
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", roll_row(0, 11), roll_row(2, 3))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "turns_missing"


def test_state_with_no_rolls_escalates():
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", {"what": "verdict", "verdict": "open"})))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_roll_on_record"


# ----------------------------------------------------- classes and precision


def test_G6_is_not_a_class_and_an_unknown_table_class_escalates():
    assert resolve_fixture({"id": "x", "class": "G6", "excerpts": []})["reason"] == "unknown_class"
    res = resolve_fixture(act("table.seal", "seal it", excerpt("ex-a", roll_row(0, 11))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "unknown_class"
    assert set(resolvers.TABLE_CLASSES) == {"table.roll", "table.rules", "table.state"}
    assert not any(c.startswith("G") for c in resolvers.TABLE_CLASSES)


def test_precision_is_one():
    """30 generated fixtures with gold: every resolved answer equals gold, and
    every fixture with no row for its turn escalates rather than answering."""
    rng = random.Random(42)
    resolved = scored = 0
    for i in range(30):
        rows = [roll_row(t, rng.randint(2, 12)) for t in range(rng.randint(1, 5))]
        ask = rng.randint(0, 6)
        gold = next(({"total": r["total"], "verdict": r["verdict"]} for r in rows if r["turn"] == ask), None)
        fx = act("table.roll", f"turn {ask}", excerpt(f"ex-{i}", *rows),
                 expected={"answer": gold, "must_cite": [f"ex-{i}"]} if gold else None)
        res = resolve_fixture(fx)
        if gold is None:
            assert res["status"] == STATUS_ESCALATE
            assert score_resolution(fx, res)["scored"] is False
            continue
        assert res["status"] == STATUS_RESOLVED
        sc = score_resolution(fx, res)
        scored += 1
        resolved += sc["correct"] is True
    assert scored and resolved == scored  # precision 1.0


def test_a_table_act_is_not_in_the_g_class_scorer():
    fx = act("table.roll", "turn 0", excerpt("ex-a", roll_row(0, 11)))
    sc = score_resolution(fx, resolve_fixture(fx))
    assert sc == {"scored": False, "correct": None, "detail": "table_no_gold"}
    summary = resolvers.summarize([{**resolve_fixture(fx), **sc, "score_detail": sc["detail"]}])
    assert summary["resolved_total"] == 0  # no gold: never counted as a resolved score


@pytest.mark.parametrize("cls", ["G1", "G2", "G3", "G4", "G5"])
def test_g_classes_still_dispatch_as_before(cls):
    """The flowering battery's classes are untouched: an empty act of each
    escalates (or, for G4, asks for flowering) exactly as it did."""
    res = resolve_fixture({"id": "x", "class": cls, "excerpts": []})
    assert res["class"] == cls and res["reason"] != "unknown_class"
