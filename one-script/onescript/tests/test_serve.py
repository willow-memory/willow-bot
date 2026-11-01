"""serve: only the tables in scope, by ids the model can't guess."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, serve  # noqa: E402
from onescript.record import Record  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
SERVE_KEY = b"the-runs-serve-key"

IN = {"rows": [{"who": "hanuman", "what": "write"}], "source": "record.jsonl@n=4"}
ALSO = {"rows": [{"who": "willow", "what": "seal"}], "source": "record.jsonl@n=9"}
OUT = {"rows": [{"who": "operator", "what": "PIN 4471"}], "source": "vault@n=1"}


CAP = 10**6  # serve needs the caller's cap; most tests don't care what it is


def capped(*args, **kw):
    kw.setdefault("max_chars", CAP)
    kw.setdefault("human_key", HUMAN_KEY)  # what the seal rows are re-checked by
    return serve.serve(*args, **kw)


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-07T00:00:{next(n):06d}Z"


def box(tmp_path):
    return Record(tmp_path, clock(), "v", gate.token())


def seal(rec, subject):
    proof = gate.sign(HUMAN_KEY, "seal", subject)
    hum = gate.human(subject, proof, HUMAN_KEY)
    rec.append("seal", hum, subject=subject, proof=proof)  # as Run.seal writes it


def scoped(rec, *tables):
    ids = [serve.table_id(t) for t in tables]
    seal(rec, serve.scope_subject(ids))
    return ids


def test_table_id_fails_closed_when_source_rows_misalign():
    rows = [{"who": "hanuman", "what": "write"}, {"who": "run", "what": "boot"}]
    good = {"rows": rows, "source": {"rows": ["a", "b"], "group": {"who": "x"}}}
    assert serve.table_id(good)
    bad = {"rows": rows, "source": {"rows": ["a"], "group": {"who": "x"}}}
    with pytest.raises(ValueError):
        serve.table_id(bad)
    # a source that is not the per-row dict shape keeps working
    assert serve.table_id({"rows": rows, "source": "record.jsonl@n=4"})
    assert serve.table_id({"rows": rows, "source": {"group": {"who": "x"}}})


def test_only_the_scope_is_served_and_the_rest_is_never_named(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    doc = capped(rec, [IN, ALSO, OUT], ids, SERVE_KEY)
    assert doc["state"] == "populated"
    assert len(doc["tables"]) == 2
    raw = (tmp_path / serve.OUT).read_text()
    assert "PIN 4471" not in raw and "vault" not in raw
    assert serve.table_id(OUT) not in raw
    assert set(json.loads(raw)) == {"state", "why", "return", "tables"}  # no count


def test_served_ids_are_keyed_never_the_content_hash(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN], ids, SERVE_KEY)
    raw = (tmp_path / serve.OUT).read_text()
    assert ids[0] not in raw
    assert doc["tables"][0]["id"] == serve.served_id(SERVE_KEY, ids[0])
    assert doc["tables"][0]["id"] != serve.served_id(b"another-key", ids[0])


def test_no_scope_serves_nothing_and_says_why(tmp_path):
    doc = capped(box(tmp_path), [IN], None, SERVE_KEY)
    assert doc == {
        "state": "empty",
        "why": doc["why"],
        "return": serve.RETURN,
        "tables": [],
    }
    assert "no scope" in doc["why"]


def test_an_unsealed_scope_serves_nothing(tmp_path):
    rec = box(tmp_path)
    doc = capped(rec, [IN], [serve.table_id(IN)], SERVE_KEY)
    assert doc["state"] == "empty" and doc["tables"] == []
    assert "no human seal" in doc["why"]


def test_a_seal_binds_to_the_exact_set_never_a_wider_one(tmp_path):
    rec = box(tmp_path)
    scoped(rec, IN)  # sealed {IN}
    wider = [serve.table_id(IN), serve.table_id(OUT)]
    doc = capped(rec, [IN, OUT], wider, SERVE_KEY)
    assert doc["state"] == "empty"
    assert "PIN 4471" not in (tmp_path / serve.OUT).read_text()


def test_a_forged_seal_never_reaches_the_record(tmp_path):
    subject = serve.scope_subject([serve.table_id(IN)])
    try:
        gate.human(subject, gate.sign(b"not-the-key", "seal", subject), HUMAN_KEY)
    except gate.Refused:
        pass
    else:
        raise AssertionError("a forged seal verified")


def test_a_sealed_table_that_is_missing_is_unreachable_not_empty(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    doc = capped(rec, [IN], ids, SERVE_KEY)
    assert doc["state"] == "unreachable" and doc["tables"] == []


def test_trust_comes_from_the_record_not_the_table(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    seal(rec, serve.table_id(ALSO))
    claims = {**IN, "trust": "human-sealed"}  # a table can't label itself
    doc = capped(rec, [claims, ALSO], ids, SERVE_KEY)
    trust = {t["rows"][0]["who"]: t["trust"] for t in doc["tables"]}
    assert trust == {"hanuman": "untrusted", "willow": "human-sealed"}


def test_a_table_without_a_receipt_fails_the_whole_serve(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN, {"rows": [], "source": ""}], ids, SERVE_KEY)
    assert doc["state"] == "empty" and "receipt" in doc["why"]


def test_every_serve_is_recorded_with_where_and_the_file_is_in_the_pile(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN], ids, SERVE_KEY)
    row = rec.rows()[-1]
    assert row["kind"] == "serve" and row["where"] == serve.OUT
    assert row["served"] == [doc["tables"][0]["id"]]
    assert row["scope"] == serve.scope_subject(ids)
    ptr = {i["where"]: i for i in rec.pile()["items"]}[serve.OUT]
    assert ptr["sha"] == row["sha"] and ptr["provenance"] == "third-party"
    assert rec.verify_chain() == []


def test_a_cite_outside_the_served_file_is_link_fail(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN], ids, SERVE_KEY)
    good = doc["tables"][0]["id"]
    guessed = serve.served_id(SERVE_KEY, serve.table_id(OUT))
    fails = serve.check_cites(doc, [good, guessed, ids[0]])
    assert [f["cite"] for f in fails] == [guessed, ids[0]]
    assert {f["verdict"] for f in fails} == {"link_fail"}


# ── the stack: piles from the record, proposed by code, sealed by the human ──
def activity(rec):
    hanuman = gate.Verified("hanuman", "qwen", gate.token())
    willow = gate.Verified("willow", "claude", gate.token())
    rec.write_file(hanuman, "docs/a.md", b"a", provenance="authored")
    rec.write_file(willow, "docs/a.md", b"a2", provenance="authored")
    rec.write_file(hanuman, "docs/b.md", b"b", provenance="authored")
    rec.append("door", hanuman, verdict="pass")  # no where: in no where-pile


def test_piles_group_the_record_by_the_ws_and_skip_what_has_no_where(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    got = {
        serve.name(p): len(p["rows"]) for p in serve.piles(rec.rows(), "who", "where")
    }
    assert got == {
        "who=hanuman · where=docs/a.md": 1,
        "who=hanuman · where=docs/b.md": 1,
        "who=willow · where=docs/a.md": 1,
    }
    by_what = {serve.name(p) for p in serve.piles(rec.rows(), "what")}
    assert "what=door" in by_what


def test_a_pile_receipt_is_its_group_and_each_rows_number_and_hash(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    pile = serve.piles(rec.rows(), "where")[0]
    assert pile["source"]["group"] == {"where": "docs/a.md"}
    assert pile["source"]["rows"] == [[r["n"], r["hash"]] for r in pile["rows"]]


def test_code_proposes_the_stack_and_the_seal_makes_it_the_scope(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    stack = serve.piles(rec.rows(), "who", "where")
    card = serve.propose(stack, where="docs/a.md")
    assert [s["name"] for s in card["stack"]] == [
        "who=hanuman · where=docs/a.md",
        "who=willow · where=docs/a.md",
    ]
    assert capped(rec, stack, card["ids"], SERVE_KEY)["state"] == "empty"
    seal(rec, card["subject"])
    doc = capped(rec, stack, card["ids"], SERVE_KEY)
    assert doc["state"] == "populated" and len(doc["tables"]) == 2
    raw = (tmp_path / serve.OUT).read_text()
    assert "docs/b.md" not in raw  # the pile out of the stack isn't there


def test_a_proposal_that_matches_nothing_has_nothing_to_seal(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    card = serve.propose(serve.piles(rec.rows(), "where"), where="docs/z.md")
    assert card == {"subject": None, "ids": [], "stack": [], "joins": {}}


def test_a_pile_whose_receipt_does_not_match_the_record_serves_nothing(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    pile = serve.piles(rec.rows(), "where")[0]
    forged = {
        **pile,
        "rows": [{**pile["rows"][0], "who": "operator"}, *pile["rows"][1:]],
    }
    ids = [serve.table_id(forged)]
    seal(rec, serve.scope_subject(ids))
    doc = capped(rec, [forged], ids, SERVE_KEY)
    assert doc["state"] == "empty" and "receipt" in doc["why"]


# ── what the model reads: the view, not the record ───────────────────────────
HEX64 = re.compile(r"[0-9a-f]{64}")


def served_where(tmp_path, where="docs/a.md"):
    rec = box(tmp_path)
    activity(rec)
    rec.write_file(
        gate.Verified("willow", "claude", gate.token()),
        "docs/secret.md",
        b"s",
        provenance="authored",
    )
    stack = serve.piles(rec.rows(), "where")
    card = serve.propose(stack, where=where)
    seal(rec, card["subject"])
    return rec, stack, card, capped(rec, stack, card["ids"], SERVE_KEY)


def test_the_model_cannot_count_name_or_reverse_the_rest(tmp_path):
    _, _, _, doc = served_where(tmp_path)
    raw = (tmp_path / serve.OUT).read_text()
    for row in (r for t in doc["tables"] for r in t["rows"]):
        assert not {"n", "hash", "prev", "sha", "version"} & set(row)
    assert "source" not in doc["tables"][0]  # the receipt stays in the record
    ids = {t["id"] for t in doc["tables"]}
    assert set(HEX64.findall(raw)) == ids  # the only hashes are its own ids
    assert "secret" not in raw


def test_a_hash_inside_served_text_is_withheld(tmp_path):
    rec = box(tmp_path)
    who = gate.Verified("hanuman", "qwen", gate.token())
    rec.append("door", who, where="docs/a.md", reason="cites " + "ab" * 32)
    stack = serve.piles(rec.rows(), "where")
    card = serve.propose(stack)
    seal(rec, card["subject"])
    doc = capped(rec, stack, card["ids"], SERVE_KEY)
    assert doc["tables"][0]["rows"][0]["reason"] == "cites " + serve.WITHHELD


def test_unreachable_says_how_many_never_which(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    doc = capped(rec, [IN], ids, SERVE_KEY)
    assert doc["why"] == "1 sealed table(s) not found"
    assert not HEX64.search((tmp_path / serve.OUT).read_text())


def test_every_served_file_says_what_to_return(tmp_path):
    _, _, _, doc = served_where(tmp_path)
    assert doc["return"] == serve.RETURN
    assert capped(box(tmp_path / "b"), [], None, SERVE_KEY)["return"] == (serve.RETURN)


def test_a_where_pile_says_what_it_cannot_hold(tmp_path):
    _, _, _, doc = served_where(tmp_path)
    assert doc["tables"][0]["cannot_hold"] == [serve.CANNOT_HOLD["where"]]


def test_the_card_lists_the_joins_the_model_could_make(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    stack = serve.piles(rec.rows(), "where")
    card = serve.propose(stack)  # both files: hanuman and willow, one day
    assert card["joins"]["who"] == {"hanuman": ["where=docs/a.md", "where=docs/b.md"]}
    assert list(card["joins"]["when"].values()) == [
        ["where=docs/a.md", "where=docs/b.md"]
    ]
    one = serve.propose(stack, where="docs/b.md")
    assert one["joins"] == {}  # one pile joins nothing


# ── the serve key: one per session, never the seal key ───────────────────────
def test_a_new_session_gives_the_same_table_a_different_id(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    a = capped(rec, [IN], ids, serve.session_key())["tables"][0]["id"]
    b = capped(rec, [IN], ids, serve.session_key())["tables"][0]["id"]
    assert a != b


# ── the cap is the caller's, never truncated ─────────────────────────────────
def test_serve_takes_its_cap_from_the_caller_and_never_truncates(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    size = len(serve.canon(capped(rec, [IN], ids, SERVE_KEY)))
    assert capped(rec, [IN], ids, SERVE_KEY, max_chars=size)["state"] == "populated"
    doc = capped(rec, [IN], ids, SERVE_KEY, max_chars=size - 1)
    assert doc["state"] == "empty" and "narrow the stack" in doc["why"]
    assert doc["tables"] == []
    assert "hanuman" not in (tmp_path / serve.OUT).read_text()  # whole or nothing


def test_no_cap_serves_nothing_never_an_uncapped_document(tmp_path):
    """Loki 8E0652F5 F9: the caller sizes the cap. Without one the answer is
    `empty` with the reason, and no table reaches the file."""
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
    assert doc["state"] == "empty" and doc["tables"] == []
    assert "no max_chars" in doc["why"]
    assert "hanuman" not in (tmp_path / serve.OUT).read_text()
    big = {"rows": [{"who": "x" * 50_000, "what": "write"}], "source": "r@n=1"}
    rec2 = box(tmp_path / "b")
    ids2 = scoped(rec2, big)
    assert (
        capped(rec2, [big], ids2, SERVE_KEY)["state"] == "populated"
    )  # a caller's cap


def test_the_return_line_is_the_proposal_contract():
    """Loki 8E0652F5 F8: what the model is told to return is what the proposal
    reader accepts: path, data, cites, claim."""
    from onescript import proposals

    for key in sorted(proposals.KEYS):
        assert f'"{key}"' in serve.RETURN


def test_the_return_line_names_the_propose_tool_and_the_json_line_both():
    """One line the two readers can both follow: a model with the propose tool
    gives its rows through it, one without writes one JSON object per line."""
    assert "propose tool" in serve.RETURN
    assert "one JSON object per line" in serve.RETURN
    assert "A cite must be an id in this file" in serve.RETURN
    assert '"cites": []' in serve.RETURN
    assert "\n" not in serve.RETURN and len(serve.RETURN) < 400


# ── a turn-bearing table is served as one fact per turn ──────────────────────
def _drow(i, kind, turn, **kw):
    row = {
        "who": "desk",
        "kind": kind,
        "ts": f"2026-10-08T00:00:{i:02d}Z",
        "where": "desk/turns",
        "family": "claude",
        "standing": "unattested",
        "turn": turn,
    }
    return {**row, **kw}


def _desk():
    """The 11 rows the 3B model was served raw (Kart QRGMNFKD)."""
    rows = [
        _drow(1, "turn_open", 1, intent="Live one-script drive: who-pile join"),
        _drow(2, "turn_close", 1),
        _drow(3, "turn_open", 2, intent="escalate piece a"),
        _drow(
            4,
            "proposal",
            2,
            verdict="link_fail",
            reason="a cite is not in the served file",
        ),
        _drow(5, "turn_close", 2, reason=None),  # a null never overrides a real one
        _drow(6, "turn_open", 3, intent="escalate piece b"),
        _drow(7, "proposal", 3, verdict="pass"),
        _drow(8, "turn_close", 3),
        _drow(9, "turn_open", 4, intent="escalate piece c"),
        _drow(10, "proposal", 4, verdict="pass"),
        _drow(11, "turn_close", 4),
    ]
    return {"rows": rows, "source": "record.jsonl@n=11"}


def _serve_one(tmp_path, table):
    rec = box(tmp_path)
    ids = scoped(rec, table)
    return capped(rec, [table], ids, SERVE_KEY)["tables"][0]


def _entries(fact, key):
    return [e[key] for e in fact.get("rows", []) if key in e]


def test_a_desk_table_is_served_as_one_fact_per_turn(tmp_path):
    t = _serve_one(tmp_path, _desk())
    facts = t["rows"]
    assert [f["turn"] for f in facts] == [1, 2, 3, 4]
    assert [_entries(f, "what") for f in facts] == [
        ["turn_open", "turn_close"],
        ["turn_open", "proposal", "turn_close"],
        ["turn_open", "proposal", "turn_close"],
        ["turn_open", "proposal", "turn_close"],
    ]
    assert [_entries(f, "intent") for f in facts] == [
        ["Live one-script drive: who-pile join"],
        ["escalate piece a"],
        ["escalate piece b"],
        ["escalate piece c"],
    ]
    assert [_entries(f, "verdict") for f in facts] == [
        [],
        ["link_fail"],
        ["pass"],
        ["pass"],
    ]
    # the reason stays with the proposal row; the close row's null stays its own
    assert facts[1]["rows"][1]["reason"] == "a cite is not in the served file"
    assert facts[1]["rows"][2] == {
        "what": "turn_close",
        "when": "2026-10-08T00:00:05Z",
        "reason": None,
    }
    # what every fact shares is said once, and is gone from the facts
    assert t["same"] == {
        "who": "desk",
        "where": "desk/turns",
        "family": "claude",
        "standing": "unattested",
    }
    assert not set(t["same"]) & {k for f in facts for k in f}


def _unjoined(table):
    return [serve.view(r) for r in serve.authored(table["rows"])]


def _rebuilt(t):
    """Each source row back from the served table: the table's `same`, the
    fact's own keys, and the row's entry. A fact with no `rows` is one row."""
    out = []
    for f in t["rows"]:
        base = {k: v for k, v in f.items() if k != "rows"}
        for e in f.get("rows", [{}]):
            out.append({**t.get("same", {}), **base, **e})
    return out


def _canon(rows):
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def test_a_joined_table_holds_each_row_whole_served_equals_sealed(tmp_path):
    table = _desk()
    t = _serve_one(tmp_path, table)
    # every row, one by one, nothing de-duplicated, nulls included
    assert _canon(_rebuilt(t)) == _canon(_unjoined(table))
    assert len(_rebuilt(t)) == 11


def test_three_proposals_in_one_turn_keep_each_triple_intact(tmp_path):
    """Loki MLY70YPV: values must stay with their row, repeats included."""
    rows = [
        _drow(1, "turn_open", 1, where="desk/turns"),
        _drow(2, "proposal", 1, where="a.py", verdict="pass", reason="ok"),
        _drow(3, "proposal", 1, where="b.py", verdict="refused", reason="outside law"),
        _drow(4, "proposal", 1, where="c.py", verdict="pass", reason="ok"),
        _drow(5, "turn_close", 1, where="desk/turns"),
    ]
    table = {"rows": rows, "source": "record.jsonl@n=5"}
    t = _serve_one(tmp_path, table)
    (fact,) = t["rows"]
    got = [
        (e["where"], e["verdict"], e["reason"]) for e in fact["rows"] if "verdict" in e
    ]
    assert got == [
        ("a.py", "pass", "ok"),
        ("b.py", "refused", "outside law"),
        ("c.py", "pass", "ok"),
    ]
    assert [e["what"] for e in fact["rows"]] == [
        "turn_open",
        "proposal",
        "proposal",
        "proposal",
        "turn_close",
    ]
    assert _canon(_rebuilt(t)) == _canon(_unjoined(table))


def test_a_null_in_one_row_is_not_lifted_over_the_real_values(tmp_path):
    rows = [
        _drow(1, "proposal", 1, verdict="pass", reason="ok"),
        _drow(2, "proposal", 1, verdict="pass", reason=None),
        _drow(3, "proposal", 2, verdict="pass", reason="ok"),
    ]
    table = {"rows": rows, "source": "record.jsonl@n=3"}
    t = _serve_one(tmp_path, table)
    assert [e["reason"] for e in t["rows"][0]["rows"]] == ["ok", None]
    assert _canon(_rebuilt(t)) == _canon(_unjoined(table))


def test_two_identical_rows_in_a_turn_are_both_kept(tmp_path):
    rows = [
        _drow(1, "proposal", 1, verdict="pass", reason="ok"),
        _drow(1, "proposal", 1, verdict="pass", reason="ok"),
        _drow(2, "turn_close", 1),
    ]
    table = {"rows": rows, "source": "record.jsonl@n=3"}
    t = _serve_one(tmp_path, table)
    (fact,) = t["rows"]
    assert [e["what"] for e in fact["rows"]] == ["proposal", "proposal", "turn_close"]
    assert _canon(_rebuilt(t)) == _canon(_unjoined(table))
    assert len(_rebuilt(t)) == 3


def test_a_group_with_two_sessions_is_not_joined(tmp_path):
    """A writer numbering turns per session would merge two sessions' turn 1."""

    def rows(s2):
        return [
            _drow(1, "turn_open", 1, session="s1"),
            _drow(2, "turn_close", 1, session="s1"),
            _drow(3, "turn_open", 1, session=s2),
        ]

    two = {"rows": rows("s2"), "source": "r@n=3"}
    t = _serve_one(tmp_path, two)
    assert t["rows"] == _unjoined(two) and "same" not in t
    one = {"rows": rows("s1"), "source": "r@n=3"}
    joined = _serve_one(tmp_path / "b", one)
    assert [f["turn"] for f in joined["rows"]] == [1] and "rows" in joined["rows"][0]


