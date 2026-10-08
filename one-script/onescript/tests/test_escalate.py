"""escalate: the deterministic chain on the one script, with a stub local rung
(no Ollama, no Rat).

D0 closes what it can; an empty scope goes straight up with no model call;
pieces are deterministic; a recorded hash is reused and no model runs; an
ESCALATE row is never a write; silence, a timeout, a cap and an uncited answer
are escalations with a reason; what no rung answered is ONE card.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, escalate, gate  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
CAP = 10**6
TASK = "Summarize what each table shows"
FAR = "f" * 64


@pytest.fixture
def cfg(tmp_path):
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    keys = tmp_path / "keys" / "keys.json"
    keys.parent.mkdir(mode=0o700)
    keys.write_text(json.dumps({"desk": "aa" * 32, "human": HUMAN_KEY.hex()}))
    keys.chmod(0o600)
    return api.Config(
        box=tmp_path / "box",
        keys=keys,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now="2026-10-08T00:00:00Z",
        no_tests=True,
        phone=True,
    )


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def rows(cfg) -> list[dict]:
    text = (cfg.box / "record.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def served(cfg, by=("what",), max_chars=CAP) -> dict:
    """Check in, scope, seal, serve. The served document."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=by)
    assert card["code"] == 0 and card["subject"].startswith("serve:")
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    return api.serve(cfg, card["spec"], max_chars=max_chars)["doc"]


