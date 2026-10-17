"""The act (box-build SPEC R12; SPEC-documented §11 J6, C13): a table act
closes at D0 from served verdict rows, with no model; `G6:` is no class."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, gate, serve, table  # noqa: E402

HUMAN_KEY = b"\x07" * 32
CAP = 200_000


@pytest.fixture
def cfg(tmp_path):
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    keys = tmp_path / "keys" / "keys.json"
    keys.parent.mkdir(mode=0o700)
    keys.write_text(
        json.dumps({"desk": "aa" * 32, "table": "bb" * 32, "human": HUMAN_KEY.hex()})
    )
    keys.chmod(0o600)
    return api.Config(
        box=tmp_path / "box",
        keys=keys,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now="2026-10-17T00:00:00Z",
        no_tests=True,
        phone=True,
    )


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def rows(cfg) -> list[dict]:
    return [json.loads(x) for x in (cfg.box / "record.jsonl").read_text().splitlines()]


def roll(cfg, turn: int, total: int) -> dict:
    v = "strong" if total >= 10 else "weak" if total >= 7 else "miss"
    res = table.verdict(
        cfg,
        {
            "what": "roll",
            "session": "s1",
            "where": "s1",
            "turn": turn,
            "total": total,
            "verdict": v,
            "reason": f"2d6+2 = {total}: {'10+' if v == 'strong' else '7-9' if v == 'weak' else '6-'}",
        },
    )
    assert res["code"] == 0, res
    return res["row"]


def served_table(cfg) -> dict:
    """Check in, write two rolls, scope the verdict pile, seal it, serve it."""
    assert api.checkin(cfg)["code"] == 0
    roll(cfg, 0, 11)
    roll(cfg, 1, 8)
    card = api.scope(cfg, by=("what",), match={"what": "verdict"})
    assert card["code"] == 0
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    doc = api.serve(cfg, card["spec"], max_chars=CAP)["doc"]
    assert doc["state"] == "populated", doc
    return doc


def never(piece, question, model):
    raise AssertionError("a model was called for a table act")


def test_a_table_roll_act_closes_at_d0_with_no_model(cfg):
    doc = served_table(cfg)
    res = api.escalate(cfg, "table.roll: turn 1", rung=never)
    assert "refused" not in res, res
    assert res["d0"]["status"] == "resolved" and res["escalated"] == 0
    (p,) = res["pieces"]
    assert p["rung"] == "d0" and p["outcome"] == "answered"
    assert p["rows"] == [{"status": "resolved", "answer": {"total": 8, "verdict": "weak"}}]
    assert p["cites"] == [doc["tables"][0]["id"]]  # the served id, nothing else
    assert res["card"] is None


def test_the_served_roll_carries_its_total(cfg):
    doc = served_table(cfg)
    served = [r for t in doc["tables"] for r in t["rows"]]
    assert [r["total"] for r in served] == [11, 8]
    assert "total" in serve.PAYLOAD


def test_a_table_state_act_tallies_at_d0(cfg):
    served_table(cfg)
    res = api.escalate(cfg, "table.state: tally", rung=never)
    assert res["d0"]["status"] == "resolved"
    (p,) = res["pieces"]
    assert p["rows"][0]["answer"]["path"] == "strong-weak"


def test_R20_a_roll_not_on_record_goes_straight_to_the_human_never_a_model(cfg):
    """B1 / R20: D0 has no answer for turn 9; the act goes to the human card
    the way an empty scope does. No model rung is asked, so an invented roll
    can never read as answered."""
    doc = served_table(cfg)
    calls = []

    def inventing(piece, question, model):
        calls.append(question)
        return [{"path": "table/turn-9", "data": "turn 9 rolled 12, strong",
                 "cites": [doc["tables"][0]["id"]], "claim": "turn 9 rolled 12"}]

    res = api.escalate(cfg, "table.roll: turn 9", rung=inventing, piece_chars=10**6)
    assert res["d0"] == {"status": "escalate", "reason": "no_roll_on_record"}
    assert calls == [] and res["answered"] == 0 and res["escalated"] == 1
    (p,) = res["pieces"]
    assert p["rung"] == "d0" and p["label"] == "escalated:d0_escalate"
    assert "no_roll_on_record" in p["detail"]
    assert res["card"]["route"] == "willow" and res["card"]["kind"] == "review"
    assert "12" not in json.dumps(res["pieces"])


def test_B4_the_served_roll_names_its_session(cfg):
    doc = served_table(cfg)
    served = [r for t in doc["tables"] for r in t["rows"]]
    assert {r["session"] for r in served} == {"s1"}
    assert "session" in serve.PAYLOAD
    res = api.escalate(cfg, "table.roll: turn 1 session s1", rung=never)
    assert res["d0"]["status"] == "resolved"


def test_G6_prefix_is_not_a_class(cfg):
    served_table(cfg)
    res = api.escalate(cfg, "G6: turn 1", rung=never, piece_chars=1)
    # classless: D0 escalates it; with piece_chars=1 every piece is capped by
    # code, so the stub is never reached and nothing is answered
    assert res["d0"]["status"] == "escalate" and res["d0"]["reason"] == "unknown_class"
    assert all(p["label"] == "escalated:cap" for p in res["pieces"])


def test_an_escalate_verdict_row_is_a_verdict_never_a_seal(cfg):
    assert api.checkin(cfg)["code"] == 0
    ok = table.verdict(
        cfg,
        {
            "what": "escalate",
            "session": "s1",
            "verdict": "escalate",
            "reason": "the model picked act:Fly, not on the menu; code took act:Grit",
        },
    )
    assert ok["code"] == 0 and ok["row"]["kind"] == "verdict"
    bad = table.verdict(
        cfg,
        {"what": "escalate", "session": "s1", "verdict": "escalate", "proof": "x"},
    )
    assert bad["code"] == 1 and "never a seal" in bad["refused"]
