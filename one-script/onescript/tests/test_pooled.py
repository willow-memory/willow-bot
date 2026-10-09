"""pooled: the unsealed pass set the close-out deposit hands to Nestor.

Read-only. Every proposal on record that passed and that no human seal covers,
one per subject; a refused, a link_fail, a sealed pass and a forged row never.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate, proposals  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
CAP = 10**6
FIELDS = {"subject", "path", "data", "cites", "claim"}


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


def line(path="notes/a.md", data="hello\n", cites=("x",), claim="a note") -> str:
    return json.dumps(
        {"path": path, "data": data, "cites": list(cites), "claim": claim}
    )


def served(cfg) -> list[str]:
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "populated", res
    return [t["id"] for t in res["doc"]["tables"]]


def propose(cfg, *lines) -> list[dict]:
    return api.take_proposals(cfg, list(lines))["out"]["proposals"]


def subjects(cfg) -> list[str]:
    return [p["subject"] for p in api.pooled(cfg)["pooled"]]


def test_a_pass_accumulates_across_two_turns_and_is_pooled(cfg):
    ids = served(cfg)
    (a,) = propose(cfg, line(cites=ids[:1]))
    assert subjects(cfg) == [a["subject"]]
    (b,) = propose(cfg, line("notes/b.md", "other", ids[:1], "another"))
    res = api.pooled(cfg)
    assert res["code"] == 0 and res["count"] == 2
    assert subjects(cfg) == [a["subject"], b["subject"]]
    first = res["pooled"][0]
    assert set(first) == FIELDS
    assert first["path"] == "notes/a.md" and first["data"] == "hello\n"
    assert first["cites"] == ids[:1] and first["claim"] == "a note"
    assert first["subject"] == proposals.subject({k: first[k] for k in proposals.KEYS})


def test_a_proposal_taken_twice_is_pooled_once(cfg):
    ids = served(cfg)
    propose(cfg, line(cites=ids[:1]))
    propose(cfg, line(cites=ids[:1]))
    assert len(subjects(cfg)) == 1


def test_a_sealed_pass_drops_out_of_the_pool(cfg):
    ids = served(cfg)
    a, b = propose(cfg, line(cites=ids[:1]), line("notes/b.md", "other", ids[:1], "b"))
    assert api.seal_proposal(cfg, a["subject"], proof=proof(a["subject"]))["sealed"]
    assert subjects(cfg) == [b["subject"]]


def test_a_refused_and_a_link_fail_are_never_pooled(cfg):
    ids = served(cfg)
    got = api.take_proposals(
        cfg,
        [
            line(cites=ids[:1]),
            line("notes/lost.md", "no", ["0" * 64], "lost"),
            "not json at all",
        ],
    )["out"]["proposals"]
    assert [p["verdict"] for p in got] == ["pass", "link_fail", "refused"]
    assert subjects(cfg) == [got[0]["subject"]]


def test_a_forged_row_is_never_pooled(cfg):
    ids = served(cfg)
    (real,) = propose(cfg, line(cites=ids[:1]))
    run, _ = api._open(cfg, "probe", ())
    claimed = proposals.subject(
        {"path": "notes/f.md", "data": "what the human saw", "cites": ids[:1],
         "claim": "f"}
    )  # fmt: skip
    # a pass row that names one subject and carries other bytes
    run.rec.append(
        "proposal", run.sys, subject=claimed, verdict="pass", path="notes/f.md",
        data="other bytes", cites=ids[:1], claim="f",
    )  # fmt: skip
    assert subjects(cfg) == [real["subject"]]


def test_pooling_changes_nothing_but_the_invocation_row(cfg):
    ids = served(cfg)
    propose(cfg, line(cites=ids[:1]))
    path = cfg.box / "record.jsonl"
    before = [json.loads(x)["kind"] for x in path.read_text().splitlines()]
    api.pooled(cfg)
    after = [json.loads(x)["kind"] for x in path.read_text().splitlines()]
    assert after == [*before, "invocation"]
    assert not (cfg.box / "notes").exists()


def test_the_pool_is_readable_after_check_out(cfg):
    ids = served(cfg)
    (a,) = propose(cfg, line(cites=ids[:1]))
    assert api.checkout(cfg)["code"] == 0
    assert subjects(cfg) == [a["subject"]]


def test_no_law_is_a_refusal_not_an_empty_pool(cfg):
    cfg.constitution.unlink()
    res = api.pooled(cfg)
    assert "refused" in res and "pooled" not in res and res["code"] != 0


def _cli(cfg, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    return [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests", "--phone",
    ]  # fmt: skip


def test_the_cli_prints_one_json_object_per_line(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    ids = served(cfg)
    a, b = propose(cfg, line(cites=ids[:1]), line("notes/b.md", "other", ids[:1], "b"))
    capsys.readouterr()
    assert cli.main([*base, "pooled"]) == 0
    out = capsys.readouterr().out
    objs = [json.loads(x) for x in out.splitlines()]
    assert [o["subject"] for o in objs] == [a["subject"], b["subject"]]
    assert all(set(o) == FIELDS for o in objs)


def test_the_cli_prints_nothing_for_an_empty_pool(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    assert cli.main([*base, "checkin"]) == 0
    capsys.readouterr()
    assert cli.main([*base, "pooled"]) == 0
    assert capsys.readouterr().out == ""


def test_the_cli_refusal_keeps_stdout_clean_and_exits_nonzero(cfg, capsys, monkeypatch):
    base = _cli(cfg, monkeypatch)
    cfg.constitution.unlink()
    assert cli.main([*base, "pooled"]) != 0
    cap = capsys.readouterr()
    assert cap.out == "" and "refused:" in cap.err
