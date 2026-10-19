"""Jeles as the pile's escalation rung: asked at seed only when the pile ranks
nothing (or there is no pile), proposed by scope, sealed by the human with the
rest of the stack, and served only while it still hashes to what was sealed."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api  # noqa: E402
from onescript import jeles_store as js  # noqa: E402
from onescript.tests.test_api import cfg, proof, rows  # noqa: E402,F401
from onescript.tests.test_store import BITE, box  # noqa: E402,F401

LIGHT = {
    "_id": "s1",
    "question": "What is a light-year?",
    "answer": "A light-year is the distance light travels in one year.",
    "sources": ["https://science.nasa.gov/"],
    "verified_by": "public record: NASA",
    "verification_kind": "human",
    "visibility": "serve",
}
ARGUMENT = {
    "_id": "s2",
    "question": "Is a light-year a silly unit?",
    "answer": "An opinion about units.",
    "verified_by": "research-agent",
    "verification_kind": "asserted",
    "visibility": "internal",
}
HIDDEN = {
    **LIGHT,
    "_id": "s3",
    "question": "A light-year kept internal",
    "visibility": "internal",
}
JELES_BITE = "what is a light-year"


class FakeJeles:
    """Jeles's corpus as two stand-ins: word-overlap search, and get by id."""

    def __init__(self, *nuggets):
        self.db = {n["_id"]: dict(n) for n in nuggets}
        self.searched: list[str] = []

    def search(self, bite, limit):
        self.searched.append(bite)
        ws = set(bite.lower().replace("?", "").split())
        scored = [
            (len(ws & set(n["question"].lower().replace("?", "").split())), n)
            for n in self.db.values()
        ]
        return [n for s, n in sorted(scored, key=lambda x: -x[0]) if s][:limit]

    def get(self, nid):
        return dict(self.db[nid]) if nid in self.db else {"error": "not_found"}

    def store(self):
        return js.JelesStore(self.search, self.get)


# ── the store on its own ────────────────────────────────────────────────────
def test_only_what_jeles_lets_answer_is_ranked():
    out = FakeJeles(LIGHT, ARGUMENT, HIDDEN).store().rank(JELES_BITE)
    assert out["state"] == js.RANKED
    assert [h["path"] for h in out["hits"]] == ["ask_jeles_corpus/s1"]
    h = out["hits"][0]
    assert h["repo"] == "jeles" and h["line"] == 1 and len(h["chunk"]) == 64


def test_nothing_servable_is_empty_and_a_failed_read_is_unreachable():
    assert FakeJeles(ARGUMENT).store().rank(JELES_BITE)["state"] == js.EMPTY

    def broken(bite, limit):
        raise OSError("store locked")

    out = js.JelesStore(broken, lambda i: {}).rank(JELES_BITE)
    assert out["state"] == js.UNREACHABLE and "store locked" in out["why"]


def test_a_chunk_reads_back_only_while_it_hashes_and_still_answers():
    j = FakeJeles(LIGHT)
    st = j.store()
    hit = st.rank(JELES_BITE)["hits"][0]
    chunk = {
        "repo": hit["repo"],
        "path": hit["path"],
        "line": 1,
        "sha256": hit["chunk"],
    }
    text, why = st.read_chunk(chunk)
    assert why is None and "distance light travels" in text
    assert "verified: public record: NASA" in text
    j.db["s1"]["answer"] = "edited after the seal"
    assert st.read_chunk(chunk) == (None, "changed in Jeles since it was sealed")
    j.db["s1"] = {**LIGHT, "verification_kind": "asserted"}
    assert st.read_chunk(chunk)[1].startswith("not settled in Jeles")
    del j.db["s1"]
    assert st.read_chunk(chunk) == (None, "no longer in Jeles")


def test_the_chunk_reader_sends_each_chunk_to_its_own_store():
    st = FakeJeles(LIGHT).store()
    pile_calls = []

    def pile(ch):
        pile_calls.append(ch)
        return "pile text", None

    read = js.chunk_reader(st, pile)
    assert read({"repo": "docs", "path": "a.md"}) == ("pile text", None)
    assert (
        read({"repo": "jeles", "path": "ask_jeles_corpus/zz"})[1]
        == "no longer in Jeles"
    )
    assert len(pile_calls) == 1
    assert js.chunk_reader(None, None)({"repo": "jeles"})[1].startswith("Jeles isn't")
    assert js.chunk_reader(st, None)({"repo": "docs"})[1].startswith("no xref index")


