"""J5 (R11): sealed `world/*.md` proposals reach the model as `lore` rows.

SPEC.md R11; SPEC-rival.md section 5 T14; SPEC-documented.md phase J5. A lore
fact is served only when the proposal carrying it is sealed (proof AND hash).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import gate, lore, proposals, serve  # noqa: E402
from onescript.record import h256  # noqa: E402
from onescript.run import Run  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
KEYS = {"willow": b"k-willow", "hanuman": b"k-hanuman", gate.HUMAN: HUMAN_KEY}
LAW = {"trace_ids": ["CONST-III", "CONST-VI"], "grants": []}
CAP = 10**6
SERVE_KEY = b"serve-key"
WORLD = (
    "---\nname: vale\n---\n# The Vale\n- The ferryman takes only salt.\n"
    "- A palantir is not kept in the Vale.\n## The Mill\nThe miller counts nine stones.\n"
)


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-17T00:00:{next(n):06d}Z"


def ident(who="willow", family="qwen"):
    return {"who": who, "family": family, "sig": gate.sign(KEYS[who], who, family)}


@pytest.fixture
def run(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    r.serve_key = SERVE_KEY
    return r


def propose(run, path="world/vale.md", data=WORLD, claim="the vale"):
    """A passing proposal row, as `Run._take` records it."""
    p = {"path": path, "data": data, "cites": ["x"], "claim": claim}
    subject = proposals.subject(p)
    who = gate.verify(ident(), KEYS)
    row = run.rec.append(
        "proposal",
        who,
        where=path,
        turn=1,
        line=1,
        sha=h256(data),
        verdict="pass",
        reason="",
        subject=subject,
        **p,
    )
    return subject, row


def seal(run, subject):
    hum = gate.human(subject, proof(subject), HUMAN_KEY)
    run.rec.append("seal", hum, where=subject, subject=subject, proof=proof(subject))


def serve_lore(run, max_chars=CAP):
    """Scope the lore pile, seal the scope, serve it. The served document."""
    stack = serve.piles(run.rec.rows(), "what")
    chosen = serve.choose(stack, what="lore")
    ids = [serve.table_id(p) for p in chosen]
    seal(run, serve.scope_subject(ids))
    return run.serve(chosen, ids, max_chars=max_chars)


def served_rows(doc):
    return [r for t in doc["tables"] for r in t["rows"]]


def test_facts_are_one_per_line_under_their_scene():
    got = lore.facts_of("world/vale.md", WORLD)
    assert got == [
        ("The Vale", "The ferryman takes only salt."),
        ("The Vale", "A palantir is not kept in the Vale."),
        ("The Mill", "The miller counts nine stones."),
    ]
    assert lore.facts_of("world/x.md", "plain line\n") == [("x", "plain line")]


def test_sealed_world_proposal_serves_its_lore(run):
    subject, _ = propose(run)
    seal(run, subject)
    new = lore.from_sealed(run, subject)
    assert [r["kind"] for r in new] == ["lore"] * 3
    assert {r["where"] for r in new} == {"The Vale", "The Mill"}
    assert lore.from_sealed(run, subject) == []  # idempotent
    doc = serve_lore(run)
    assert doc["state"] == "populated"
    rows = served_rows(doc)
    assert [r["fact"] for r in rows] == [
        f for _, f in lore.facts_of("world/vale.md", WORLD)
    ]
    assert rows[0]["where"] == "The Vale" and rows[0]["what"] == "lore"
    assert "proposal" not in rows[0]  # the subject hash stays on the record's side


def test_unsealed_world_proposal_serves_no_lore(run):
    subject, _ = propose(run)
    (refused,) = lore.from_sealed(run, subject)
    assert refused["kind"] == "refused" and "no human seal" in refused["reason"]
    # a lore row forged beside it, as anyone who can write the record could
    run.rec.append(
        "lore",
        gate.verify(ident(), KEYS),
        where="The Vale",
        fact="The ferryman takes only salt.",
        proposal=subject,
    )
    doc = serve_lore(run)
    assert served_rows(doc) == []
    assert "ferryman" not in (run.rec.box / serve.OUT).read_text()


def test_a_seal_row_without_proof_is_no_seal(run):
    subject, _ = propose(run)
    hum = gate.human(subject, proof(subject), HUMAN_KEY)
    run.rec.append("seal", hum, where=subject, subject=subject)  # no proof: a claim
    assert lore.from_sealed(run, subject)[0]["kind"] == "refused"


def test_forged_proposal_seal_serves_no_lore(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    other = proposals.subject(
        {"path": "world/vale.md", "data": "x", "cites": ["x"], "claim": "c"}
    )
    # lore rows now point at a proposal whose seal is for different bytes
    rows = [r for r in run.rec.rows() if r["kind"] == "lore"]
    assert rows
    held = {other}
    assert not any(lore.servable(r, run.rec.rows(), held) for r in rows)


def test_edited_data_after_seal_serves_no_lore(run):
    subject, row = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    # the stored proposal is replaced by one with other data under the same
    # subject (the record is append-only, so the forger appends)
    forged = {k: row[k] for k in proposals.KEYS}
    forged["data"] = "# The Vale\n- The ferryman takes gold.\n"
    run.rec.append(
        "proposal",
        gate.verify(ident(), KEYS),
        where="world/vale.md",
        verdict="pass",
        subject=subject,
        **forged,
    )
    # the last stored row no longer hashes to the sealed subject; the earlier
    # honest one is still found, so a fact of the FORGED text is what must fail
    run.rec.append(
        "lore",
        gate.verify(ident(), KEYS),
        where="The Vale",
        fact="The ferryman takes gold.",
        proposal=subject,
    )
    doc = serve_lore(run)
    facts = [r["fact"] for r in served_rows(doc)]
    assert "The ferryman takes gold." not in facts
    assert "The ferryman takes only salt." in facts


def test_a_tampered_only_proposal_serves_nothing(run):
    subject, row = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    path = run.rec.path
    lines = path.read_text().splitlines()
    rec = [json.loads(x) for x in lines]
    i = next(k for k, r in enumerate(rec) if r["kind"] == "proposal")
    rec[i]["data"] = rec[i]["data"].replace("salt", "gold")  # edit in place
    path.write_text("\n".join(json.dumps(r) for r in rec) + "\n")
    doc = serve_lore(run)
    assert "salt" not in json.dumps(doc) and "gold" not in json.dumps(doc)
    assert served_rows(doc) == []


def test_run_written_world_text_is_not_served(run):
    run.rec.write_file(run.sys, "world/vale.md", WORLD.encode(), provenance="authored")
    run.rec.append(  # even a lore row stamped with the run's own name
        "lore",
        run.sys,
        where="The Vale",
        fact="The ferryman takes only salt.",
        proposal="proposal:" + "0" * 64,
    )
    stack = serve.piles(run.rec.rows(), "what")
    ids = [serve.table_id(p) for p in stack]
    seal(run, serve.scope_subject(ids))
    doc = run.serve(stack, ids, max_chars=CAP)
    raw = json.dumps(doc) + (run.rec.box / serve.OUT).read_text()
    assert "ferryman" not in raw and "miller" not in raw


def test_a_world_file_written_as_a_file_names_no_fact(run):
    """T14: the file is a `write` row; its facts are not served, so a sentence
    naming them has nothing to rest on."""
    run.rec.write_file(run.sys, "world/vale.md", WORLD.encode(), provenance="authored")
    assert not [r for r in run.rec.rows() if r["kind"] == "lore"]


def test_not_under_world_is_not_lore(run):
    subject, _ = propose(run, path="notes/vale.md")
    seal(run, subject)
    (refused,) = lore.from_sealed(run, subject)
    assert refused["kind"] == "refused" and "world" in refused["reason"]


def test_over_budget_is_empty_never_truncated(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    doc = serve_lore(run, max_chars=200)
    assert doc["state"] == "empty" and doc["tables"] == []
    assert "narrow the stack" in doc["why"]
    raw = (run.rec.box / serve.OUT).read_text()
    assert "ferryman" not in raw  # not even the first fact


def test_a_hash_inside_lore_is_withheld(run):
    h = "ab" * 32
    subject, _ = propose(run, data=f"# Vale\n- The stone is marked {h}.\n")
    seal(run, subject)
    lore.from_sealed(run, subject)
    (row,) = served_rows(serve_lore(run))
    assert h not in row["fact"] and serve.WITHHELD in row["fact"]


def test_a_fact_the_model_cites_that_was_not_served_is_struck(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    doc = serve_lore(run)
    good = doc["tables"][0]["id"]
    assert serve.check_cites(doc, [good]) == []
    struck = serve.check_cites(doc, ["lore-the-palantir", good])
    assert [s["cite"] for s in struck] == ["lore-the-palantir"]
    assert struck[0]["verdict"] == "link_fail"


def test_unserved_training_is_struck_by_the_vocabulary_check(run):
    """`seat_say.check_nothing_lacking` over the served lore rows, not
    `gate.check_claims`: a sentence naming a thing no served fact holds fails."""
    sys.path.insert(0, str(cli.GROVE))
    try:
        from grove import seat_boxes, seat_say
    except Exception:  # pragma: no cover - the grove is checked out beside
        pytest.skip("willows-grove seat_say not importable here")
    finally:
        sys.path.remove(str(cli.GROVE))
    subject, _ = propose(run, data="# The Vale\n- The ferryman takes only salt.\n")
    seal(run, subject)
    lore.from_sealed(run, subject)
    doc = serve_lore(run)
    boxes = [
        seat_boxes.Box(id="b1", kind="note", label=r["where"], value=r["fact"])
        for r in served_rows(doc)
    ]
    assert seat_say.check_nothing_lacking("The ferryman takes only salt [b1]", boxes)
    assert not seat_say.check_nothing_lacking(
        "The ferryman keeps a palantir [b1]", boxes
    )


# ── revision round (Opus audit of 48b3408) ──────────────────────────────────
def test_B1_fact_on_a_non_lore_row_is_not_payload(run):
    """P2: any row may carry a `fact` field; only a servable lore row serves it."""
    run.rec.append(
        "note", gate.verify(ident(), KEYS), where="x", fact="UNSEALED-FACT seed=424242"
    )
    stack = serve.piles(run.rec.rows(), "what")
    chosen = serve.choose(stack, what="note")
    ids = [serve.table_id(p) for p in chosen]
    seal(run, serve.scope_subject(ids))
    doc = run.serve(chosen, ids, max_chars=CAP)
    assert doc["state"] == "populated" and served_rows(doc)  # the row is served
    assert all("fact" not in r for r in served_rows(doc))
    assert (
        "UNSEALED-FACT" not in json.dumps(doc) + (run.rec.box / serve.OUT).read_text()
    )


def test_B2_a_wrapped_bullet_is_one_fact_and_the_meaning_holds():
    data = "# Vale\n- The ferryman never\n  takes salt from strangers.\n\nThe miller is not\nhonest.\n"
    got = lore.facts_of("world/v.md", data)
    assert got == [
        ("Vale", "The ferryman never takes salt from strangers."),
        ("Vale", "The miller is not honest."),
    ]
    assert not any(f in ("The ferryman never", "honest.") for _, f in got)


def test_B2_unicode_line_separators_never_split_a_fact():
    got = lore.facts_of("world/v.md", "# V\n- A B\x0cC \u2028D\n")
    assert got == [("V", "A B\x0cC \u2028D")]  # record.py:95 splits on "\n" only


def test_N5_comments_tables_and_setext_titles_are_not_facts():
    data = (
        "Title\n=====\n<!-- private\nnote -->\n| a | b |\n|---|---|\n"
        "    code line\n\nA real fact.\n- item one\n---\n"
    )
    assert lore.facts_of("world/v.md", data) == [
        ("Title", "A real fact."),
        ("Title", "item one"),
    ]


def test_S1_an_unreachable_proposal_seal_is_said_never_dropped(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    stack = serve.piles(run.rec.rows(), "what")
    chosen = serve.choose(stack, what="lore")
    ids = [serve.table_id(p) for p in chosen]
    rows = run.rec.rows()
    seals = ({serve.scope_subject(ids)}, {subject: "unreachable: no Nestor store"})
    doc = serve._serve(rows, chosen, ids, SERVE_KEY, CAP, seals)
    assert doc["state"] == "unreachable" and "can't be checked" in doc["why"]
    (t,) = doc["tables"]
    assert t["state"] == "unreachable" and "Nestor" in t["why"] and t["rows"] == []


def test_S2_a_lore_row_must_be_by_the_proposals_author(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    run.rec.append(
        "lore",
        gate.verify(ident("hanuman"), KEYS),
        where="The Vale",
        fact="The ferryman takes only salt.",
        proposal=subject,
    )
    who = [r["who"] for r in served_rows(serve_lore(run))]
    assert who and set(who) == {"willow"}
    assert len(who) == 3  # the three real facts; hanuman's copy is not served


def test_S3_an_own_idea_world_proposal_never_yields_lore(run):
    p = {
        "path": "world/v.md",
        "data": "- Own idea fact.\n",
        "cites": [],
        "claim": "mine",
    }
    subject = proposals.subject(p)
    run.rec.append(
        "proposal",
        gate.verify(ident(), KEYS),
        where=p["path"],
        turn=1,
        line=1,
        sha=h256(p["data"]),
        verdict="pass",
        reason="",
        subject=subject,
        own_idea=True,
        **p,
    )
    seal(run, subject)
    (refused,) = lore.from_sealed(run, subject)
    assert refused["kind"] == "refused" and "own-idea" in refused["reason"]
    run.rec.append(  # and a forged row for it is not served either
        "lore",
        gate.verify(ident(), KEYS),
        where="v",
        fact="Own idea fact.",
        proposal=subject,
    )
    assert served_rows(serve_lore(run)) == []


@pytest.mark.parametrize("h", ["AB" * 32, "ab" * 31 + "aB", "ａｂ" * 32])
def test_S4_hashes_are_withheld_in_upper_case_and_fullwidth(run, h):
    subject, _ = propose(run, data=f"# V\n- mark {h}.\n")
    seal(run, subject)
    lore.from_sealed(run, subject)
    (row,) = served_rows(serve_lore(run))
    assert serve.WITHHELD in row["fact"]
    assert h.lower() not in row["fact"].lower() and "ab" * 8 not in row["fact"].lower()


def test_S5_data_edited_after_sealing_serves_nothing_and_says_why(run):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    stack = serve.piles(run.rec.rows(), "what")
    chosen = serve.choose(stack, what="lore")
    ids = [serve.table_id(p) for p in chosen]
    seal(run, serve.scope_subject(ids))
    path = run.rec.path
    rec = [json.loads(x) for x in path.read_text().splitlines()]
    i = next(k for k, r in enumerate(rec) if r["kind"] == "proposal")
    rec[i]["data"] = rec[i]["data"].replace("salt", "gold")
    path.write_text("\n".join(json.dumps(r) for r in rec) + "\n")
    doc = run.serve(chosen, ids, max_chars=CAP)
    assert doc["state"] == "unreachable" and doc["tables"] == []
    assert "chain is broken" in doc["why"]
    assert "gold" not in (run.rec.box / serve.OUT).read_text()


def test_N5_the_latest_verdict_for_a_subject_decides(run):
    subject, row = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    run.rec.append(  # a later row for the same bytes that no longer passes
        "proposal",
        gate.verify(ident(), KEYS),
        where="world/vale.md",
        verdict="link_fail",
        subject=subject,
        **{k: row[k] for k in proposals.KEYS},
    )
    assert served_rows(serve_lore(run)) == []
    assert lore.from_sealed(run, subject)[0]["kind"] == "refused"


def test_N6_facts_are_worked_out_once_per_serve(run, monkeypatch):
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    calls = []
    real = lore.facts_of
    monkeypatch.setattr(lore, "facts_of", lambda p, d: calls.append(p) or real(p, d))
    assert len(served_rows(serve_lore(run))) == 3
    assert len(calls) == 1


def _d1(run, own=False):
    """An honest sealed lore pile plus a forged lore row over proposal Y."""
    subject, _ = propose(run)
    seal(run, subject)
    lore.from_sealed(run, subject)
    if own:
        p = {"path": "world/y.md", "data": "- Y fact.\n", "cites": [], "claim": "y"}
        y = proposals.subject(p)
        run.rec.append(
            "proposal",
            gate.verify(ident(), KEYS),
            where=p["path"],
            verdict="pass",
            subject=y,
            own_idea=True,
            **p,
        )
    else:
        y, _ = propose(run, data="# Y\n- Y fact.\n", claim="y")
    run.rec.append(
        "lore", gate.verify(ident(), KEYS), where="Y", fact="Y fact.", proposal=y
    )
    stack = serve.piles(run.rec.rows(), "what")
    chosen = serve.choose(stack, what="lore")
    ids = [serve.table_id(p) for p in chosen]
    seals = (
        {serve.scope_subject(ids), subject},
        {y: "unreachable: forged nestor seal"},
    )
    return serve._serve(run.rec.rows(), chosen, ids, SERVE_KEY, CAP, seals)


def test_NEW3_one_unreachable_seal_does_not_blank_the_honest_rows(run):
    doc = _d1(run)
    assert doc["state"] == "populated"  # the rest is still served
    (t,) = doc["tables"]
    assert t["state"] == "unreachable" and "forged nestor seal" in t["why"]
    assert [r["fact"] for r in t["rows"]][0] == "The ferryman takes only salt."
    assert len(t["rows"]) == 3 and "Y fact." not in json.dumps(doc)


def test_NEW3_a_proposal_that_could_never_be_lore_is_no_lever(run):
    doc = _d1(run, own=True)
    (t,) = doc["tables"]
    assert "state" not in t and len(t["rows"]) == 3


def test_NEW4_nested_items_stay_with_their_parent():
    data = "# V\n- Parent\n  - child one\n    - grand\n\n  - child two\n- Next\n"
    assert lore.facts_of("world/v.md", data) == [
        ("V", "Parent; child one; grand; child two"),
        ("V", "Next"),
    ]


def test_NEW6_markup_is_stripped_from_facts_and_zero_width_hides_no_hash(run):
    data = "# V\n> - quoted <b>bold</b> <!-- x --> end\n"
    assert lore.facts_of("world/v.md", data) == [("V", "quoted bold end")]
    h = "ab" * 16 + "\u200b" + "ab" * 16
    ful = "ＡＢ" * 32
    subject, _ = propose(run, data=f"# {ful}\n- mark {h}.\n")
    seal(run, subject)
    lore.from_sealed(run, subject)
    (row,) = served_rows(serve_lore(run))
    assert serve.WITHHELD in row["fact"] and serve.WITHHELD in row["where"]


def test_NEW8_a_fence_closes_only_on_its_own_marker():
    data = "# V\n```\n~~~\n- inside\n```\n- after\n~~~~\n```\n- still\n~~~~\n- out\n"
    assert [f for _, f in lore.facts_of("world/v.md", data)] == ["after", "out"]


# ── W4 break 5: the T-mai-1 shape gate at the lore door ─────────────────────
MALFORMED = (
    "@markdownai v1.0\n# The Floor\n- the floor is lava\n"
    "@constraint: the parser drops this\n"
)
WELL_FORMED_MAI = (
    "@markdownai v1.0\n\n# The Floor\n\n- the floor is lava\n\n"
    "@constraint severity=warning\nkeep off the floor\n"
)


def test_a_malformed_mai_world_doc_writes_no_lore(run):
    subject, _ = propose(run, path="world/floor.md", data=MALFORMED)
    seal(run, subject)
    (got,) = lore.from_sealed(run, subject)
    assert got["kind"] == "refused" and "malformed @markdownai" in got["reason"]
    assert not [r for r in run.rec.rows() if r["kind"] == "lore"]


def test_facts_of_never_yields_a_header_or_a_directive_line():
    facts = [f for _, f in lore.facts_of("world/floor.md", WELL_FORMED_MAI)]
    assert facts and not any(f.startswith("@") or "@constraint" in f for f in facts)
    assert "the floor is lava" in facts
    # a directive line ends the item before it, never joins it
    got = lore.facts_of("world/x.md", "- the floor is lava\n@phase p1\nbody\n")
    assert [f for _, f in got] == ["the floor is lava", "body"]


def test_a_well_formed_mai_world_doc_writes_its_lore(run):
    subject, _ = propose(run, path="world/floor.md", data=WELL_FORMED_MAI)
    seal(run, subject)
    got = lore.from_sealed(run, subject)
    assert got and all(r["kind"] == "lore" for r in got)
    assert not any(r["fact"].startswith("@") for r in got)
