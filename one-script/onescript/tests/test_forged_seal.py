"""A5: a seal row appended by hand is not a seal.

Anyone who can write the box's record can append a row that chains: `kind:
"seal"`, `who: "human"`, `prev` the last hash, `hash` the canonical row. No key
is needed, `verify_chain()` passes, and with no anchored tip `verify_anchor()` is
silent. The seal is the proof the row carries, re-checked on every read
(`serve.sealed_verified`); the row's `who` is a label. Without the key, keyring
or store to check by, the answer is `unreachable`, never a pass.

Found 2026-10-17 (willows-grove seat/willow/experiments/foreshadow-2026-10-16,
§7, attack A5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, gate, record, serve  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
CAP = 10**6


def make_cfg(tmp_path, keys: dict) -> api.Config:
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    kf = tmp_path / "keys" / "keys.json"
    kf.parent.mkdir(mode=0o700)
    kf.write_text(json.dumps(keys))
    kf.chmod(0o600)
    return api.Config(
        box=tmp_path / "box",
        keys=kf,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now="2026-10-08T00:00:00Z",
        no_tests=True,
        phone=True,
    )


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path, {"desk": "aa" * 32, "human": HUMAN_KEY.hex()})


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def rows(cfg) -> list[dict]:
    return [json.loads(x) for x in (cfg.box / "record.jsonl").read_text().splitlines()]


def forge_seal(cfg, subject: str, **extra) -> dict:
    """A5 exactly: a seal row by hand, chained and hashed as `record.canon`
    hashes it, with no key behind it."""
    rs = rows(cfg)
    row = {
        "n": len(rs),
        "kind": "seal",
        "who": "human",
        "family": "human",
        "standing": "sealed",
        "version": rs[-1]["version"],
        "ts": "2026-10-08T00:00:00Z",
        "prev": rs[-1]["hash"],
        "where": subject,
        "subject": subject,
        **extra,
    }
    row["hash"] = record.h256(record.canon(row))
    with (cfg.box / "record.jsonl").open("a") as f:
        f.write(record.canon(row) + "\n")
    return row


def test_a_hand_appended_scope_seal_chains_and_still_serves_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"])
    run, _ = api._open(cfg, "probe", ())
    assert run.rec.verify_chain() == []  # the forgery chains: the chain can't catch it
    assert card["subject"] in serve.sealed(run.rec.rows())  # the row claims it
    assert card["subject"] not in run.held()[0]  # the proof says no
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "empty" and res["doc"]["tables"] == []
    assert api.resume(cfg) == {"resume": None, "code": 0}


def test_a_hand_appended_proposal_seal_leaves_it_pooled_and_unwritten(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "populated"
    ids = [t["id"] for t in res["doc"]["tables"]]
    line = json.dumps(
        {"path": "notes/a.md", "data": "hello\n", "cites": ids[:1], "claim": "a note"}
    )
    (p,) = api.take_proposals(cfg, [line])["out"]["proposals"]
    assert p["verdict"] == "pass"
    forge_seal(cfg, p["subject"])
    assert [x["subject"] for x in api.pooled(cfg)["pooled"]] == [p["subject"]]
    run, _ = api._open(cfg, "probe", ())
    out = run.write_proposal(p["subject"])
    assert out["kind"] == "refused" and "no human seal" in out["reason"]
    assert not (cfg.box / "notes" / "a.md").exists()


def test_a_wrong_proof_on_the_row_is_no_seal(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"], proof=gate.sign(b"not-the-key", "seal", "x"))
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "empty"


def test_a_real_seal_row_still_serves(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    got = api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))
    assert got["sealed"] and got["row"]["proof"] == proof(card["subject"])
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "populated"


def test_with_no_key_to_check_by_a_proof_is_unreachable_never_a_pass(tmp_path):
    cfg = make_cfg(tmp_path, {"desk": "aa" * 32})  # no human key on this box
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"], proof=proof(card["subject"]))
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "unreachable" and res["doc"]["tables"] == []
    assert "no human key" in res["why"]
    got = api.resume(cfg)
    assert got["resume"] is None and got["state"] == "unreachable"


def test_with_no_keyring_a_nestor_row_is_unreachable_never_a_pass(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    pair = {
        "source_norm": "x",
        "target_text": card["subject"],
        "verifier": "sean campbell",
        "seal_sig": "00" * 64,
        "status": "sealed",
    }
    forge_seal(cfg, card["subject"], via="nestor", verifier="sean campbell", pair=pair)
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "unreachable" and res["doc"]["tables"] == []


def test_a_nestor_row_with_no_pair_is_no_seal(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"], via="nestor", verifier="sean campbell")
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "empty"


# ── the morning screen, and seal rows from before they carried a proof ───────
KEYS = {"hanuman": b"k-hanuman", gate.HUMAN: HUMAN_KEY}
LAW = {"trace_ids": ["CONST-III", "CONST-VI"], "min_features": 6}


def forge_row(path: Path, subject: str) -> None:
    """`forge_seal` for a bare Run's record."""
    rs = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    row = {
        "n": len(rs),
        "kind": "seal",
        "who": "human",
        "family": "human",
        "standing": "sealed",
        "version": rs[-1]["version"],
        "ts": "t",
        "prev": rs[-1]["hash"],
        "where": subject,
        "subject": subject,
    }
    row["hash"] = record.h256(record.canon(row))
    with path.open("a") as f:
        f.write(record.canon(row) + "\n")