# ── escalation only, through the API ────────────────────────────────────────
def with_jeles(box, *nuggets):  # noqa: F811
    j = FakeJeles(*nuggets)
    return j, replace(box["cfg"], jeles=j.store())


def test_jeles_is_not_asked_when_the_pile_ranks_the_bite(box):  # noqa: F811
    j, c = with_jeles(box, LIGHT)
    res = api.seed(c, BITE)
    assert res["rank"] and all(h["repo"] != "jeles" for h in res["rank"])
    assert res["jeles"]["why"] == "the pile holds the bite's words"
    assert res["jeles"]["state"] == "not_asked"
    assert j.searched == []


def test_without_jeles_the_seed_row_is_as_before(box):  # noqa: F811
    res = api.seed(box["cfg"], JELES_BITE)
    assert "jeles" not in res
    seed_row = [r for r in rows(box["cfg"]) if r["kind"] == "seed"][-1]
    assert "jeles" not in seed_row


def serve_jeles(c, top=1, seal=True):
    card = api.scope(c, by=(), seed_top=top)
    assert card["code"] == 0, card
    if seal:
        assert api.seal_scope(c, card["subject"], proof=proof(card["subject"]))[
            "sealed"
        ]
    return card, api.serve(c, card["spec"], max_chars=10**6)


def test_a_bite_the_pile_cannot_rank_escalates_to_jeles_and_is_served(box):  # noqa: F811
    j, c = with_jeles(box, LIGHT, ARGUMENT)
    res = api.seed(c, JELES_BITE)
    assert res["jeles"]["state"] == js.RANKED
    assert res["jeles"]["asked"] == "the pile is missing 3 of the bite's 5 words"
    assert res["rank"][0]["repo"] == "jeles"  # Jeles leads; the pile's noise follows
    assert {h["repo"] for h in res["rank"][1:]} == {"docs"}
    seed_row = [r for r in rows(c) if r["kind"] == "seed"][-1]
    assert seed_row["rank"][0]["path"] == "ask_jeles_corpus/s1"
    card, out = serve_jeles(c)
    assert out["state"] == "populated"
    t = out["doc"]["tables"][0]
    assert "distance light travels" in t["rows"][0]["text"]
    assert t["trust"] == "untrusted"  # like any chunk: Jeles's own word is in the text
    assert "opinion" not in json.dumps(out["doc"])  # asserted research never reaches it


def test_an_unsealed_jeles_scope_serves_nothing(box):  # noqa: F811
    _, c = with_jeles(box, LIGHT)
    api.seed(c, JELES_BITE)
    _, out = serve_jeles(c, seal=False)
    assert out["state"] == "empty"


def test_a_jeles_card_edited_after_the_seal_is_not_served(box):  # noqa: F811
    j, c = with_jeles(box, LIGHT)
    api.seed(c, JELES_BITE)
    card = api.scope(c, by=(), seed_top=1)
    api.seal_scope(c, card["subject"], proof=proof(card["subject"]))
    j.db["s1"]["answer"] = "A light-year is a measure of time."
    out = api.serve(c, card["spec"], max_chars=10**6)
    assert out["state"] == "unreachable"
    assert "changed in Jeles since it was sealed" in out["doc"]["tables"][0]["why"]
    assert not [r for r in rows(c) if r["kind"] == "tombstone"]  # Jeles's, not a file


def test_with_no_pile_at_all_jeles_still_answers(cfg):  # noqa: F811
    j = FakeJeles(LIGHT)
    c = replace(cfg, jeles=j.store())
    assert api.checkin(c)["code"] == 0
    res = api.seed(c, JELES_BITE)
    assert res["jeles"]["asked"] == "no pile to ask"
    _, out = serve_jeles(c)
    assert out["state"] == "populated"
    assert "distance light travels" in out["doc"]["tables"][0]["rows"][0]["text"]


@pytest.mark.parametrize("state", ["unreachable", "empty"])
def test_jeles_that_cannot_answer_leaves_the_seed_as_the_pile_left_it(box, state):  # noqa: F811
    if state == "empty":
        _, c = with_jeles(box, ARGUMENT)
    else:

        def broken(bite, limit):
            raise OSError("down")

        c = replace(box["cfg"], jeles=js.JelesStore(broken, lambda i: {}))
    res = api.seed(c, JELES_BITE)
    assert res["jeles"]["state"] == state
    assert all(h["repo"] != "jeles" for h in res.get("rank") or [])
