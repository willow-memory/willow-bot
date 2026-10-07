"""serve: only the tables in scope, by ids the model can't guess."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, serve  # noqa: E402
from onescript.record import Record  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
SERVE_KEY = b"the-runs-serve-key"

IN = {"rows": [{"who": "hanuman", "what": "write"}], "source": "record.jsonl@n=4"}
ALSO = {"rows": [{"who": "willow", "what": "seal"}], "source": "record.jsonl@n=9"}
OUT = {"rows": [{"who": "operator", "what": "PIN 4471"}], "source": "vault@n=1"}


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-07T00:00:{next(n):06d}Z"


def box(tmp_path):
    return Record(tmp_path, clock(), "v", gate.token())


def seal(rec, subject):
    hum = gate.human(subject, gate.sign(HUMAN_KEY, "seal", subject), HUMAN_KEY)
    rec.append("seal", hum, subject=subject)


def scoped(rec, *tables):
    ids = [serve.table_id(t) for t in tables]
    seal(rec, serve.scope_subject(ids))
    return ids


def test_only_the_scope_is_served_and_the_rest_is_never_named(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    doc = serve.serve(rec, [IN, ALSO, OUT], ids, SERVE_KEY)
    assert doc["state"] == "populated"
    assert len(doc["tables"]) == 2
    raw = (tmp_path / serve.OUT).read_text()
    assert "PIN 4471" not in raw and "vault" not in raw
    assert serve.table_id(OUT) not in raw
    assert set(json.loads(raw)) == {"state", "why", "return", "tables"}  # no count


def test_served_ids_are_keyed_never_the_content_hash(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
    raw = (tmp_path / serve.OUT).read_text()
    assert ids[0] not in raw
    assert doc["tables"][0]["id"] == serve.served_id(SERVE_KEY, ids[0])
    assert doc["tables"][0]["id"] != serve.served_id(b"another-key", ids[0])


def test_no_scope_serves_nothing_and_says_why(tmp_path):
    doc = serve.serve(box(tmp_path), [IN], None, SERVE_KEY)
    assert doc == {
        "state": "empty",
        "why": doc["why"],
        "return": serve.RETURN,
        "tables": [],
    }
    assert "no scope" in doc["why"]


def test_an_unsealed_scope_serves_nothing(tmp_path):
    rec = box(tmp_path)
    doc = serve.serve(rec, [IN], [serve.table_id(IN)], SERVE_KEY)
    assert doc["state"] == "empty" and doc["tables"] == []
    assert "no human seal" in doc["why"]


def test_a_seal_binds_to_the_exact_set_never_a_wider_one(tmp_path):
    rec = box(tmp_path)
    scoped(rec, IN)  # sealed {IN}
    wider = [serve.table_id(IN), serve.table_id(OUT)]
    doc = serve.serve(rec, [IN, OUT], wider, SERVE_KEY)
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
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
    assert doc["state"] == "unreachable" and doc["tables"] == []


def test_trust_comes_from_the_record_not_the_table(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    seal(rec, serve.table_id(ALSO))
    claims = {**IN, "trust": "human-sealed"}  # a table can't label itself
    doc = serve.serve(rec, [claims, ALSO], ids, SERVE_KEY)
    trust = {t["rows"][0]["who"]: t["trust"] for t in doc["tables"]}
    assert trust == {"hanuman": "untrusted", "willow": "human-sealed"}


def test_a_table_without_a_receipt_fails_the_whole_serve(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = serve.serve(rec, [IN, {"rows": [], "source": ""}], ids, SERVE_KEY)
    assert doc["state"] == "empty" and "receipt" in doc["why"]


def test_every_serve_is_recorded_with_where_and_the_file_is_in_the_pile(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
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
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
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
    assert serve.serve(rec, stack, card["ids"], SERVE_KEY)["state"] == "empty"
    seal(rec, card["subject"])
    doc = serve.serve(rec, stack, card["ids"], SERVE_KEY)
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
    forged = {**pile, "rows": [{**pile["rows"][0], "who": "operator"}]}
    ids = [serve.table_id(forged)]
    seal(rec, serve.scope_subject(ids))
    doc = serve.serve(rec, [forged], ids, SERVE_KEY)
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
    return rec, stack, card, serve.serve(rec, stack, card["ids"], SERVE_KEY)


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
    doc = serve.serve(rec, stack, card["ids"], SERVE_KEY)
    assert doc["tables"][0]["rows"][0]["reason"] == "cites " + serve.WITHHELD


def test_unreachable_says_how_many_never_which(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN, ALSO)
    doc = serve.serve(rec, [IN], ids, SERVE_KEY)
    assert doc["why"] == "1 sealed table(s) not found"
    assert not HEX64.search((tmp_path / serve.OUT).read_text())


def test_every_served_file_says_what_to_return(tmp_path):
    _, _, _, doc = served_where(tmp_path)
    assert doc["return"] == serve.RETURN
    assert serve.serve(box(tmp_path / "b"), [], None, SERVE_KEY)["return"] == (
        serve.RETURN
    )


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
    a = serve.serve(rec, [IN], ids, serve.session_key())["tables"][0]["id"]
    b = serve.serve(rec, [IN], ids, serve.session_key())["tables"][0]["id"]
    assert a != b


def test_no_serve_key_serves_nothing(tmp_path):
    rec = box(tmp_path)
    ids = scoped(rec, IN)
    doc = serve.serve(rec, [IN], ids, None)
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