def test_the_human_s_key_on_another_subject_is_no_seal(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"], proof=proof("serve:" + "1" * 64))
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "empty"


def test_a_forged_seal_does_not_clear_a_door_awaiting_the_human(tmp_path):
    from onescript.run import Run

    run = Run(tmp_path / "box", KEYS, LAW, lambda: "t")
    sig = gate.sign(KEYS["hanuman"], "hanuman", "qwen")
    out = run.turn(
        {"who": "hanuman", "family": "qwen", "sig": sig},
        "amend",
        change={"kind": "edit", "amends": ["CONST-III"], "what": "widen reach"},
    )
    door = out["door"]
    assert door["verdict"] == "awaiting_seal"
    forge_row(run.rec.path, door["hash"])
    rep, _ = run.checkout()
    assert [a["hash"] for a in rep["awaiting"]] == [door["hash"]]


def test_a_seal_row_without_proof_holds_only_inside_the_sealed_tip(tmp_path):
    """Seal rows written before they carried their proof: the human's signed
    tip vouches for every row up to it, and for nothing after it."""
    from onescript.run import Run

    run = Run(tmp_path / "box", KEYS, LAW, lambda: "t", anchor=tmp_path / "a.json")
    hum = gate.human("serve:old", proof("serve:old"), HUMAN_KEY)
    run.rec.append("seal", hum, where="serve:old", subject="serve:old")  # no proof
    assert "serve:old" not in run.held()[0]  # nothing vouches for it yet
    tip = run.rec.tip()
    run.seal_tip(proof(record.tip_subject(tip)), HUMAN_KEY)
    assert "serve:old" in run.held()[0]
    forge_row(run.rec.path, "serve:new")  # after the tip: a claim, nothing more
    assert run.rec.verify_chain() == []
    assert "serve:new" not in run.held()[0]


def test_an_old_nestor_seal_row_holds_only_inside_the_sealed_tip(tmp_path):
    """Nestor seal rows from before they carried their pair (`via`, `verifier`,
    no `pair`) hold inside the human's signed tip, like old HMAC rows."""
    from onescript.run import Run

    run = Run(tmp_path / "box", KEYS, LAW, lambda: "t", anchor=tmp_path / "a.json")
    hum = gate.human("serve:old", proof("serve:old"), HUMAN_KEY)
    run.rec.append(
        "seal",
        hum,
        where="serve:old",
        subject="serve:old",
        via="nestor",
        verifier="sean campbell",
    )  # no pair
    assert "serve:old" not in run.held()[0]
    tip = run.rec.tip()
    run.seal_tip(proof(record.tip_subject(tip)), HUMAN_KEY)
    assert "serve:old" in run.held()[0]
