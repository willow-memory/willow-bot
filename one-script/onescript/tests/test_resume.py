"""resume: the last human-sealed scope, recovered from the record.

Read-only. The seat serves the recorded `spec` (its `upto` pinned) instead of
re-scoping at the latest row, which moves the subject once the record has grown
and leaves the human's seal over a subject that no longer exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
CAP = 10**6


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
        now="2026-10-10T00:00:00Z",
        no_tests=True,
        phone=True,
    )


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def sealed_scope(cfg) -> dict:
    """Check in, scope the whole record, and have the human seal that subject."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",))
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    return card


def grow(cfg) -> None:
    """The desk works: the record gets rows the sealed scope never saw."""
    api.take_proposals(cfg, ["not json at all", "still not json"])


def rows(cfg) -> list[dict]:
    path = cfg.box / "record.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_resume_returns_the_sealed_spec_pinned_at_its_upto_not_latest(cfg):
    card = sealed_scope(cfg)
    sealed_upto = card["spec"]["upto"]
    grow(cfg)
    assert rows(cfg)[-1]["n"] > sealed_upto
    got = api.resume(cfg)
    assert got["code"] == 0
    assert got["spec"] == card["spec"]
    assert got["spec"]["upto"] == sealed_upto  # NOT the latest row
    assert got["subject"] == card["subject"]


def test_serving_the_resumed_spec_stays_populated_after_the_record_grows(cfg):
    card = sealed_scope(cfg)
    grow(cfg)
    # the drift this exists to stop: a fresh scope at latest names another subject
    drifted = api.scope(cfg, by=("who",))
    assert drifted["subject"] != card["subject"]
    assert drifted["spec"]["upto"] > card["spec"]["upto"]
    served = api.serve(cfg, drifted["spec"], max_chars=CAP)
    assert served["state"] == "empty" and "no human seal" in served["why"]
    # the pin does not
    got = api.resume(cfg)
    res = api.serve(cfg, got["spec"], max_chars=CAP)
    assert res["state"] == "populated", res


def test_resume_is_the_newest_seal_and_its_own_scope_row(cfg):
    first = sealed_scope(cfg)
    grow(cfg)
    second = api.scope(cfg, by=("who",))
    assert second["subject"] != first["subject"]
    assert api.resume(cfg)["subject"] == first["subject"]  # second is unsealed
    assert api.seal_scope(cfg, second["subject"], proof=proof(second["subject"]))[
        "sealed"
    ]
    got = api.resume(cfg)
    assert got["subject"] == second["subject"]
    assert got["spec"] == second["spec"]


def test_with_no_seal_resume_is_none(cfg):
    assert api.checkin(cfg)["code"] == 0
    assert api.resume(cfg) == {"resume": None, "code": 0}
    api.scope(cfg, by=("who",))  # a scope nobody sealed is not a resume point
    assert api.resume(cfg) == {"resume": None, "code": 0}


def test_a_proposal_seal_is_not_a_scope_seal(cfg):
    assert api.checkin(cfg)["code"] == 0
    run, _ = api._open(cfg, "probe", ())
    hum = gate.human("proposal:abc", proof("proposal:abc"), HUMAN_KEY)
    run.rec.append("seal", hum, where="proposal:abc", subject="proposal:abc")
    assert api.resume(cfg) == {"resume": None, "code": 0}


def test_a_later_proposal_seal_does_not_mask_the_sealed_scope(cfg):
    card = sealed_scope(cfg)
    run, _ = api._open(cfg, "probe", ())
    hum = gate.human("proposal:abc", proof("proposal:abc"), HUMAN_KEY)
    run.rec.append("seal", hum, where="proposal:abc", subject="proposal:abc")
    got = api.resume(cfg)
    assert got["subject"] == card["subject"] and got["spec"] == card["spec"]


def test_a_seal_with_no_scope_row_behind_it_is_none(cfg):
    assert api.checkin(cfg)["code"] == 0
    run, _ = api._open(cfg, "probe", ())
    hum = gate.human("serve:0", proof("serve:0"), HUMAN_KEY)
    run.rec.append("seal", hum, where="serve:0", subject="serve:0")
    assert api.resume(cfg) == {"resume": None, "code": 0}


def test_resume_writes_nothing_but_its_invocation_row(cfg):
    sealed_scope(cfg)
    before = [r["kind"] for r in rows(cfg)]
    api.resume(cfg)
    after = [r["kind"] for r in rows(cfg)]
    assert after == [*before, "invocation"]
    assert not (cfg.box / "served.json").exists()


def test_resume_needs_no_open_box(cfg):
    card = sealed_scope(cfg)
    assert api.checkout(cfg)["code"] == 0
    assert api.resume(cfg)["subject"] == card["subject"]


def test_no_law_is_a_refusal_not_a_none(cfg):
    cfg.constitution.unlink()
    res = api.resume(cfg)
    assert "refused" in res and "resume" not in res and res["code"] != 0


def _cli(cfg, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    return [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests", "--phone",
    ]  # fmt: skip


def test_the_cli_prints_the_spec_and_subject_as_json(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    card = sealed_scope(cfg)
    grow(cfg)
    capsys.readouterr()
    assert cli.main([*base, "resume"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"spec": card["spec"], "subject": card["subject"]}


def test_the_cli_prints_none_when_nothing_is_sealed(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    assert cli.main([*base, "checkin"]) == 0
    capsys.readouterr()
    assert cli.main([*base, "resume"]) == 0
    assert json.loads(capsys.readouterr().out) == {"resume": None}


def test_the_cli_refusal_keeps_stdout_clean_and_exits_nonzero(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    cfg.constitution.unlink()
    assert cli.main([*base, "resume"]) != 0
    cap = capsys.readouterr()
    assert cap.out == "" and "refused:" in cap.err
