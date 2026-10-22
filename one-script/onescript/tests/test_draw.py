"""The serendipity loop (draw.py; IDEAS.md 7): draw, scope, serve, the join
as a proposal, the human's seal, lore.

The operator, 2026-10-22: "build the loop, then run my joins through it."
Each step is the one script's own; the draw row is the only new piece, and it
is a chunk receipt exactly as a seed row is. Every forbidden act is tried: a
pick with no randomness, a join citing what wasn't served, lore from an
unsealed join, a draw from an idea file.
"""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, draw, serve  # noqa: E402
from onescript.states import EMPTY, POPULATED, UNREACHABLE  # noqa: E402
from onescript.tests.test_api import CAP, line, proof, rows  # noqa: E402
from onescript.tests.test_seed import box, cfg, index_into_box  # noqa: E402,F401
from onescript.tests.test_xref import make  # noqa: E402

R = "ab" * 32
JOIN = (
    "# What joins these\n"
    "- Both are a room where many ask and one decides.\n"
    "- The seal is the veto.\n"
)


def served_pair(c) -> tuple[dict, list[dict]]:
    """Scope the newest draw, seal the scope as the human, serve it."""
    card = api.scope(c, by=(), draw=True)
    assert card["code"] == 0 and len(card["ids"]) == 2
    assert api.seal_scope(c, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(c, card["spec"], max_chars=CAP)
    assert res["state"] == POPULATED, res
    return card, res["doc"]["tables"]


def test_a_draw_picks_one_chunk_from_each_of_two_repos(box):
    got = draw.draw(box["cfg"], R)
    assert got["state"] == POPULATED and got["source"] == {"given": True}
    assert {p["repo"] for p in got["pair"]} == {"docs", "bot"}
    (row,) = [r for r in rows(box["cfg"]) if r["kind"] == draw.KIND]
    assert row["rank"] == got["pair"] and row["question"] == draw.QUESTION
    assert draw.draw(box["cfg"], R)["pair"] == got["pair"]  # same randomness, same pair


def test_the_drawn_pair_is_served_like_any_chunk(box):
    draw.draw(box["cfg"], R)
    _, tables = served_pair(box["cfg"])
    texts = [t["rows"][0]["text"] for t in tables]
    assert len(texts) == 2 and all(texts)


def test_a_join_goes_through_the_door_the_seal_and_into_lore(box):
    c = box["cfg"]
    draw.draw(c, R)
    _, tables = served_pair(c)
    ids = [t["id"] for t in tables]
    (p,) = api.take_proposals(
        c, [line("world/joins/draw-1.md", JOIN, ids, "what joins these")]
    )["out"]["proposals"]
    assert p["verdict"] == "pass"  # awaiting the human
    assert "no human seal" in api.lore(c, p["subject"])["refused"]
    assert api.seal_proposal(c, p["subject"], proof=proof(p["subject"]))["code"] == 0
    res = api.lore(c, p["subject"])
    assert res["code"] == 0 and res["count"] == 2
    facts = [r["fact"] for r in rows(c) if r["kind"] == "lore"]
    assert facts == [
        "Both are a room where many ask and one decides.",
        "The seal is the veto.",
    ]


def test_a_join_citing_what_was_not_served_is_a_link_fail(box):
    c = box["cfg"]
    draw.draw(c, R)
    served_pair(c)
    (p,) = api.take_proposals(c, [line("world/joins/x.md", JOIN, ["nope"], "a join")])[
        "out"
    ]["proposals"]
    assert p["verdict"] == "link_fail"


def test_no_randomness_and_no_beacon_is_unreachable(box):
    got = draw.draw(box["cfg"])
    assert got["state"] == UNREACHABLE and "--drand" in got["why"]
    assert not [r for r in rows(box["cfg"]) if r["kind"] == draw.KIND]


def test_a_beacon_round_is_the_randomness_and_is_on_the_row(box):
    def beacon(rnd):
        return {"round": 4242} if rnd == "latest" else {"round": rnd, "randomness": R}

    got = draw.draw(dataclasses.replace(box["cfg"], beacon=beacon))
    assert got["source"] == {"drand": 4242}
    assert got["pair"] == draw.draw(box["cfg"], R)["pair"]


def test_bad_randomness_is_refused_said(box):
    assert draw.draw(box["cfg"], "not hex")["state"] == UNREACHABLE


def test_an_idea_file_is_never_drawn(cfg, tmp_path):
    a = make(tmp_path, "docs", {"IDEAS.md": "an idea about the pile\n" * 30})
    b = make(tmp_path, "bot", {"run.py": "print('forge')\n" * 30})
    index_into_box(cfg, a, b)
    assert api.checkin(cfg)["code"] == 0
    got = draw.draw(cfg, R)
    assert got["state"] == EMPTY  # docs holds only an idea: one repo left


def test_scope_needs_a_draw_and_takes_one_source(box):
    c = box["cfg"]
    assert "no draw" in api.scope(c, by=(), draw=True)["refused"]
    draw.draw(c, R)
    assert "not both" in api.scope(c, by=(), draw=True, seed_top=3)["refused"]


def test_a_receipt_is_a_seed_or_a_draw_row():
    assert serve.RECEIPTS == ("seed", "draw")


def test_two_chosen_chunks_are_drawn_as_chosen(box):
    c = box["cfg"]
    picked = draw.draw(c, R)["pair"]
    named = [{k: p[k] for k in ("repo", "path", "line")} for p in reversed(picked)]
    got = draw.draw(c, chosen=named)
    assert got["source"] == {"chosen": True}
    assert got["pair"] == list(reversed(picked))  # the same chunks, in the order named
    _, tables = served_pair(c)
    assert len(tables) == 2


def test_a_chosen_place_that_is_no_chunk_is_refused(box):
    c = box["cfg"]
    bad = [{"repo": "docs", "path": "forge.md", "line": 999}] * 2
    assert "not where a chunk starts" in draw.draw(c, chosen=bad)["refused"]
    assert "exactly two" in draw.draw(c, R, chosen=bad)["refused"]
