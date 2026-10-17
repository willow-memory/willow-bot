"""T-mai-2 (R15): a rules lookup by exact `@phase` name, a D0 `verbatim` answer.

SPEC-tools.md phase T-mai-2; SPEC.md rulings R15 and R20. A `rules/*.md`
`@markdownai` proposal the human sealed becomes `rules` rows (`rules.py`, beside
lore and on the same seal); `table.rules: @phase NAME` answers the body of the
one served row named NAME, byte for byte, citing the served table id. An
unsealed or tampered document serves nothing, and a name that differs by a
letter's case or a space is refused, never matched near.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_api as T  # noqa: E402
from test_api import line, proof, rows  # noqa: E402

from onescript import __main__ as cli  # noqa: E402
from onescript import api, escalate, gate, mai_shape, proposals, rules, serve  # noqa: E402
from onescript.record import h256  # noqa: E402
from onescript.run import Run  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
KEYS = {"willow": b"k-willow", gate.HUMAN: HUMAN_KEY}
LAW = {"trace_ids": ["CONST-III", "CONST-VI"], "grants": []}
CAP = 10**6
# the weak phase has a double space, a trailing-space line and a blank line
DOC = (
    "---\nname: box-rules\n---\n\n@markdownai v1.0\n\n# Rules\n\n"
    "@phase R-weak\nA weak hit (7-9)  lands, but it costs.   \n\n  Indented line.\n\n"
    "@phase R-miss\nA miss (6-) gives the GM a move.\n\n"
    "@phase R-strong\nA strong hit (10+) lands clean.\n"
)
WEAK = "A weak hit (7-9)  lands, but it costs.   \n\n  Indented line."
D0 = escalate._chain()[1]


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-17T00:00:{next(n):06d}Z"


@pytest.fixture
def run(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    r.serve_key = b"serve-key"
    return r


def ident():
    return {
        "who": "willow",
        "family": "qwen",
        "sig": gate.sign(KEYS["willow"], "willow", "qwen"),
    }


def propose(run, path="rules/box.md", data=DOC, cites=("x",)):
    p = {"path": path, "data": data, "cites": list(cites), "claim": "the rules"}
    subject = proposals.subject(p)
    run.rec.append(
        "proposal",
        gate.verify(ident(), KEYS),
        where=path,
        turn=1,
        line=1,
        sha=h256(data),
        verdict="pass",
        reason="",
        subject=subject,
        **p,
    )
    return subject


def seal(run, subject):
    hum = gate.human(subject, proof(subject), HUMAN_KEY)
    run.rec.append("seal", hum, where=subject, subject=subject, proof=proof(subject))


def serve_rules(run):
    chosen = serve.choose(serve.piles(run.rec.rows(), "what"), what="rules")
    ids = [serve.table_id(p) for p in chosen]
    seal(run, serve.scope_subject(ids))
    return run.serve(chosen, ids, max_chars=CAP)


def ask(doc, task):
    return D0.resolve_fixture(escalate.act_of(task, doc))


def sealed_doc(run, **kw):
    s = propose(run, **kw)
    seal(run, s)
    return s, rules.from_sealed(run, s)


# ── the parser ──────────────────────────────────────────────────────────────────
def test_phases_are_read_the_way_mai_reads_them():
    assert rules.phases(DOC) == [
        ("R-weak", WEAK),
        ("R-miss", "A miss (6-) gives the GM a move."),
        ("R-strong", "A strong hit (10+) lands clean."),
    ]


MAI = shutil.which("mai") or "/home/user/tools/bin/mai"


@pytest.mark.skipif(not Path(MAI).exists(), reason="the mai tool is not here")
def test_the_parser_gives_what_mai_resolve_phase_gives(tmp_path):
    # S3: mai reads with read_text, which turns CRLF and a lone CR into LF; so
    # does phases_of ("verbatim" means mai's bytes), and the parity holds for both
    docs = {
        "fixture.md": DOC,
        "crlf.md": DOC.replace("\n", "\r\n"),
        "cr.md": DOC.replace("\n", "\r"),
        "fenced.md": DOC + "\n```\n@phase R-in-a-fence\nexample\n```\n",
    }
    brief = Path("/home/user/sandbox/box-build/SPEC-documented.md")
    if brief.exists():
        docs["spec.md"] = brief.read_text()
    for name, text in docs.items():
        f = tmp_path / name
        f.write_bytes(text.encode())
        for phase, body in mai_shape.phases_of(text):
            out = subprocess.run(
                [
                    MAI,
                    "mai_resolve_phase",
                    json.dumps({"file": str(f), "phase": phase}),
                ],
                capture_output=True,
                text=True,
            ).stdout
            got = json.loads(json.loads(out)[0])
            assert got["content"] == body, (name, phase)


# ── sealing: rows only from a sealed document ─────────────────────────────────
def test_a_sealed_rules_document_makes_one_row_per_phase(run):
    subject, new = sealed_doc(run)
    assert [(r["kind"], r["where"]) for r in new] == [
        ("rules", "R-weak"),
        ("rules", "R-miss"),
        ("rules", "R-strong"),
    ]
    assert new[0]["body"] == WEAK and new[0]["sha"] == h256(DOC)
    assert rules.from_sealed(run, subject) == []  # idempotent


@pytest.mark.parametrize(
    "kw, why",
    [
        ({"path": "notes/box.md"}, "not a rules/*.md"),
        ({"path": "rules/box.txt"}, "not a rules/*.md"),
        ({"cites": ()}, "own-idea"),
        ({"data": "# plain\n@phase R-weak\nx\n"}, "not an @markdownai"),
        (
            {"data": "@markdownai v1.0\n@constraint: no\n@phase R-weak\nx\n"},
            "malformed",
        ),
    ],
)
def test_a_document_that_cannot_be_rules_makes_no_row(run, kw, why):
    s = propose(run, **kw)
    seal(run, s)
    (r,) = rules.from_sealed(run, s)
    assert r["kind"] == "refused" and why in r["reason"]
    assert not [x for x in run.rec.rows() if x["kind"] == "rules"]


def test_an_unsealed_document_makes_no_row(run):
    s = propose(run)
    (r,) = rules.from_sealed(run, s)
    assert r["kind"] == "refused" and "no human seal" in r["reason"]


def test_a_body_with_a_full_hash_is_not_made_into_a_row(run):
    data = DOC + "\n@phase R-hash\nsee " + "ab" * 32 + " for it\n"
    _, new = sealed_doc(run, data=data)
    assert "R-hash" not in [r["where"] for r in new]
    assert [r["where"] for r in new] == ["R-weak", "R-miss", "R-strong"]


def test_a_phase_named_twice_answers_for_neither(run):
    data = DOC + "\n@phase R-miss\nA different miss.\n"
    _, new = sealed_doc(run, data=data)
    assert "R-miss" not in [r["where"] for r in new]


# ── the answer: verbatim, exact, sealed ────────────────────────────────────────
def test_the_named_phase_is_answered_verbatim_citing_the_served_table(run):
    sealed_doc(run)
    doc = serve_rules(run)
    assert doc["state"] == "populated"
    res = ask(doc, "table.rules: @phase R-weak")
    assert (res["status"], res["reason"]) == ("verbatim", None)
    assert res["answer"] == WEAK  # byte for byte: the spaces and the blank line
    assert res["cites"] == [doc["tables"][0]["id"]]
    assert ask(doc, "table.rules: @phase R-miss")["answer"].startswith("A miss")


@pytest.mark.parametrize(
    "task",
    [
        "table.rules: @phase r-weak",  # case
        "table.rules: @phase R-WEAK",
        "table.rules: @Phase R-weak",
        "table.rules: @PHASE R-weak",
        "table.rules: @phase  R-weak",  # two spaces
        "table.rules: @phase R- weak",  # a space inside the name
        "table.rules: @phase R-weak R-miss",  # two names
        "table.rules: @phase",  # no name
        "table.rules: what is @phase R-weak?",  # a question, not a name
        "table.rules: @phase R-wea",  # a prefix
        "table.rules: @phase R-weak\tR-miss",
    ],
)
def test_a_name_that_is_not_exact_is_refused(run, task):
    sealed_doc(run)
    res = ask(serve_rules(run), task)
    assert res["status"] == "escalate" and res["answer"] is None
    assert res["reason"] in ("phase_name_not_exact", "no_phase_on_record")


def test_an_unknown_phase_escalates_with_no_guess(run):
    sealed_doc(run)
    res = ask(serve_rules(run), "table.rules: @phase R-nothing")
    assert (res["status"], res["reason"]) == ("escalate", "no_phase_on_record")


def test_an_unsealed_document_never_answers(run):
    s = propose(run)  # a proposal nobody sealed ...
    me = gate.verify(ident(), KEYS)
    # ... whose rows are forged straight into the record
    for name, body in rules.phases(DOC):
        run.rec.append("rules", me, where=name, body=body, sha=h256(DOC), proposal=s)
    doc = serve_rules(run)
    assert [r for t in doc["tables"] for r in t["rows"]] == []
    res = ask(doc, "table.rules: @phase R-weak")
    assert res["status"] == "escalate" and res["reason"] == "no_phase_on_record"


def test_a_rules_row_with_a_body_the_document_does_not_hold_is_not_served(run):
    s, _ = sealed_doc(run)
    me = gate.verify(ident(), KEYS)
    run.rec.append(
        "rules",
        me,
        where="R-weak",
        body="A weak hit is a win.",
        sha=h256(DOC),
        proposal=s,
    )
    doc = serve_rules(run)
    assert "A weak hit is a win." not in json.dumps(doc)
    assert ask(doc, "table.rules: @phase R-weak")["answer"] == WEAK


def test_a_document_edited_after_sealing_serves_nothing(run):
    s, _ = sealed_doc(run)
    # the stored proposal row is rewritten in place: its hash no longer matches
    path = run.rec.box / "record.jsonl"
    text = path.read_text().replace("lands clean", "lands dirty")
    path.write_text(text)
    run2 = Run(run.rec.box, KEYS, LAW, clock())
    run2.serve_key = b"serve-key"
    doc = serve_rules(run2)
    assert "dirty" not in json.dumps(doc)
    assert ask(doc, "table.rules: @phase R-strong")["status"] == "escalate"


def test_two_sealed_documents_that_disagree_do_not_answer(run):
    sealed_doc(run, path="rules/a.md")
    other = DOC.replace("lands, but it costs", "always wins")
    sealed_doc(run, path="rules/b.md", data=other)
    res = ask(serve_rules(run), "table.rules: @phase R-weak")
    assert (res["status"], res["reason"]) == ("escalate", "conflicting_field")
    assert ask(serve_rules(run), "table.rules: @phase R-miss")["status"] == "verbatim"


def test_the_body_field_rides_only_on_a_rules_row(run):
    me = gate.verify(ident(), KEYS)
    row = run.rec.append("note", me, where="x", body="smuggled")
    assert "body" not in serve.view(row)
    assert serve.view({**row, "kind": "rules"})["body"] == "smuggled"


def test_the_served_name_is_not_normalised(run):
    data = DOC.replace("R-miss", "R-mißs")  # a letter NFKC leaves, casefold would not
    sealed_doc(run, data=data)
    doc = serve_rules(run)
    assert ask(doc, "table.rules: @phase R-mißs")["status"] == "verbatim"
    assert ask(doc, "table.rules: @phase R-MISS")["status"] == "escalate"


# ── through the verb and the chain: no model, the human card when refused ──────
def never(piece, question, model):
    raise AssertionError("a model was called for a rules act")


def sealed_through_api(cfg, data=DOC):
    _, ids = T.served(cfg)
    (p,) = api.take_proposals(cfg, [line("rules/box.md", data, ids[:1], "the rules")])[
        "out"
    ]["proposals"]
    return p["subject"]


def serve_through_api(cfg):
    card = api.scope(cfg, by=("what",), match={"what": "rules"})
    assert card["code"] == 0, card
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    return api.serve(cfg, card["spec"], max_chars=CAP)["doc"]


def test_the_verb_then_the_chain_answers_with_no_model(cfg):
    subject = sealed_through_api(cfg)
    api.seal_proposal(cfg, subject, proof=proof(subject))
    res = api.rules(cfg, subject)
    assert res["code"] == 0 and res["count"] == 3
    assert any("what=rules" in s for s in res["rescope"]["steps"])
    doc = serve_through_api(cfg)
    out = api.escalate(cfg, "table.rules: @phase R-weak", rung=never)
    assert out["d0"]["status"] == "verbatim" and out["escalated"] == 0
    (p,) = out["pieces"]
    assert p["rows"] == [{"status": "verbatim", "answer": WEAK}]
    assert p["cites"] == [doc["tables"][0]["id"]] and out["card"] is None
    assert api.rules(cfg, subject)["count"] == 0


def test_the_verb_refuses_an_unsealed_proposal(cfg):
    subject = sealed_through_api(cfg)
    res = api.rules(cfg, subject)
    assert res["code"] == 1 and "no human seal" in res["refused"]
    assert not [r for r in rows(cfg) if r["kind"] == "rules"]


def test_a_refused_name_goes_to_the_human_card_never_a_model(cfg):
    subject = sealed_through_api(cfg)
    api.seal_proposal(cfg, subject, proof=proof(subject))
    api.rules(cfg, subject)
    serve_through_api(cfg)
    out = api.escalate(cfg, "table.rules: @phase r-weak", rung=never, piece_chars=10**6)
    assert out["d0"]["status"] == "escalate" and out["answered"] == 0
    assert out["escalated"] == 1 and out["card"]["route"] == "willow"


def test_cli_rules_command(cfg, capsys):
    subject = sealed_through_api(cfg)
    api.seal_proposal(cfg, subject, proof=proof(subject))
    code = cli.main(
        [
            "--box",
            str(cfg.box),
            "--keys",
            str(cfg.keys),
            "--venv",
            str(cfg.venv),
            "--now",
            "2026-10-17T00:00:00Z",
            "--no-tests",
            "rules",
            subject,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "rules 3 row(s)" in out and "rescope:" in out


@pytest.fixture
def cfg(tmp_path):
    return T.cfg.__wrapped__(tmp_path)


def test_S3_a_crlf_document_is_answered_with_mai_s_bytes():
    crlf = DOC.replace("\n", "\r\n")
    assert mai_shape.phases_of(crlf) == mai_shape.phases_of(DOC)
    assert "\r" not in "".join(
        b for _, b in mai_shape.phases_of(crlf.replace("\n", "\r"))
    )
    assert rules.phases(crlf) == rules.phases(DOC)


def test_N4_a_phase_line_in_a_fence_opens_a_phase_as_mai_reads_it():
    data = DOC + "\n```\n@phase R-in-a-fence\nexample\n```\n"
    names = [n for n, _ in mai_shape.phases_of(data)]
    assert "R-in-a-fence" in names  # mai's parser does not know fences
    assert "fence" in mai_shape.phases_of.__doc__


def test_N2_dropped_phases_are_named_with_their_reason(cfg):
    data = (
        DOC + "\n@phase R-dup\none\n\n@phase R-dup\ntwo\n"
        "\n@phase R-empty\n\n@phase R-hash\nsee " + "ab" * 32 + "\n"
    )
    subject = sealed_through_api(cfg, data)
    api.seal_proposal(cfg, subject, proof=proof(subject))
    res = api.rules(cfg, subject)
    assert res["dropped"] == {
        "R-dup": "the name is used more than once",
        "R-empty": "the phase is empty",
        "R-hash": "the phase holds a 64-hex run, which serving would alter",
    }
    assert [r["phase"] for r in res["rules"]] == ["R-weak", "R-miss", "R-strong"]
