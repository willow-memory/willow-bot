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
    assert card["subject"] in serve.claimed_seals(run.rec.rows())  # the row claims it
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


def test_an_unreachable_newer_seal_does_not_hide_a_verified_older_one(cfg):
    """One seal row that can't be checked here (no keyring) is shown beside
    the verified seal, never in place of it."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    other = {"subject": "serve:" + "2" * 64}  # any scope; it never verifies
    pair = {
        "source_norm": "x",
        "target_text": other["subject"],
        "verifier": "sean campbell",
        "seal_sig": "00" * 64,
        "status": "sealed",
    }
    forge_seal(cfg, other["subject"], via="nestor", verifier="sean campbell", pair=pair)
    got = api.resume(cfg)
    assert got["subject"] == card["subject"] and got["spec"] == card["spec"]
    assert got["newer"]["subject"] == other["subject"]
    assert got["newer"]["state"] == "unreachable"


def test_resume_skips_a_forged_seal_over_a_subject_the_human_really_sealed(cfg):
    """A sealed subject vouches for its own seal row, not for every row that
    names it: a forged scope and seal after the real one change nothing."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    real = api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["row"]
    rs = rows(cfg)
    bad = {
        "n": len(rs),
        "kind": "scope",
        "who": "desk",
        "family": "system",
        "standing": "system",
        "version": rs[-1]["version"],
        "ts": "2026-10-08T00:00:00Z",
        "prev": rs[-1]["hash"],
        "where": card["subject"],
        "spec": {"by": ["who"], "match": {"who": "anyone"}, "upto": 0},
    }
    bad["hash"] = record.h256(record.canon(bad))
    with (cfg.box / "record.jsonl").open("a") as f:
        f.write(record.canon(bad) + "\n")
    forge_seal(cfg, card["subject"], ts="2099-01-01T00:00:00Z")  # no proof
    got = api.resume(cfg)
    assert got["spec"] == card["spec"] and got["subject"] == card["subject"]
    assert got["sealed_at"] == real["ts"]


def test_a_malformed_nestor_pair_is_no_seal_even_with_nothing_to_check_by(cfg):
    """With no keyring, a row whose pair isn't shaped like a seal is no seal,
    never `unreachable`: its shape is checked before the ring and store."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    forge_seal(cfg, card["subject"], via="nestor", verifier="x", pair={"x": 1})
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "empty"
    assert "state" not in api.resume(cfg)


def test_an_unreachable_seal_stays_marked_on_the_morning_screen(tmp_path):
    """A door or proposal whose seal can't be checked stays listed and says so,
    never as plainly awaiting (empty and unreachable are not collapsed)."""
    from onescript import reverse, view

    rows_ = [
        {"n": 0, "kind": "door", "hash": "h1", "ts": "t", "verdict": "awaiting_seal"},
        {"n": 1, "kind": "proposal", "subject": "s1", "path": "p", "verdict": "pass"},
        {"n": 2, "kind": "door", "hash": "h2", "ts": "t", "verdict": "awaiting_seal"},
    ]
    why = {"h1": "unreachable: no keyring", "s1": "unreachable: no store"}
    rep = reverse.reconcile(tmp_path, {}, rows_, "v", held=set(), unreachable=why)
    door1, door2 = rep["awaiting"]
    assert door1["seal"] == "unreachable" and "seal" not in door2
    assert rep["proposals"][0]["seal_why"] == "unreachable: no store"
    lines = view.needs_you(rep, None, [], [])
    assert sum("seal unreachable" in x for x in lines) == 2


def test_held_checks_by_the_key_it_is_handed(tmp_path):
    """`checkout` and `reconciled` hand a key over; `held` checks by that key
    when the run's own keys hold no human key."""
    from onescript.run import Run

    keys = {"hanuman": b"k-hanuman"}  # no human key on the run
    run = Run(tmp_path / "box", keys, LAW, lambda: "t", anchor=tmp_path / "a.json")
    hum = gate.human("serve:x", proof("serve:x"), HUMAN_KEY)
    run.rec.append(
        "seal", hum, where="serve:x", subject="serve:x", proof=proof("serve:x")
    )
    assert "serve:x" in run.held()[1]  # nothing to check by: unreachable
    assert "serve:x" in run.held(human_key=HUMAN_KEY)[0]
