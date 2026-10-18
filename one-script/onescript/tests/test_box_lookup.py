"""box_lookup: the Forge's BoxLookup seam over the pile, in all three states."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from onescript import box_lookup as bl  # noqa: E402
from onescript.tests.test_phrase_rank import CACHE_FILES, cached  # noqa: E402


@dataclass(frozen=True)
class Hit:
    """The fields of `forge.majors.Hit` the box reads, without the Forge."""

    source: str
    target: str = "app"
    reason: str = ""
    path: str = ""
    anchor: str = ""


def test_matching_hits_come_back_as_candidates(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    box = bl.PileBox(c, top=3)
    out = box.lookup([Hit("ad"), Hit("breaks")])
    assert out and len(out) <= 3
    assert box.last_state == "populated" and box.name == "willow pile"
    first = out[0]
    assert first.name.startswith("docs:") and first.where.startswith(first.name + ":")
    assert "keywords ad, breaks" in first.why and "score " in first.why
    assert {o.name for o in out} <= {f"docs:{p}" for p in CACHE_FILES}


def test_unmatched_hits_are_empty_and_say_so(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    box = bl.PileBox(c)
    assert box.lookup([Hit("zeppelin")]) == []
    assert box.last_state == "empty"
    assert box.name == "willow pile (empty: nothing ranked for zeppelin)"


def test_no_keyword_with_a_store_is_empty_not_unreachable(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    box = bl.PileBox(c)
    assert box.lookup([]) == []
    assert box.last_state == "empty"


def test_no_store_is_unreachable():
    box = bl.PileBox()
    assert box.lookup([Hit("ad")]) == []
    assert box.last_state == "unreachable"
    assert box.name == "willow pile (unreachable: no store)"
    assert bl.PileBox(stores=[]).lookup([]) == []


def test_a_failed_read_is_unreachable_with_its_reason(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    c.db.close()
    box = bl.PileBox(c)
    assert box.lookup([Hit("ad")]) == []
    assert box.last_state == "unreachable" and "ProgrammingError" in box.last_why


def test_a_callable_ranks_without_a_store():
    seen = []

    def rank(bite):
        seen.append(bite)
        return {
            "state": "ranked",
            "hits": [
                {"repo": "r", "path": "p.md", "line": 4, "score": 1.5, "chunk": "x"}
            ],
        }

    box = bl.PileBox(rank=rank)
    out = box.lookup([Hit("cli"), Hit("cli", target="lib"), Hit("tui")])
    assert seen == ["cli tui"]  # keywords once each, in hit order
    assert out == [bl.Candidate("r:p.md", "r:p.md:4", "keywords cli, tui; score 1.5")]
    assert (
        bl.PileBox(rank=lambda b: {"state": "unreachable", "hits": []}).lookup(
            [Hit("cli")]
        )
        == []
    )


def test_several_stores_merge_by_hash(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, _, a, _ = cached(tmp_path / "a", CACHE_FILES)
    _, _, b, _ = cached(tmp_path / "b", CACHE_FILES)
    box = bl.PileBox({"a": a, "b": b})
    out = box.lookup([Hit("ad"), Hit("breaks")])
    assert out and "seen in 2 store(s)" in out[0].why
    assert box.last_state == "populated"


def test_end_to_end_through_the_forge_entry(tmp_path, monkeypatch):
    entry = pytest.importorskip("forge.entry")
    pytest.importorskip("nestor")
    from forge import majors

    monkeypatch.setenv("FORGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FORGE_MAIN_NESTOR", raising=False)
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    table = [majors.Row(keyword="ad breaks", major="app", reason="a test row")]

    class Never:
        def confirm(self, prompt):
            return True

        def choose(self, decision):
            raise AssertionError("one major: never asked")

    def bite(box):
        return entry.open_bite(
            "count the ad breaks",
            project_id="p",
            builder_id="b" * 32,
            responder=Never(),
            root=tmp_path / "cp",
            box=box,
            table=table,
        )

    box = bl.PileBox(c, top=2)
    assert isinstance(box, entry.BoxLookup)
    e = bite(box)
    assert e.tiers["box"] == "willow pile: 2 candidate(s)", e.tiers
    assert e.candidates[0].where.startswith("docs:")
    e = bite(bl.PileBox())
    assert e.tiers["box"] == "willow pile (unreachable: no store): nothing"
    print("\n".join(f"{k:7} {v}" for k, v in e.tiers.items()))
