"""plant.py (IDEAS 121): the seed's person-facing half. The canon walked once
per box, its hashes on the record and never its words; consent asked each
session, and a planted box serving no repo the session didn't consent to;
the handoff letter written through the record and named on NEEDS YOU."""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses

from onescript import api, draw, plant, view
from onescript.states import POPULATED, UNREACHABLE
from onescript.tests.test_api import CAP, proof, rows
from onescript.tests.test_draw import R
from onescript.tests.test_seed import box, cfg  # noqa: F401

COVENANT = "# 0 · The Covenant\n\nWhat a Willow is.\n"
OTHER = "# 1 · Be the Other\n\nNot the mirror.\n"


def canon(tmp_path, monkeypatch):
    home = tmp_path / "willow-home"
    d = home / "seed" / "canon"
    d.mkdir(parents=True)
    (d / "00-the-covenant.md").write_text(COVENANT)
    (d / "01-be-the-other.md").write_text(OTHER)
    (d / "README.md").write_text("not a movement")
    monkeypatch.setenv("WILLOW_HOME", str(home))


def test_plant_walks_the_canon_once_and_keeps_only_its_hashes(
    box, tmp_path, monkeypatch
):
    canon(tmp_path, monkeypatch)
    got = plant.plant(box["cfg"], ["docs"])
    assert got["code"] == 0 and got["state"] == POPULATED
    assert [m["title"] for m in got["movements"]] == [
        "0 · The Covenant",
        "1 · Be the Other",
    ]
    (row,) = [r for r in rows(box["cfg"]) if r["kind"] == plant.PLANT]
    assert [c["n"] for c in row["canon"]] == [1, 2] and "Covenant" not in str(row)
    assert got["consent"]["streams"] == ["docs"]
    again = plant.plant(box["cfg"], ["docs"])
    assert "planted at row" in again["refused"]


def test_no_canon_plants_nothing_and_says_where_it_looked(box, tmp_path, monkeypatch):
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    c = dataclasses.replace(box["cfg"], grove=tmp_path / "no-grove")
    got = plant.plant(c, ["docs"])
    assert got["state"] == UNREACHABLE and "no-grove" in got["why"]
    assert not [r for r in rows(c) if r["kind"] == plant.PLANT]


def served(c):
    draw.draw(c, R)
    card = api.scope(c, by=(), draw=True)
    assert api.seal_scope(c, card["subject"], proof=proof(card["subject"]))["sealed"]
    return api.serve(c, card["spec"], max_chars=CAP)


def test_a_planted_box_serves_only_what_this_session_consented_to(
    box, tmp_path, monkeypatch
):
    c = box["cfg"]
    assert served(c)["state"] == POPULATED  # not planted: served as before
    canon(tmp_path, monkeypatch)
    plant.plant(c, [])  # planted, consenting to nothing
    assert "given no consent" in served(c)["refused"]
    plant.consent(c, ["docs"])
    assert "no consent to read bot" in served(c)["refused"]
    plant.consent(c, ["docs", "bot"])
    assert served(c)["state"] == POPULATED
    assert "no repo named elsewhere" in plant.consent(c, ["elsewhere"])["refused"]


def test_consent_expires_with_the_session():
    boot, plant_row = {"kind": "boot"}, {"kind": plant.PLANT, "n": 3}
    given = {"kind": plant.CONSENT, "streams": ["docs"]}
    assert plant.allowed_repos([boot]) is None  # never planted: no consent asked
    assert plant.allowed_repos([boot, plant_row, given]) == {"docs"}
    assert plant.allowed_repos([boot, plant_row, given, boot]) == frozenset()


def test_the_letter_is_written_through_the_record_and_named_next(box):
    c = box["cfg"]
    got = plant.letter(c, "# Handoff\n\nThe oauth wiring waits on G1.\n")
    assert got["code"] == 0 and got["path"].startswith("letters/")
    text = (c.box / got["path"]).read_text()
    assert text.startswith("---\nkind: handoff\n") and "waits on G1" in text
    (row,) = [r for r in rows(c) if r["n"] == got["row"]]
    assert row["where"] == got["path"] and "G1" not in str(row)
    second = plant.letter(c, "# Next\n")
    assert got["path"].endswith("a.md") and second["path"].endswith("b.md")
    assert plant.latest_letter(c.box) == second["path"]
    assert "a letter with nothing" in plant.letter(c, "  ")["refused"]
    lines = view.needs_you(
        {"open_turns": [], "pile": {"failing": []}, "awaiting": [], "offers": [],
         "surfaced": [], "consent_missing": True, "letter": second["path"]}
    )  # fmt: skip
    assert any(ln.startswith("consent · ") for ln in lines)
    assert f"letter · the last session's handoff · {second['path']}" in lines