def test_a_table_without_turn_on_every_row_is_served_as_it_was(tmp_path):
    rows = [
        _drow(1, "turn_open", 1, intent="x"),
        {"who": "desk", "kind": "note", "ts": "2026-10-08T00:00:02Z", "where": "w"},
    ]
    table = {"rows": rows, "source": "record.jsonl@n=2"}
    t = _serve_one(tmp_path, table)
    assert t["rows"] == [serve.view(r) for r in rows]
    assert "same" not in t
    plain = _serve_one(tmp_path / "b", IN)
    assert plain["rows"] == [serve.view(IN["rows"][0])] and "same" not in plain
    # a bool is not a turn
    flag = {"rows": [_drow(1, "turn_open", True)], "source": "r@n=1"}
    assert "same" not in _serve_one(tmp_path / "c", flag)


def test_a_table_of_roll_rows_is_served_row_by_row_for_d0(tmp_path):
    """D0's `table.*` acts read each roll row's own turn, session and total."""
    roll = lambda i, s: {  # noqa: E731
        "who": "table",
        "kind": "verdict",
        "ts": f"2026-10-08T00:00:{i:02d}Z",
        "where": "t",
        "turn": 1,
        "session": s,
        "total": 12,
    }
    table = {"rows": [roll(1, "s1"), roll(2, "s1")], "source": "r@n=2"}
    t = _serve_one(tmp_path, table)
    assert t["rows"] == [serve.view(r) for r in table["rows"]] and "same" not in t


