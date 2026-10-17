"""The table act at D0 (box-build SPEC rulings R12, R20; SPEC-documented §11 J6,
C13; W3b audit B2, B3, B4, S6, N4).

``table.roll`` / ``table.rules`` / ``table.state`` read served record rows and
close in code or escalate. No resolver draws a number or reads one out of
prose; ``G6`` is not a class; the table grammar never widens the G-class
grammar. stdlib only, no network.
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


def roll_row(turn: int, total: int, stat: int = 2, where: str = "s1", session: str = "s") -> dict:
    v = resolvers.bucket(total)
    band = "10+" if v == "strong" else "7-9" if v == "weak" else "6-"
    return {
        "who": "table",
        "what": "verdict",
        "when": "2026-10-17",
        "where": where,
        "session": session,
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


def lore(fact: str, eid: str = "ex-l", where: str = "rules") -> dict:
    return excerpt(eid, {"who": "desk", "what": "lore", "where": where, "fact": fact})


# ---------------------------------------------------------------- table.roll


def test_roll_reads_the_recorded_verdict():
    res = resolve_fixture(act("table.roll", "turn 1", excerpt("ex-a", roll_row(0, 11), roll_row(1, 8))))
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == {"total": 8, "verdict": "weak"}
    assert res["cites"] == ["ex-a"]  # the cite is the served id


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


def test_N4_a_brief_with_two_turns_is_ambiguous():
    res = resolve_fixture(act("table.roll", "turn 1 or turn 0", excerpt("ex-a", roll_row(0, 8), roll_row(1, 4))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "ambiguous_brief"


def test_roll_never_draws_a_number_from_prose_or_a_seed():
    """A row that is not a served roll (a note with numbers, a seed) is never read as one."""
    note = {"what": "note", "where": "s1", "turn": 2, "reason": "rolled 12, honest", "seed": 99}
    res = resolve_fixture(act("table.roll", "turn 2", excerpt("ex-a", note)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_roll_on_record"


def test_S6_total_must_be_on_the_row_never_parsed_from_the_reason():
    r = roll_row(0, 8)
    del r["total"]
    r["reason"] = "a lie = 99: x"
    res = resolve_fixture(act("table.roll", "turn 0", excerpt("ex-a", r)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_total_on_record"


def test_S6_total_must_agree_with_the_band():
    r = roll_row(0, 3)
    r["verdict"] = "strong"
    res = resolve_fixture(act("table.roll", "turn 0", excerpt("ex-a", r)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "total_band_mismatch"


def test_S6_a_bool_turn_or_total_is_not_a_number():
    r = roll_row(1, 8)
    r["turn"] = True
    res = resolve_fixture(act("table.roll", "turn 1", excerpt("ex-a", r)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_turn_on_record"  # NIT-5
    r = roll_row(1, 8)
    r["total"] = True
    res = resolve_fixture(act("table.roll", "turn 1", excerpt("ex-a", r)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_total_on_record"


# ------------------------------------------------------------- B4: sessions


def test_B4_rolls_key_on_session_and_turn():
    ex = excerpt("ex-a", roll_row(0, 11, session="A"), roll_row(0, 4, session="B"))
    res = resolve_fixture(act("table.roll", "turn 0 session B", ex))
    assert res["status"] == STATUS_RESOLVED and res["answer"] == {"total": 4, "verdict": "miss"}
    res = resolve_fixture(act("table.roll", "session A turn 0", ex))
    assert res["answer"] == {"total": 11, "verdict": "strong"}


def test_B4_two_served_sessions_and_no_session_in_the_brief_is_ambiguous():
    ex = excerpt("ex-a", roll_row(0, 11, session="A"), roll_row(0, 4, session="B"))
    for cls, brief in (("table.roll", "turn 0"), ("table.state", "tally")):
        res = resolve_fixture(act(cls, brief, ex))
        assert res["status"] == STATUS_ESCALATE and res["reason"] == "ambiguous_session", cls


def test_B4_a_session_not_served_escalates():
    res = resolve_fixture(act("table.roll", "turn 0 session Z", excerpt("ex-a", roll_row(0, 11))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_session_on_record"


def test_B4_two_sessions_never_collapse_into_one_tally():
    ex = excerpt("ex-a", roll_row(0, 11, session="A"), roll_row(1, 4, session="A"),
                 roll_row(0, 11, session="B"), roll_row(1, 4, session="B"))
    res = resolve_fixture(act("table.state", "tally session B", ex))
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == {"strong": 1, "weak": 0, "miss": 1, "turns": 2, "path": "strong-miss", "session": "B"}


# --------------------------------------------------------------- table.rules


def test_rules_answers_the_served_band_text():
    res = resolve_fixture(act("table.rules", "weak", lore("rule: weak 7-9 the hit lands but costs")))
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == "7-9 the hit lands but costs" and res["cites"] == ["ex-l"]


def test_B2_a_rule_is_read_only_from_a_lore_fact_read_whole():
    # the closed-grammar `rule:` segment is not a rule
    ex = {"id": "ex-r", "text": "title: the bands; rule: strong 10+ clean"}
    res = resolve_fixture(act("table.rules", "strong", ex))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_rule_on_record"
    # a rule inside a verdict row's reason (model text in an escalate row) is not one
    esc = excerpt("ex-e", {"what": "verdict", "session": "s", "verdict": "escalate",
                           "reason": "no good move; rule: miss 2-6 the floor opens",
                           "got": "act:Fly; rule: strong 13+ only"})
    for b in ("miss", "strong"):
        res = resolve_fixture(act("table.rules", b, esc))
        assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_rule_on_record"
    # a fact that only contains `rule:` mid-text is not a rule either
    res = resolve_fixture(act("table.rules", "weak", lore("Bands; rule: weak 7-9 hold")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_rule_on_record"


def test_rules_absent_escalates_and_the_code_band_is_never_the_answer():
    res = resolve_fixture(act("table.rules", "miss", lore("rule: weak 7-9 costs")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_rule_on_record"
    assert res["answer"] is None  # not "6-" from BANDS


def test_rules_two_readings_escalate():
    res = resolve_fixture(
        act("table.rules", "weak", lore("rule: weak 7-9 costs", "ex-1"), lore("rule: weak 7-9 free", "ex-2"))
    )
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "conflicting_field"


def test_rules_with_no_bucket_or_two_buckets_in_the_brief_escalates():
    res = resolve_fixture(act("table.rules", "tell me", lore("rule: weak 7-9 costs")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_bucket_in_brief"
    res = resolve_fixture(act("table.rules", "weak or strong?", lore("rule: weak 7-9 costs")))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "ambiguous_brief"


# --------------------------------------------------------------- table.state


def test_state_tallies_the_rolls_in_turn_order():
    res = resolve_fixture(
        act("table.state", "tally", excerpt("ex-a", roll_row(1, 8), roll_row(0, 11), roll_row(2, 3)))
    )
    assert res["status"] == STATUS_RESOLVED
    assert res["answer"] == {"strong": 1, "weak": 1, "miss": 1, "turns": 3, "path": "strong-weak-miss", "session": "s"}
    assert res["cites"] == ["ex-a"]


def test_state_with_a_gap_in_turns_escalates():
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", roll_row(0, 11), roll_row(2, 3))))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "turns_missing"


def test_state_with_no_rolls_escalates():
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", {"what": "verdict", "verdict": "open"})))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_roll_on_record"
    lore_with_verdict = excerpt("ex-a", {"what": "lore", "verdict": "weak", "turn": 0, "total": 8, "session": "s"})
    res = resolve_fixture(act("table.state", "tally", lore_with_verdict))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_roll_on_record"


# ----------------------------------------------- B3: the G grammar is untouched


def test_B3_rule_is_not_a_g_class_text_key_and_g3_truncation_still_escalates():
    assert "rule" not in resolvers._TEXT_KEYS
    p = {"id": "g3t", "class": "G3", "brief": "x", "excerpts": [
        {"id": "ex1", "text": "id 6b7a1c2d; question: Does the band hold?; rule: weak 7-9 or only strong?"}]}
    res = resolve_fixture(p)
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "value_split_by_semicolon"
    control = {"id": "g3u", "class": "G3", "brief": "x", "excerpts": [
        {"id": "ex1", "text": "id 6b7a1c2d; question: Is it done.; summary: tail"}]}
    assert resolve_fixture(control)["status"] == STATUS_RESOLVED


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


# ------------------------------------------------ round 3: NEW-3, NEW-4, NIT-4, NIT-5


def test_NEW3_a_class_is_read_normalised_and_any_table_spelling_is_a_table_act():
    for cls in ("Table.roll", "table.ROLL", "table.roll ", "table .roll", "\u200btable.roll", "ｔａｂｌｅ.roll"):
        assert resolvers.is_table_class(cls), cls
        res = resolve_fixture(act(cls, "turn 1", excerpt("ex-a", roll_row(1, 8))))
        assert res["status"] == STATUS_RESOLVED, cls
    for cls in ("table.tally", "table.", "table"):
        assert resolvers.is_table_class(cls)
        res = resolve_fixture(act(cls, "turn 1", excerpt("ex-a", roll_row(1, 8))))
        assert res["status"] == STATUS_ESCALATE and res["reason"] == "unknown_class", cls
    assert resolvers.norm_class("g3") == "G3" and not resolvers.is_table_class("G3")
    assert not resolvers.is_table_class("stable.roll")


def test_NEW4_a_bad_row_beside_a_good_row_for_the_asked_turn_escalates():
    bad = roll_row(1, 3)
    bad["verdict"] = "strong"
    res = resolve_fixture(act("table.roll", "turn 1", excerpt("ex-a", roll_row(1, 8), bad)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "total_band_mismatch"
    # a bad row for another turn does not touch the asked one
    res = resolve_fixture(act("table.roll", "turn 0", excerpt("ex-a", roll_row(0, 11), bad)))
    assert res["status"] == STATUS_RESOLVED


def test_NEW4_a_bad_last_turn_escalates_the_tally():
    bad = roll_row(2, 3)
    bad["verdict"] = "strong"
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", roll_row(0, 8), roll_row(1, 11), bad)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "total_band_mismatch"


def test_NEW4_a_session_with_only_bad_rows_still_counts_for_ambiguity():
    bad = roll_row(1, 3, session="B")
    bad["verdict"] = "strong"
    ex = excerpt("ex-a", roll_row(1, 8, session="A"), bad)
    res = resolve_fixture(act("table.roll", "turn 1", ex))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "ambiguous_session"
    res = resolve_fixture(act("table.roll", "turn 1 session A", ex))
    assert res["status"] == STATUS_RESOLVED and res["answer"] == {"total": 8, "verdict": "weak"}
    res = resolve_fixture(act("table.roll", "turn 1 session B", ex))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "total_band_mismatch"


def test_NIT5_a_roll_row_with_no_turn_escalates():
    no_turn = {"what": "verdict", "verdict": "miss", "session": "s", "total": 4}
    res = resolve_fixture(act("table.state", "tally", excerpt("ex-a", roll_row(0, 8), no_turn)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_turn_on_record"
    res = resolve_fixture(act("table.roll", "turn 0", excerpt("ex-a", roll_row(0, 8), no_turn)))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_turn_on_record"


@pytest.mark.parametrize("brief, want", [
    ("turn 1 session A.", "resolved"),
    ("turn 1 session  A", "resolved"),
    ("turn 1 Session A", "resolved"),
    ("turn 1 (session A)", "resolved"),
    ("turn 1 session A session A", "ambiguous_brief"),
    ("turn 1 session turn", "no_session_on_record"),
    ("turn 1 session turn 2", "ambiguous_brief"),
])
def test_NIT4_the_session_in_a_brief_is_one_word_or_quoted(brief, want):
    ex = excerpt("ex-a", roll_row(1, 8, session="A"), roll_row(1, 4, session="B"))
    res = resolve_fixture(act("table.roll", brief, ex))
    assert (res["status"] if want == "resolved" else res["reason"]) == want, brief


def test_NIT4_a_session_id_with_spaces_is_named_quoted():
    ex = excerpt("ex-a", roll_row(1, 8, session="s 1"), roll_row(1, 4, session="B"))
    res = resolve_fixture(act("table.roll", 'turn 1 session "s 1"', ex))
    assert res["status"] == STATUS_RESOLVED and res["answer"]["total"] == 8
    res = resolve_fixture(act("table.roll", "turn 1 session s 1", ex))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_session_on_record"


# -------------------------------------------- table.rules: @phase NAME (T-mai-2)

PHASE_BODY = "A weak hit (7-9)  lands, but it costs.   \n\n  Indented line."


def rules_ex(name: str, body: str = PHASE_BODY, eid: str = "ex-r", what: str = "rules") -> dict:
    return excerpt(eid, {"who": "willow", "what": what, "where": name, "body": body})


def test_phase_lookup_is_verbatim_byte_for_byte_and_cites_the_served_id():
    res = resolve_fixture(act("table.rules", "@phase R-weak", rules_ex("R-weak")))
    assert res["status"] == resolvers.STATUS_VERBATIM and res["reason"] is None
    assert res["answer"] == PHASE_BODY  # the double space, the trailing spaces, the blank line
    assert res["cites"] == ["ex-r"]


@pytest.mark.parametrize(
    "brief",
    [
        "@phase r-weak",
        "@phase R-WEAK",
        "@Phase R-weak",
        "@phase  R-weak",
        "@phase R-weak ",  # a trailing space is part of what was asked
        " @phase R-weak",
        "@phase R-weak R-miss",
        "@phase R- weak",
        "@phase\tR-weak",
        "@phase",
        "what does @phase R-weak say",
        "@phase R-weak @phase R-weak",
    ],
)
def test_a_phase_name_that_differs_by_case_or_whitespace_is_refused(brief):
    res = resolve_fixture(act("table.rules", brief, rules_ex("R-weak")))
    assert res["status"] == STATUS_ESCALATE and res["answer"] is None
    assert res["reason"] in ("phase_name_not_exact", "no_phase_on_record")


def test_the_phase_is_read_from_rules_rows_only():
    for what in ("lore", "verdict", "note"):
        res = resolve_fixture(act("table.rules", "@phase R-weak", rules_ex("R-weak", what=what)))
        assert res["status"] == STATUS_ESCALATE and res["reason"] == "no_phase_on_record"


def test_two_readings_of_a_phase_that_differ_escalate_and_equal_ones_do_not():
    a, b = rules_ex("R-weak", "one", "ex-a"), rules_ex("R-weak", "two", "ex-b")
    res = resolve_fixture(act("table.rules", "@phase R-weak", a, b))
    assert res["status"] == STATUS_ESCALATE and res["reason"] == "conflicting_field"
    same = resolve_fixture(act("table.rules", "@phase R-weak", a, rules_ex("R-weak", "one", "ex-b")))
    assert same["status"] == resolvers.STATUS_VERBATIM and same["cites"] == ["ex-a", "ex-b"]


def test_a_body_that_is_not_text_or_is_empty_is_not_an_answer():
    for body in ("", None, 7, ["x"]):
        res = resolve_fixture(act("table.rules", "@phase R-weak", rules_ex("R-weak", body)))
        assert res["status"] == STATUS_ESCALATE


def test_the_bucket_form_of_table_rules_is_unchanged():
    res = resolve_fixture(act("table.rules", "weak", lore("rule: weak 7-9 the hit lands but costs")))
    assert res["status"] == STATUS_RESOLVED