class Stub:
    """A local rung that records what it was handed."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, piece, question, model):
        self.calls.append((piece, question, model))
        return self.fn(piece, question, model)


def tid(piece) -> str:
    return piece["tables"][0]["id"]


def good(piece, question, model):
    t = tid(piece)
    return [
        {
            "path": f"notes/{t[:8]}.md",
            "data": "a note\n",
            "cites": [t],
            "claim": "what the table shows",
        }
    ]


def mark(reason="not enough here"):
    def fn(piece, question, model):
        return [
            {"path": "ESCALATE", "data": "", "cites": [tid(piece)], "claim": reason}
        ]

    return fn


def never(piece, question, model):
    raise AssertionError("a model was called")


def go(cfg, rung, task=TASK, **kw):
    kw.setdefault("piece_chars", 10**6)  # one piece a table, unless a test cuts
    res = api.escalate(cfg, task, rung=rung, **kw)
    assert "refused" not in res, res
    return res


# ── D0 first ────────────────────────────────────────────────────────────────
def test_d0_closes_a_route_task_with_no_model_call(cfg):
    served(cfg)
    stub = Stub(never)
    res = go(cfg, stub, "Route this review to its owner")
    assert stub.calls == []
    (p,) = res["pieces"]
    assert (p["rung"], p["outcome"], p["label"]) == ("d0", "answered", "answered")
    assert res["card"] is None and res["code"] == 0
    assert res["d0"]["status"] == "routed"
    assert [r["rung"] for r in rows(cfg) if r["kind"] == "escalate_rung"] == ["d0"]


def test_d0_escalate_is_what_goes_on_to_the_pieces(cfg):
    doc = served(cfg)
    stub = Stub(good)
    res = go(cfg, stub)
    assert res["d0"]["status"] == "escalate"
    assert len(stub.calls) == len(doc["tables"])
    assert {p["rung"] for p in res["pieces"]} == {"local"}


# ── 'only along side': an empty scope is never handed to a model ────────────
def test_no_served_file_escalates_straight_up(cfg):
    assert api.checkin(cfg)["code"] == 0
    stub = Stub(never)
    res = go(cfg, stub)
    assert stub.calls == []
    (p,) = res["pieces"]
    assert p["label"] == "escalated:empty_scope" and p["rung"] == "d0"
    assert res["card"]["route"] == "willow" and res["card"]["kind"] == "review"


def test_an_empty_served_state_escalates_straight_up(cfg):
    doc = served(cfg, max_chars=1)  # over the cap: serve says empty
    assert doc["state"] == "empty"
    stub = Stub(never)
    res = go(cfg, stub)
    assert stub.calls == []
    (p,) = res["pieces"]
    assert p["label"] == "escalated:empty_scope"
    assert "empty" in res["why"]


def test_a_served_file_edited_after_serving_is_no_scope(cfg):
    served(cfg)
    (cfg.box / "served.json").write_text('{"state": "populated", "tables": []}\n')
    stub = Stub(never)
    res = go(cfg, stub)
    assert stub.calls == []
    assert res["pieces"][0]["label"] == "escalated:empty_scope"


def test_a_closed_box_refuses_and_calls_no_model(cfg):
    stub = Stub(never)
    res = api.escalate(cfg, TASK, rung=stub)
    assert "no check-in" in res["refused"] and stub.calls == []


def test_a_blank_task_is_refused(cfg):
    served(cfg)
    assert "needs a task" in api.escalate(cfg, "  ", rung=Stub(never))["refused"]


# ── the piece cutter ────────────────────────────────────────────────────────
def test_pieces_are_deterministic_and_hashed(cfg):
    doc = served(cfg)
    a = escalate.cut(doc, TASK, 10**6)
    b = escalate.cut(json.loads(json.dumps(doc)), TASK, 10**6)
    assert a == b
    assert [p["table"] for p in a] == [t["id"] for t in doc["tables"]]
    assert len({p["hash"] for p in a}) == len(a)
    for p in a:
        assert p["piece"]["state"] == "populated"
        assert p["hash"] == escalate.piece_hash(p["piece"], TASK)
    assert escalate.cut(doc, "another question", 10**6)[0]["hash"] != a[0]["hash"]


def test_a_table_over_the_budget_is_cut_into_row_groups(cfg):
    doc = served(cfg, by=("who",))  # every row of the run in one table
    (big,) = doc["tables"]
    assert len(big["rows"]) > 1
    one = {"state": "populated", "why": "", "return": doc["return"], "tables": [big]}
    budget = len(json.dumps(one)) // 2
    pieces = [p for p in escalate.cut(one, TASK, budget) if p["table"] == big["id"]]
    assert len(pieces) > 1
    got = [r for p in pieces for r in p["piece"]["tables"][0]["rows"]]
    assert got == big["rows"]  # in order, none dropped, none repeated
    assert escalate.cut(one, TASK, budget) == escalate.cut(one, TASK, budget)


def test_a_row_too_big_for_any_piece_is_capped_by_code_with_no_model(cfg):
    served(cfg)
    stub = Stub(never)
    res = go(cfg, stub, piece_chars=1)
    assert stub.calls == []
    assert {p["label"] for p in res["pieces"]} == {"escalated:cap"}
    assert res["card"] is not None


# ── write-back by hash ──────────────────────────────────────────────────────
def test_a_recorded_answer_is_reused_and_no_model_runs(cfg):
    served(cfg)
    first = Stub(good)
    a = go(cfg, first)
    assert first.calls and a["answered"] == len(first.calls)
    again = Stub(never)
    b = go(cfg, again)
    assert again.calls == []
    assert {p["rung"] for p in b["pieces"]} == {"hash"}
    assert [p["piece"] for p in b["pieces"]] == [p["piece"] for p in a["pieces"]]
    assert [p["rows"] for p in b["pieces"]] == [p["rows"] for p in a["pieces"]]
    reused = [
        r for r in rows(cfg) if r["kind"] == "escalate_rung" and r["rung"] == "hash"
    ]
    assert len(reused) == len(b["pieces"]) and all(
        r["reused"] is not None for r in reused
    )


def test_a_recorded_escalation_is_reused_too(cfg):
    served(cfg)
    go(cfg, Stub(mark("too thin")))
    again = Stub(never)
    b = go(cfg, again)
    assert again.calls == []
    assert {p["label"] for p in b["pieces"]} == {"escalated:local_escalate"}
    assert {p["rung"] for p in b["pieces"]} == {"hash"}
    assert {p["detail"] for p in b["pieces"]} == {"too thin"}


# ── an escalation is a proposal, never a write ──────────────────────────────
def test_escalate_rows_never_write_and_are_never_proposals(cfg):
    served(cfg)
    res = go(cfg, Stub(mark("the rows don't say")))
    assert {p["label"] for p in res["pieces"]} == {"escalated:local_escalate"}
    assert {p["detail"] for p in res["pieces"]} == {"the rows don't say"}
    assert not (cfg.box / "ESCALATE").exists()
    assert not [r for r in rows(cfg) if r["kind"] == "proposal"]
    assert not [
        r for r in rows(cfg) if r["kind"] == "write" and r["path"] != "served.json"
    ]
    assert not [r for r in rows(cfg) if r.get("path") == "ESCALATE"]
    assert not any(p.name == "ESCALATE" for p in cfg.box.rglob("*"))


def test_an_answer_goes_through_the_proposal_handling_and_nothing_is_written(cfg):
    served(cfg)
    res = go(cfg, Stub(good))
    props = [r for r in rows(cfg) if r["kind"] == "proposal"]
    assert len(props) == res["answered"] > 0
    assert {r["verdict"] for r in props} == {"pass"}
    assert not (cfg.box / "notes").exists()
    assert not [
        r for r in rows(cfg) if r["kind"] == "write" and r["path"] != "served.json"
    ]


# ── what a rung can fail at is an escalation with a reason ──────────────────
def raises(exc):
    def fn(piece, question, model):
        raise exc

    return fn


def cited(cites, **kw):
    def fn(piece, question, model):
        return [
            {
                "path": f"notes/{tid(piece)[:8]}.md",
                "data": "d",
                "cites": cites if cites != "own" else [tid(piece)],
                "claim": "c",
                **kw,
            }
        ]

    return fn


@pytest.mark.parametrize(
    "fn, reason",
    [
        (lambda p, q, m: [], "silent"),
        (raises(escalate.RungTimeout("slow")), "timeout"),
        (raises(escalate.RungCap("big")), "cap"),
        (raises(escalate.RungError("exit 2")), "rung_error"),
        (raises(ValueError("boom")), "rung_error"),
        (lambda p, q, m: "not a list", "rung_error"),
        (lambda p, q, m: ["not a row"], "rung_error"),
        (cited([]), "uncited"),
        (cited([FAR]), "uncited"),
        (cited("own", path="../outside.md"), "rung_error"),  # the contract refuses
    ],
)
def test_a_failed_rung_is_an_escalation_with_its_reason(cfg, fn, reason):
    served(cfg)
    res = go(cfg, Stub(fn))
    assert res["answered"] == 0
    assert {p["label"] for p in res["pieces"]} == {f"escalated:{reason}"}
    assert res["card"] is not None
    assert not [
        r for r in rows(cfg) if r["kind"] == "write" and r["path"] != "served.json"
    ]


def test_a_cite_to_another_piece_is_uncited(cfg):
    doc = served(cfg)
    other = doc["tables"][-1]["id"]

    def fn(piece, question, model):
        return [
            {
                "path": f"notes/{tid(piece)[:8]}.md",
                "data": "d",
                "cites": [other],
                "claim": "c",
            }
        ]

    res = go(cfg, Stub(fn))
    labels = [p["label"] for p in res["pieces"]]
    assert len(labels) == len(doc["tables"]) > 1
    # only the last table's own piece cites a table that is in it
    assert labels[-1] == "answered"
    assert set(labels[:-1]) == {"escalated:uncited"}


# ── the flowering card ──────────────────────────────────────────────────────
def test_everything_unanswered_is_one_card(cfg):
    doc = served(cfg)
    res = go(cfg, Stub(mark("no")))
    n = len(doc["tables"])
    assert res["escalated"] == n and res["answered"] == 0
    card = res["card"]
    assert card["kind"] == "review" and card["route"] == "willow"
    assert card["title"].startswith(TASK)
    assert card["summary"].count("\n- ") == n - 1 and card["summary"].startswith("- ")
    assert "escalated:local_escalate at local" in card["summary"]
    row_ids = [str(p["row"]) for p in res["pieces"]]
    assert card["source_ref"] == "record:" + ",".join(row_ids)
    cards = [r for r in rows(cfg) if r["kind"] == "escalate_card"]
    assert len(cards) == 1 and cards[0]["card"] == card


def test_a_mixed_run_answers_some_and_cards_the_rest(cfg):
    doc = served(cfg)
    first = doc["tables"][0]["id"]

    def fn(piece, question, model):
        return (
            good(piece, question, model)
            if tid(piece) == first
            else mark("no")(piece, question, model)
        )

    res = go(cfg, Stub(fn))
    assert res["answered"] == 1 and res["escalated"] == len(doc["tables"]) - 1
    card = res["card"]
    assert card["summary"].count("\n- ") + 1 == res["escalated"]
    answered = [p for p in res["pieces"] if p["outcome"] == "answered"]
    assert answered[0]["rows"][0]["cites"] == [first]
    refs = card["source_ref"].split(":")[1].split(",")
    assert str(answered[0]["row"]) not in refs and len(refs) == res["escalated"]


def test_all_answered_is_no_card(cfg):
    served(cfg)
    res = go(cfg, Stub(good))
    assert res["card"] is None and res["escalated"] == 0
    assert not [r for r in rows(cfg) if r["kind"] == "escalate_card"]


def test_every_rung_row_is_recorded_by_hash_and_the_chain_holds(cfg):
    served(cfg)
    res = go(cfg, Stub(mark("no")))
    rung_rows = [r for r in rows(cfg) if r["kind"] == "escalate_rung"]
    assert [r["piece"] for r in rung_rows] == [p["piece"] for p in res["pieces"]]
    assert all(
        len(r["piece"]) == 64 and r["label"].startswith("escalated:") for r in rung_rows
    )
    from onescript import record

    assert record.Record(cfg.box, lambda: "", "", gate.token()).verify_chain() == []


# ── the vocabulary ──────────────────────────────────────────────────────────
def test_the_vocabulary_is_one_mapping():
    assert escalate.label("answered", None) == "answered"
    assert [escalate.label("escalated", r) for r in escalate.REASONS] == [
        f"escalated:{r}" for r in escalate.REASONS
    ]
    assert escalate.REASONS == (
        "d0_escalate",
        "local_escalate",
        "uncited",
        "silent",
        "timeout",
        "cap",
        "rung_error",
        "empty_scope",
    )
    with pytest.raises(ValueError):
        escalate.label("escalated", "tired")


# ── the default rung: Rat as a subprocess ───────────────────────────────────
FAKE = textwrap.dedent(
    """
    import json, sys
    a = sys.argv[1:]
    out = a[a.index("--out") + 1]
    served = json.load(open(a[a.index("--served") + 1]))
    mode = "__MODE__"
    if mode == "exit":
        sys.exit(3)
    if mode == "sleep":
        import time; time.sleep(30)
    if mode == "silent":
        sys.exit(0)
    if mode == "garbage":
        open(out, "w").write("not json\\n")
        sys.exit(0)
    t = served["tables"][0]["id"]
    open(out, "w").write(json.dumps(
        {"path": "n/a.md", "data": "d", "cites": [t], "claim": "c"}) + "\\n")
    """
)


@pytest.fixture
def fake_in(tmp_path):
    """A stand-in for `ratatosk`: a script that behaves as its mode says. The
    rung's environment is minimal, so the mode is baked in, not passed."""

    def make(mode=""):
        script = tmp_path / f"fake_ratatosk_{mode or 'ok'}.py"
        script.write_text(FAKE.replace("__MODE__", mode))
        return (sys.executable, str(script))

    return make


