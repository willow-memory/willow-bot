"""S6: `api.lore` and `python -m onescript lore SUBJECT` (SPEC R11, R14)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
import test_api as T  # noqa: E402
from test_api import line, proof, rows, served  # noqa: E402

from onescript import __main__ as cli  # noqa: E402
from onescript import api  # noqa: E402

WORLD = "# The Vale\n- The ferryman takes only salt.\n## The Mill\nThe miller counts nine stones.\n"


@pytest.fixture
def cfg(tmp_path):
    return T.cfg.__wrapped__(tmp_path) if hasattr(T.cfg, "__wrapped__") else None


def sealed_world(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line("world/vale.md", WORLD, ids[:1], "the vale")])[
        "out"
    ]["proposals"]
    api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    return p["subject"]


def test_lore_verb_makes_rows_and_prints_the_rescope_step(cfg):
    subject = sealed_world(cfg)
    res = api.lore(cfg, subject)
    assert res["code"] == 0 and res["count"] == 2
    assert [r["where"] for r in res["lore"]] == ["The Vale", "The Mill"]
    assert "serves empty until the human seals it" in res["rescope"]["why"]
    assert any("scope" in s for s in res["rescope"]["steps"])
    assert len([r for r in rows(cfg) if r["kind"] == "lore"]) == 2
    again = api.lore(cfg, subject)
    assert again["count"] == 0 and again["code"] == 0  # idempotent


def test_lore_verb_refuses_an_unsealed_proposal(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line("world/vale.md", WORLD, ids[:1], "the vale")])[
        "out"
    ]["proposals"]
    res = api.lore(cfg, p["subject"])
    assert res["code"] == 1 and "no human seal" in res["refused"]
    assert not [r for r in rows(cfg) if r["kind"] == "lore"]


def test_cli_lore_command(cfg, capsys):
    subject = sealed_world(cfg)
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
            "lore",
            subject,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "lore 2 row(s)" in out and "rescope:" in out
    assert "python3 -m onescript scope" in out


def test_NEW5_rescope_names_the_lore_pile_and_says_nothing_moved_for_zero_rows(cfg):
    subject = sealed_world(cfg)
    first = api.lore(cfg, subject)
    assert "what=lore" in " ".join(first["rescope"]["steps"])
    again = api.lore(cfg, subject)
    assert again["count"] == 0 and again["rescope"]["steps"] == []
    assert "no pile moved" in again["rescope"]["why"]