@pytest.mark.parametrize("bad", [0, -1, 1.5, "90", True])
def test_a_cap_that_is_not_a_positive_whole_number_serves_nothing(tmp_path, bad):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN], ids, SERVE_KEY, max_chars=bad)
    assert doc["state"] == "empty" and "positive whole number" in doc["why"]


def test_a_cite_the_model_cannot_have_known_never_crashes_the_check(tmp_path):
    rec = box(tmp_path)
    doc = capped(rec, [IN], scoped(rec, IN), SERVE_KEY)
    fails = serve.check_cites(doc, ["é✓", "\ud800", ""])
    assert len(fails) == 3 and {f["verdict"] for f in fails} == {"link_fail"}


def test_no_serve_key_serves_nothing(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = capped(rec, [IN], ids, None)
    assert doc["state"] == "empty" and "check-in" in doc["why"]


# ── the where gap: every row says where, or says it can't ────────────────────
def test_every_record_row_carries_where(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    seal(rec, "subject-x")
    rows = rec.rows()
    assert all("where" in r for r in rows)
    assert [r["where"] for r in rows if r["kind"] == "write"] == [
        "docs/a.md",
        "docs/a.md",
        "docs/b.md",
    ]
    assert [r["where"] for r in rows if r["kind"] == "door"] == [None]
    assert rec.verify_chain() == []


# ── the subject holds still: the run's own rows are in no scope ──────────────
def launch(rec):
    """What every launch appends: the run's bookkeeping, shaped to land in the
    same who/what/when/where groups the authored rows do."""
    run = gate.system()
    rec.append("invocation", run, where="docs/a.md")
    rec.append("door", run, verdict="pass", where="docs/a.md")
    rec.write_file(run, "docs/a.md", b"boot", provenance="authored")


# A scope seal is in no pile (`serve.scope_seal`), so sealing never moves the
# subject it seals; the "who", "what=seal" and day piles are covered by the
# scope-seal tests below. Each case names the authored pile(s) a scope would.
STABLE = [
    (("who",), {"who": "hanuman"}),
    (("what",), {"what": "door"}),
    (("where",), {"where": "docs/a.md"}),
    (("who", "where"), {"where": "docs/a.md"}),
]


@pytest.mark.parametrize("by,match", STABLE)
def test_a_sealed_subject_survives_launches_that_add_only_run_rows(tmp_path, by, match):
    rec = box(tmp_path)
    activity(rec)
    launch(rec)  # the first launch: the run's piles exist before anything is sealed
    stack = serve.piles(rec.rows(), *by)
    card = serve.propose(stack, **match)
    assert card["subject"] and card["ids"]
    seal(rec, card["subject"])
    assert card["subject"] in serve.claimed_seals(rec.rows())

    launch(rec)
    launch(rec)
    again_stack = serve.piles(rec.rows(), *by)
    again = serve.propose(again_stack, **match)
    assert again["subject"] == card["subject"] and again["ids"] == card["ids"]
    assert again["subject"] in serve.claimed_seals(rec.rows())
    doc = capped(rec, again_stack, again["ids"], SERVE_KEY)
    assert doc["state"] == "populated"  # the old seal still opens the new run


def test_a_run_row_in_a_sealed_pile_is_never_served_as_sealed_content(tmp_path):
    """Loki A91DFED4 F1: the seal binds the authored rows, so served == sealed.
    A who==run row in a sealed pile is checked by the receipt but not served."""
    rec = box(tmp_path)
    activity(rec)
    launch(rec)  # run rows now sit in the docs/a.md pile beside the authored ones
    stack = serve.piles(rec.rows(), "where")
    pile = next(p for p in stack if serve.name(p) == "where=docs/a.md")
    assert {r["who"] for r in pile["rows"]} >= {"run", "hanuman", "willow"}
    card = serve.propose(stack, where="docs/a.md")
    seal(rec, card["subject"])
    seal(rec, card["ids"][0])  # the table itself sealed too: label is human-sealed
    doc = capped(rec, stack, card["ids"], SERVE_KEY)
    assert doc["state"] == "populated"
    table = doc["tables"][0]
    assert table["trust"] == "human-sealed"
    served_who = [r["who"] for r in table["rows"]]
    assert "run" not in served_who  # no run row rides under the sealed label
    assert served_who == [r["who"] for r in serve.authored(pile["rows"])]
    assert {"hanuman", "willow"} <= set(served_who)  # authored rows still served
    assert "invocation" not in (tmp_path / serve.OUT).read_text()


@pytest.mark.parametrize("w", serve.WS)
def test_run_rows_alone_never_move_the_subject_under_any_w(tmp_path, w):
    rec = box(tmp_path)
    activity(rec)
    launch(rec)  # the first launch: the run's piles exist before anything is sealed
    before = serve.propose(serve.piles(rec.rows(), w))
    launch(rec)
    after = serve.propose(serve.piles(rec.rows(), w))
    assert before["subject"] and after["subject"] == before["subject"]
    assert after["ids"] == before["ids"]


def test_an_authored_row_still_moves_the_subject_and_needs_a_new_seal(tmp_path):
    rec = box(tmp_path)
    activity(rec)
    card = serve.propose(serve.piles(rec.rows(), "who"))
    seal(rec, card["subject"])
    rec.write_file(
        gate.Verified("hanuman", "qwen", gate.token()),
        "docs/c.md",
        b"c",
        provenance="authored",
    )
    moved = serve.propose(serve.piles(rec.rows(), "who"))
    assert moved["subject"] != card["subject"]
    assert moved["subject"] not in serve.claimed_seals(rec.rows())
    doc = capped(rec, serve.piles(rec.rows(), "who"), moved["ids"], SERVE_KEY)
    assert doc["state"] == "empty" and "no human seal" in doc["why"]


# ── a scope seal never moves the scope it seals ───────────────────────────────
def human_act(rec, subject="tip:7:" + "ab" * 32):
    """A seal the human makes that is not a scope seal (a tip or wager seal):
    the human's own act, so it is content in the human's pile."""
    seal(rec, subject)


@pytest.mark.parametrize(
    "by,match",
    [
        (("who",), {}),  # every who pile, the human's among them
        (("who",), {"who": "human"}),  # the human's pile alone
        (("what",), {"what": "seal"}),  # the seal kind's pile
        (("when",), {}),  # every row, one pile per timestamp
    ],
)
def test_sealing_a_scope_does_not_move_its_subject(tmp_path, by, match):
    """gap 54b88478b658: a scope seal is a who=="human" row. Were it in the
    human's pile, sealing would change that pile's id and so the subject, and
    the seal would never match again (nor would the next one)."""
    rec = box(tmp_path)
    activity(rec)
    human_act(rec)
    launch(rec)
    card = serve.propose(serve.piles(rec.rows(), *by), **match)
    assert card["subject"] and card["ids"]
    seal(rec, card["subject"])
    again = serve.propose(serve.piles(rec.rows(), *by), **match)
    assert again["subject"] == card["subject"] and again["ids"] == card["ids"]
    assert again["subject"] in serve.claimed_seals(rec.rows())
    doc = capped(rec, serve.piles(rec.rows(), *by), again["ids"], SERVE_KEY)
    assert doc["state"] == "populated"
    assert "serve:" not in json.dumps(doc["tables"])  # no scope seal is served


def test_a_first_scope_seal_adds_no_pile(tmp_path):
    """With no human row before it, a scope seal in a pile would add a new
    who=human pile, and a scope over every pile would move by gaining it."""
    rec = box(tmp_path)
    activity(rec)
    card = serve.propose(serve.piles(rec.rows(), "who"))
    seal(rec, card["subject"])
    stack = serve.piles(rec.rows(), "who")
    assert "who=human" not in {serve.name(p) for p in stack}
    assert serve.propose(stack)["subject"] == card["subject"]


def test_a_tip_or_wager_seal_is_still_content_and_moves_the_subject(tmp_path):
    """Only scope seals are left out: the human's other seals are what the
    human's pile holds, and a new one is a new authored row."""
    rec = box(tmp_path)
    activity(rec)
    human_act(rec)
    card = serve.propose(serve.piles(rec.rows(), "who"), who="human")
    human_act(rec, "tip:9:" + "cd" * 32)
    moved = serve.propose(serve.piles(rec.rows(), "who"), who="human")
    assert moved["subject"] != card["subject"]


def test_mutant_seal_rows_back_in_the_pile_drift_the_subject(tmp_path, monkeypatch):
    """The mutation proof: put scope seals back in the piles and the ids, as
    before the fix, and sealing the scope moves its subject."""
    monkeypatch.setattr(serve, "scope_seal", lambda row: False)
    rec = box(tmp_path)
    activity(rec)
    human_act(rec)
    card = serve.propose(serve.piles(rec.rows(), "who"), who="human")
    seal(rec, card["subject"])
    again = serve.propose(serve.piles(rec.rows(), "who"), who="human")
    assert again["subject"] != card["subject"]  # the drift the fix removes
    assert again["subject"] not in serve.claimed_seals(rec.rows())