def test_the_default_rung_runs_rat_with_the_fixed_argv(fake_in, monkeypatch):
    fake = fake_in()
    monkeypatch.setenv("SECRET_TOKEN", "x")
    seen = {}
    real = escalate.subprocess.run

    def spy(argv, **kw):
        seen["argv"], seen["env"], seen["cwd"] = argv, kw["env"], kw["cwd"]
        return real(argv, **kw)

    monkeypatch.setattr(escalate.subprocess, "run", spy)
    piece = {
        "state": "populated",
        "why": "",
        "return": "r",
        "tables": [{"id": "abc", "rows": []}],
    }
    got = escalate.ratatosk_rung(fake, 20)(piece, "what is it?", "m1")
    assert got == [{"path": "n/a.md", "data": "d", "cites": ["abc"], "claim": "c"}]
    argv = seen["argv"][2:]
    assert argv[0] == "--onescript"
    assert argv[1] == "--served" and argv[3] == "--out"
    assert argv[5:] == ["--model", "m1", "what is it?"]
    assert "SECRET_TOKEN" not in seen["env"] and "PATH" in seen["env"]
    assert not os.path.exists(argv[2])  # the scratch directory is gone


@pytest.mark.parametrize(
    "mode, exc",
    [
        ("exit", escalate.RungError),
        ("garbage", escalate.RungError),
        ("sleep", escalate.RungTimeout),
    ],
)
def test_the_default_rung_failures_raise_their_kind(fake_in, mode, exc):
    with pytest.raises(exc):
        escalate.ratatosk_rung(fake_in(mode), 1)({"tables": []}, "q", "m")


