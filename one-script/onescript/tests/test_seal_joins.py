"""compiled/seal_joins.py: the operator's joins through the loop, sealed only
with the human's key (read from a file outside the box), else waiting on it."""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path

from onescript import draw
from onescript.tests.test_api import HUMAN_KEY, rows
from onescript.tests.test_seed import box, cfg  # noqa: F401  (the box and its keys)

SCRIPT = Path(__file__).resolve().parents[2] / "compiled" / "seal_joins.py"
spec = importlib.util.spec_from_file_location("seal_joins", SCRIPT)
sj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sj)

DOC = "# A join\n- Both decide.\n- One waits.\n"


def one_join(c, tmp_path: Path) -> dict:
    """A manifest naming two chunks this box really holds."""
    pair = draw.draw(c, "cd" * 32)["pair"]
    folder = tmp_path / "joins"
    folder.mkdir()
    (folder / "draw-1.md").write_text(DOC)
    chosen = [f"{p['repo']}:{p['path']}:{p['line']}" for p in pair]
    (folder / "joins.json").write_text(
        json.dumps([{"doc": "draw-1.md", "chosen": chosen}])
    )
    (join,) = sj.manifest(folder)
    return join


def test_with_the_key_a_join_is_sealed_and_becomes_lore(box, tmp_path):
    c = box["cfg"]
    out = sj.through_the_loop(c, one_join(c, tmp_path), HUMAN_KEY)
    assert out["step"] == "lore" and out["code"] == 0 and out["lore"] == 2
    assert [r["fact"] for r in rows(c) if r["kind"] == "lore"] == [
        "Both decide.",
        "One waits.",
    ]


def test_without_the_key_it_waits_on_the_seal(box, tmp_path):
    c = dataclasses.replace(box["cfg"], seal_at_end=True)  # the CLI's mode
    out = sj.through_the_loop(c, one_join(c, tmp_path), None)
    assert out["step"] == "seal" and out["waiting"].startswith("proposal:")
    assert not [r for r in rows(c) if r["kind"] in ("lore", "seal")]


def test_the_shipped_manifest_names_three_joins_of_two_chunks():
    joins = sj.manifest()
    assert [j["path"] for j in joins] == [f"world/joins/draw-{n}.md" for n in (1, 2, 3)]
    assert all(len(j["chosen"]) == 2 and j["data"].startswith("# Draw") for j in joins)
