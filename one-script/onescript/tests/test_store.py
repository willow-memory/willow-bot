"""The session's store: attached at check-in, seeding the first bite, its
chunks proposed by scope, sealed by the human, and served from the blob."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, pile  # noqa: E402
from onescript.tests.test_api import cfg, proof, rows  # noqa: E402,F401
from onescript.tests.test_seed import index_into_box  # noqa: E402
from onescript.tests.test_xref import make, sh  # noqa: E402

DOCS = {
    "ads.md": (
        "Nobody counts the ad breaks in kids games, not even once today.\n\n"
        "Four minutes of ads for thirty seconds of play is the trade here.\n"
    ),
    "other.md": "A note about gardens and soil, with nothing about games in it.\n",
    "more.md": "The ad breaks again, said plainly in a body line of this file.\n",
}
BITE = "Nobody counts the ad breaks in kids games, not even once today."


@pytest.fixture
def box(cfg, tmp_path):  # noqa: F811
    repo = make(tmp_path, "docs", DOCS)
    index_into_box(cfg, repo)
    assert api.checkin(cfg)["code"] == 0
    return {"cfg": cfg, "repo": repo}


def kinds(cfg) -> list[str]:  # noqa: F811
    return [r["kind"] for r in rows(cfg)]


def test_check_in_attaches_the_session_store(box):
    st = [r for r in rows(box["cfg"]) if r["kind"] == "store"][-1]
    assert st["state"] == "attached" and st["how"] == "built"
    path = pile.store_path(box["cfg"].box)
    assert st["store_sha256"] == pile.file_sha256(path)
    assert not path.is_relative_to(box["cfg"].box)  # beside the box, not in it
    assert api.checkin(box["cfg"])["report"]["store"]["how"] == "warm"


def test_no_index_is_an_unreachable_store_and_the_box_still_opens(cfg):  # noqa: F811
    res = api.checkin(cfg)
    assert res["code"] == 0 and res["report"]["store"]["state"] == "unreachable"


def test_the_first_bite_seeds_from_the_store(box):
    res = api.seed(box["cfg"], BITE)
    assert res["store"] == {"state": "attached", "why": ""}
    assert (res["rank"][0]["path"], res["rank"][0]["line"]) == ("ads.md", 1)
    assert res["quotes"][0]["path"] == "ads.md"
    assert all(len(h["chunk"]) == 64 for h in res["rank"])


def test_a_store_that_changed_since_check_in_is_not_used(box):
    path = pile.store_path(box["cfg"].box)
    with open(path, "ab") as f:
        f.write(b"\0")
    res = api.seed(box["cfg"], BITE)
    assert res["store"]["state"] == "unreachable" and "rank" not in res
    assert "doesn't hash to its row" in res["store"]["why"]


def served_chunks(box, top=2, seal=True, max_chars=10**6):
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    card = api.scope(cfg, by=(), seed_top=top)
    assert card["code"] == 0 and len(card["ids"]) == top
    if seal:
        assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))[
            "sealed"
        ]
    return card, api.serve(cfg, card["spec"], max_chars=max_chars)


def test_a_sealed_chunk_is_served_from_its_blob(box):
    card, res = served_chunks(box)
    assert res["state"] == "populated"
    t = res["doc"]["tables"]
    texts = [x["rows"][0]["text"] for x in t]
    assert DOCS["ads.md"].split("\n\n")[0].strip() in texts
    assert all(x["trust"] == "untrusted" for x in t)
    assert {x["id"] for x in t}.isdisjoint(card["ids"])  # keyed ids, not content hashes
    assert all(c not in json.dumps(res["doc"]) for c in card["ids"])


def test_an_unsealed_chunk_scope_serves_nothing(box):
    _, res = served_chunks(box, seal=False)
    assert res["state"] == "empty" and res["doc"]["tables"] == []


def test_the_indexed_bytes_are_served_not_the_working_tree(box):
    (box["repo"] / "ads.md").write_text("rewritten after the index\n" * 3)
    sh(box["repo"], "commit", "-qam", "two")
    _, res = served_chunks(box)
    assert "rewritten" not in json.dumps(res["doc"])


def test_a_chunk_whose_checkout_is_gone_is_unreachable(box, tmp_path):
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    card = api.scope(cfg, by=(), seed_top=1)
    api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))
    box["repo"].rename(tmp_path / "moved")
    res = api.serve(cfg, card["spec"], max_chars=10**6)
    assert res["state"] == "unreachable"
    assert "no checkout" in res["doc"]["tables"][0]["why"]
    moved = replace(cfg, repos={"docs": tmp_path / "moved"})
    assert api.serve(moved, card["spec"], max_chars=10**6)["state"] == "populated"


def test_scope_without_a_seed_is_refused(box):
    res = api.scope(box["cfg"], by=(), seed_top=2)
    assert res["code"] == 1 and "no seed" in res["refused"]


def test_a_scope_naming_nothing_is_refused(box):
    api.seed(box["cfg"], BITE)
    assert api.scope(box["cfg"], by=())["code"] == 1


def test_chunks_sit_beside_record_piles_in_one_stack(box):
    api.seed(box["cfg"], BITE)
    card = api.scope(box["cfg"], by=("who",), seed_top=1)
    kinds_ = {s.get("kind", "pile") for s in card["stack"]}
    assert kinds_ == {"pile", "chunk"}


def test_a_chunk_whose_blob_does_not_hash_to_the_index_is_not_read(tmp_path):
    from onescript import xref

    repo = make(tmp_path, "docs", DOCS)
    idx = xref.build([("docs", repo)])
    text = DOCS["more.md"].strip()
    ch = {
        "repo": "docs",
        "path": "more.md",
        "line": 1,
        "sha256": pile.hashlib.sha256(text.encode()).hexdigest(),
    }
    assert pile.read_chunk(idx, {"docs": repo}, ch) == (text, None)
    for f in idx["arrival"]["docs"]["files"]:
        f["sha256"] = "0" * 64
    assert pile.read_chunk(idx, {"docs": repo}, ch) == (
        None,
        "bytes don't hash to the index",
    )
    assert pile.read_chunk(idx, {"docs": repo}, {**ch, "line": 2})[0] is None


SHARED = "A shared note on ad breaks in kids games, kept the same in both.\n"


@pytest.fixture
def two(cfg, tmp_path):  # noqa: F811
    """Two sessions, each its own box and store; one file in common."""
    a = make(tmp_path, "aaa", {"shared.md": SHARED, "a.md": DOCS["ads.md"]})
    b = make(tmp_path, "bbb", {"shared.md": SHARED, "b.md": DOCS["more.md"]})
    cfg_b = replace(cfg, box=tmp_path / "box-b")
    index_into_box(cfg, a)
    index_into_box(cfg_b, b)
    assert api.checkin(cfg)["code"] == 0 and api.checkin(cfg_b)["code"] == 0
    return cfg, cfg_b


def test_a_lookup_merges_sessions_by_chunk_hash(two):
    cfg_a, cfg_b = two
    before = (cfg_b.box / "record.jsonl").read_bytes()
    res = api.lookup(cfg_a, "ad breaks in kids games", [cfg_b.box])
    assert res["code"] == 0
    assert set(res["sessions"]) == {"this", str(cfg_b.box)}
    shared = [h for h in res["hits"] if h["seen_in"] == 2]
    assert len(shared) == 1 and shared[0]["where"][0][1] == "shared.md"
    assert {h["where"][0][0] for h in res["hits"]} == {"aaa", "bbb"}
    assert (cfg_b.box / "record.jsonl").read_bytes() == before  # read, never written
    assert rows(cfg_a)[-1]["kind"] == "lookup"


def test_another_store_that_changed_is_said_and_the_rest_still_answer(two):
    cfg_a, cfg_b = two
    with open(pile.store_path(cfg_b.box), "ab") as f:
        f.write(b"\0")
    res = api.lookup(cfg_a, "ad breaks in kids games", [cfg_b.box])
    assert res["sessions"][str(cfg_b.box)]["state"] == "unreachable"
    assert res["hits"] and all(h["sessions"] == ["this"] for h in res["hits"])


def test_a_box_that_is_not_there_is_said_and_not_created(two, tmp_path):
    ghost = tmp_path / "no-such-box"
    res = api.lookup(two[0], "ad breaks", [ghost])
    assert res["sessions"][str(ghost)] == {
        "state": "unreachable",
        "why": "no record in that box",
    }
    assert not ghost.exists()


def test_merge_is_the_same_view_every_time(two):
    a = api.lookup(two[0], "ad breaks in kids games", [two[1].box])
    b = api.lookup(two[0], "ad breaks in kids games", [two[1].box])
    assert a["hits"] == b["hits"] and a["quotes"] == b["quotes"]


def test_the_seed_ranks_the_repaired_bite_and_quotes_your_own_words(box):
    res = api.seed(box["cfg"], "Nobody counts the add braeks in kids games")
    assert res["repair"]["state"] == "repaired"
    assert ["braeks", "breaks", 2] in res["repair"]["repairs"]
    assert res["rank"][0]["path"] == "ads.md"
    assert res["quotes"] == []  # your words, exact: nobody said this one