def test_the_default_rung_silence_is_no_rows_and_a_missing_command_is_an_error(fake_in):
    assert escalate.ratatosk_rung(fake_in("silent"), 20)({"tables": []}, "q", "m") == []
    with pytest.raises(escalate.RungError):
        escalate.ratatosk_rung(("/no/such/ratatosk",), 5)({"tables": []}, "q", "m")


def test_an_oversize_output_is_a_cap(fake_in):
    rung = escalate.ratatosk_rung(fake_in(), 20, out_cap=5)
    with pytest.raises(escalate.RungCap):
        rung({"tables": [{"id": "abc"}]}, "q", "m")


# ── the CLI ─────────────────────────────────────────────────────────────────
def test_the_cli_hands_the_task_and_flags_to_the_api(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_escalate(cfg, task, **kw):
        seen.update(task=task, **kw)
        return {"pieces": [], "card": None, "code": 0}

    monkeypatch.setattr(cli.api, "escalate", fake_escalate)
    code = cli.main(
        [
            "--box", str(tmp_path / "b"),
            "--keys", str(tmp_path / "k" / "keys.json"),
            "escalate", "do the thing", "--model", "m2", "--piece-chars", "900",
            "--rung-timeout", "7", "--ratatosk", "python3 /x/rat.py",
        ]
    )  # fmt: skip
    assert code == 0
    assert seen["task"] == "do the thing" and seen["model"] == "m2"
    assert seen["piece_chars"] == 900 and seen["rung_timeout"] == 7.0
    assert seen["ratatosk"] == ["python3", "/x/rat.py"]
    assert json.loads(capsys.readouterr().out) == {"pieces": [], "card": None}
