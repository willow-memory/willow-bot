"""needs: the morning screen's NEEDS YOU lines, read without checking out.

Read-only. The lines are `view.needs_you`, the very ones `checkout` prints; the
verb writes its `invocation` row and nothing else (no `reconcile` row), so a seat
can answer "what is on the table" mid-run without ending the run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate, view  # noqa: E402

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
        now="2026-10-08T00:00:00Z",
        no_tests=True,
        phone=True,
    )


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def rows(cfg) -> list[dict]:
    text = (cfg.box / "record.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def line(path="notes/a.md", cites=("x",)) -> str:
    return json.dumps(
        {"path": path, "data": "hello\n", "cites": list(cites), "claim": "a note"}
    )


def served(cfg) -> list[str]:
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    return [t["id"] for t in res["doc"]["tables"]]


def test_needs_lists_what_the_morning_screen_lists(cfg):
    ids = served(cfg)
    api.take_proposals(cfg, [line(cites=ids[:1]), line("notes/lost.md", ["0" * 64])])
    got = api.needs(cfg)
    assert got["code"] == 0 and got["count"] == len(got["needs"]) >= 2
    assert any(
        x.startswith("proposal · own_idea: no · notes/a.md · pass")
        for x in got["needs"]
    )
    assert any("notes/lost.md · link_fail" in x for x in got["needs"])
    screen = api.checkout(cfg)["screen"]
    section = screen.split("NEEDS YOU\n", 1)[1].split("DISAGREEMENTS")[0]
    assert section.splitlines() == [f"  · {x}" for x in got["needs"]]


def test_needs_with_nothing_waiting_is_an_empty_list_not_a_refusal(cfg):
    assert api.checkin(cfg)["code"] == 0
    assert api.checkout(cfg)["code"] == 0
    assert api.checkin(cfg)["code"] == 0
    assert api.needs(cfg) == {"needs": [], "count": 0, "code": 0}


def test_needs_writes_nothing_but_its_invocation_row_and_does_not_check_out(cfg):
    ids = served(cfg)
    api.take_proposals(cfg, [line(cites=ids[:1])])
    before = [r["kind"] for r in rows(cfg)]
    api.needs(cfg)
    api.needs(cfg)
    assert [r["kind"] for r in rows(cfg)] == [*before, "invocation", "invocation"]
    assert not [r for r in rows(cfg) if r["kind"] == "reconcile"]
    assert api.turn(cfg, "still open")["code"] == 0  # the run was not checked out


def test_needs_needs_no_open_box(cfg):
    assert api.checkin(cfg)["code"] == 0
    assert api.checkout(cfg)["code"] == 0
    got = api.needs(cfg)
    assert got["code"] == 0 and "needs" in got


def test_no_law_is_a_refusal_not_an_empty_list(cfg):
    cfg.constitution.unlink()
    res = api.needs(cfg)
    assert "refused" in res and "needs" not in res and res["code"] != 0


def test_morning_still_draws_the_same_lines_from_needs_you():
    rep = {
        "open_turns": [],
        "pile": {"failing": [], "counts": {}},
        "awaiting": [],
        "proposals": [{"path": "p.md", "verdict": "pass", "own_idea": False}],
        "offers": [],
        "surfaced": [{"row": 9, "kind": "write", "why": "moved"}],
        "backpressure": {},
        "witness": {"split": [], "agreed": []},
        "predictions": [],
        "grades": {},
        "chain_rows": 3,
        "version": "v",
    }
    lines = view.needs_you(rep)
    assert lines == [
        "proposal · own_idea: no · p.md · pass",
        "re-check row 9 (write) · moved",
    ]
    assert "NEEDS YOU\n  · " + "\n  · ".join(lines) + "\nDISAGREEMENTS" in view.morning(
        rep
    )


def _cli(cfg, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    return [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests", "--phone",
    ]  # fmt: skip


def test_the_cli_prints_the_lines_as_json_and_refusals_to_stderr(
    cfg, capsys, monkeypatch
):
    base = _cli(cfg, monkeypatch)
    ids = served(cfg)
    api.take_proposals(cfg, [line(cites=ids[:1])])
    capsys.readouterr()
    assert cli.main([*base, "needs"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == len(out["needs"]) and "code" not in out
    cfg.constitution.unlink()
    assert cli.main([*base, "needs"]) != 0
    cap = capsys.readouterr()
    assert cap.out == "" and "refused:" in cap.err
