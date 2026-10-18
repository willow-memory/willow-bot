"""The repetition ladder over bites, the tombstones of chunks gone, and the
chunk sizes on the scope card."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, ladder  # noqa: E402
from onescript.tests.test_api import cfg, proof, rows  # noqa: E402,F401
from onescript.tests.test_seed import index_into_box  # noqa: E402
from onescript.tests.test_store import BITE, DOCS, box  # noqa: E402,F401
from onescript.tests.test_xref import make, sh  # noqa: E402

FIRST = DOCS["ads.md"].split("\n\n")[0].strip()


def ask(cfg, bite):  # noqa: F811
    """One check-in, one seed: one ask."""
    assert api.checkin(cfg)["code"] == 0
    return api.seed(cfg, bite)


def of(cfg, kind):  # noqa: F811
    return [r for r in rows(cfg) if r["kind"] == kind]


def offer_lines(cfg):  # noqa: F811
    return [x for x in api.needs(cfg)["needs"] if x.startswith("offer · ")]


# ── 1. the ladder ──────────────────────────────────────────────────────────
def test_the_key_is_the_hash_of_the_bites_words_in_order():
    k = ladder.bite_key("Fix  THE serve!")
    assert k == hashlib.sha256(b"fix the serve").hexdigest()
    assert ladder.bite_key("fix the serve") == k
    assert ladder.bite_key("fix\tthe\nserve.") == k
    assert ladder.bite_key("the fix serve") != k  # order counts: nothing fuzzy
    assert ladder.bite_key("fix the serves") != k
    assert ladder.bite_key("fix serve") != k
    assert ladder.bite_key(" ?! ") is None


def test_one_does_two_notices_three_offers(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    res = api.seed(cfg, BITE)
    assert (res["asked"], res["ladder"]) == (1, "done")
    assert ask(cfg, BITE.upper())["ladder"] == "noticed"  # the same words
    assert of(cfg, "offer") == [] and offer_lines(cfg) == []
    third = ask(cfg, BITE)
    assert (third["asked"], third["ladder"]) == (3, "offer")
    [o] = of(cfg, "offer")
    assert o["n"] == third["offer_row"] and o["seed_row"] == third["row"]
    assert o["key"] == ladder.bite_key(BITE) and o["asked"] == 3
    assert offer_lines(cfg) == [
        f"offer · asked 3 times: make it standing? · key {o['key']}"
    ]
    seeds = of(cfg, "seed")
    assert [s["ladder"] for s in seeds] == ["done", "noticed", "offer"]
    # only the hash: the normalized bite is never stored
    norm = " ".join(ladder.seed.WORD.findall(BITE.lower()))
    assert norm not in (cfg.box / "record.jsonl").read_text()


def test_a_different_bite_counts_on_its_own(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    ask(cfg, BITE)
    assert ask(cfg, "the ad breaks again")["ladder"] == "done"
    assert ask(cfg, BITE)["ladder"] == "offer"


def test_a_refused_seed_is_not_an_ask(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    assert api.seed(cfg, BITE)["code"] == 1  # once per check-in: refused
    assert api.seed(cfg, BITE)["code"] == 1
    assert ask(cfg, BITE)["asked"] == 2
    assert of(cfg, "offer") == []


def test_the_offer_comes_again_every_third_ask_and_lists_once(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    got = [ask(cfg, BITE)["ladder"] for _ in range(5)]
    assert got == ["noticed", "offer", "noticed", "noticed", "offer"]
    assert [o["asked"] for o in of(cfg, "offer")] == [3, 6]
    [line] = offer_lines(cfg)
    assert "asked 6 times" in line


def test_a_recorded_no_stops_the_offer(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    ask(cfg, BITE)
    ask(cfg, BITE)
    key = ladder.bite_key(BITE)
    res = api.decline_offer(cfg, key)
    assert res["code"] == 0 and of(cfg, "offer_declined")[0]["key"] == key
    assert offer_lines(cfg) == []
    got = [ask(cfg, BITE)["ladder"] for _ in range(3)]
    assert got == ["declined"] * 3  # the 6th ask offers nothing
    assert len(of(cfg, "offer")) == 1 and offer_lines(cfg) == []
    again = api.decline_offer(cfg, key)
    assert again["already"] and len(of(cfg, "offer_declined")) == 1


def test_a_no_to_one_key_leaves_another_offered(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    other = "the ad breaks again"
    api.seed(cfg, BITE)
    for b in (other, BITE, other, BITE, other):
        ask(cfg, b)
    assert len(offer_lines(cfg)) == 2
    api.decline_offer(cfg, ladder.bite_key(BITE))
    [line] = offer_lines(cfg)
    assert ladder.bite_key(other) in line


def test_a_no_to_a_key_never_offered_is_refused(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    ask(cfg, BITE)  # noticed, not offered
    res = api.decline_offer(cfg, ladder.bite_key(BITE))
    assert res["code"] == 1 and "no offer" in res["refused"]
    assert of(cfg, "offer_declined") == []


def test_a_no_needs_an_open_box(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    ask(cfg, BITE)
    ask(cfg, BITE)
    api.checkout(cfg)
    res = api.decline_offer(cfg, ladder.bite_key(BITE))
    assert res["code"] == 1 and of(cfg, "offer_declined") == []
    assert len(offer_lines(cfg)) == 1


def offered(cfg):  # noqa: F811
    api.seed(cfg, BITE)
    ask(cfg, BITE)
    ask(cfg, BITE)
    return ladder.bite_key(BITE)


def unsealed_lines(cfg):  # noqa: F811
    return [x for x in api.needs(cfg)["needs"] if "said by the caller" in x]


def test_an_unsigned_no_is_said_by_the_caller_and_counted(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    key = offered(cfg)
    res = api.decline_offer(cfg, key)
    assert res["sealed"] is False
    [row] = of(cfg, "offer_declined")
    assert row["said_by"] == "caller" and row["sealed"] is False
    assert "proof" not in row
    [line] = unsealed_lines(cfg)
    assert "1 offer No(s)" in line and key in line
    assert offer_lines(cfg) == []  # unsealed, it still narrows


def test_a_sealed_no_stands_over_the_unsealed_one(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    key = offered(cfg)
    api.decline_offer(cfg, key)
    res = api.decline_offer(cfg, key, proof=proof(ladder.decline_subject(key)))
    assert res["sealed"] is True and "already" not in res
    assert [r["said_by"] for r in of(cfg, "offer_declined")] == ["caller", "human"]
    assert unsealed_lines(cfg) == []
    again = api.decline_offer(cfg, key, proof=proof(ladder.decline_subject(key)))
    assert again["already"] and len(of(cfg, "offer_declined")) == 2


def test_a_sealed_no_first_needs_no_second(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    key = offered(cfg)
    assert api.decline_offer(cfg, key, proof=proof(ladder.decline_subject(key)))[
        "sealed"
    ]
    assert api.decline_offer(cfg, key)["already"]
    assert unsealed_lines(cfg) == [] and len(of(cfg, "offer_declined")) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "0" * 64,  # not the human's
        "seal-of-another-subject",
    ],
)
def test_a_proof_that_does_not_seal_this_no_is_refused(box, bad):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    key = offered(cfg)
    res = api.decline_offer(cfg, key, proof=bad)
    assert res["code"] == 1 and "doesn't seal" in res["refused"]
    assert of(cfg, "offer_declined") == []
    wrong = proof(ladder.decline_subject("ab" * 32))  # sealed, for another key
    assert api.decline_offer(cfg, key, proof=wrong)["code"] == 1
    assert len(offer_lines(cfg)) == 1


def test_without_the_human_key_a_proof_is_refused(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    key = offered(cfg)
    cfg.keys.write_text(json.dumps({"desk": "aa" * 32}))
    res = api.decline_offer(cfg, key, proof=proof(ladder.decline_subject(key)))
    assert res["code"] == 1 and of(cfg, "offer_declined") == []


# ── 2. tombstones ─────────────────────────────────────────────────────────
def sealed_scope(cfg, top=1):  # noqa: F811
    api.seed(cfg, BITE)
    card = api.scope(cfg, by=(), seed_top=top)
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    return card


def move_first_paragraph(repo):
    """The chunk moves to line 3 of the same file, its bytes unchanged."""
    (repo / "ads.md").write_text(
        # at least MIN long, so it stands alone and isn't the next chunk's title
        "A new opening paragraph, first now, long enough to stand.\n\n" + DOCS["ads.md"]
    )
    sh(repo, "commit", "-qam", "two")


def test_a_chunk_not_at_its_line_is_tombstoned_once_per_check_in(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    card = sealed_scope(cfg)
    move_first_paragraph(box["repo"])
    index_into_box(cfg, box["repo"])
    res = api.serve(cfg, card["spec"], max_chars=10**6)
    assert res["state"] == "unreachable"  # serve's answer is unchanged
    assert "isn't at that line" in res["doc"]["tables"][0]["why"]
    [t] = of(cfg, "tombstone")
    seed_row = rows(cfg)[card["spec"]["seed"]["row"]]
    assert t["chunk"] == seed_row["rank"][0]["chunk"]
    assert (t["repo"], t["path"], t["line"]) == ("docs", "ads.md", 1)
    assert t["reason"] == "not_at_line" and t["reason"] in ladder.REASONS
    assert t["forward"] is None  # this check-in's store was built for the old index
    assert FIRST not in json.dumps(t)  # a pointer, never the text
    api.serve(cfg, card["spec"], max_chars=10**6)
    assert len(of(cfg, "tombstone")) == 1  # once per chunk per check-in


def test_a_tombstone_says_where_the_same_chunk_sits_now(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    card = sealed_scope(cfg)
    move_first_paragraph(box["repo"])
    index_into_box(cfg, box["repo"])
    assert api.checkin(cfg)["report"]["store"]["how"] == "built"  # the new index
    res = api.serve(cfg, card["spec"], max_chars=10**6)
    assert res["state"] == "unreachable"
    [t] = of(cfg, "tombstone")
    assert t["forward"] == ["docs", "ads.md", 3] and t["looked"] == "store"
    assert t["reason"] == "not_at_line"


def test_a_missing_blob_is_tombstoned_as_gone(box, tmp_path):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    card = sealed_scope(cfg)
    empty = make(tmp_path, "elsewhere", {"x.md": "nothing of that blob here\n"})
    res = api.serve(replace(cfg, repos={"docs": empty}), card["spec"], max_chars=10**6)
    assert res["state"] == "unreachable"
    [t] = of(cfg, "tombstone")
    assert t["reason"] == "blob_gone"


def test_an_unreachable_checkout_is_not_a_tombstone(box, tmp_path):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    card = sealed_scope(cfg)
    box["repo"].rename(tmp_path / "moved")
    res = api.serve(cfg, card["spec"], max_chars=10**6)
    assert res["state"] == "unreachable"
    assert of(cfg, "tombstone") == []


def test_a_readable_chunk_leaves_no_tombstone(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    card = sealed_scope(cfg, top=2)
    assert api.serve(cfg, card["spec"], max_chars=10**6)["state"] == "populated"
    assert of(cfg, "tombstone") == []


def test_reason_words_are_fixed():
    assert ladder.reason("not in the index") == "not_in_index"
    assert ladder.reason("the chunk isn't at that line with that hash") == "not_at_line"
    why = "the blob doesn't hash to the index"
    assert ladder.reason(why, lambda: True) == "hash_mismatch"
    assert ladder.reason(why, lambda: False) == "blob_gone"
    assert ladder.reason(why, lambda: None) == "hash_mismatch"  # can't tell
    assert ladder.reason("no checkout to read it from") is None
    assert ladder.reason("git: fatal") is None
    assert ladder.reason(None) is None


def test_forward_is_only_a_place_that_reads_back():
    ch = {"repo": "r", "path": "a", "line": 1, "sha256": "s"}
    places = [("r", "a", 1, "s"), ("r", "b", 2, "t"), ("r", "c", 3, "s")]
    assert ladder.forward(ch, places, lambda c: ("x", None)) == ["r", "c", 3]
    assert ladder.forward(ch, places, lambda c: (None, "gone")) is None
    assert ladder.forward(ch, places[:2], lambda c: ("x", None)) is None


# ── 3. chunk sizes on the scope card ──────────────────────────────────────
def test_the_card_gives_each_chunks_size_and_no_text(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    card = api.scope(cfg, by=(), seed_top=2)
    chunks = [s for s in card["stack"] if s["kind"] == "chunk"]
    assert chunks[0]["name"] == "docs:ads.md:1" and chunks[0]["chars"] == len(FIRST)
    assert all(isinstance(s["chars"], int) and s["chars"] > 0 for s in chunks)
    assert card["size"]["chunk_chars"] == sum(s["chars"] for s in chunks)
    assert card["size"]["unreadable"] == 0 and "fits" not in card["size"]
    assert FIRST not in json.dumps(card)
    assert of(cfg, "scope")[-1]["card"]["size"] == card["size"]


def test_the_card_says_whether_the_stack_fits_exactly(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    n = api.scope(cfg, by=(), seed_top=2)["size"]["served_chars"]
    assert n > api.scope(cfg, by=(), seed_top=2)["size"]["chunk_chars"]
    at = api.scope(cfg, by=(), seed_top=2, max_chars=n)
    under = api.scope(cfg, by=(), seed_top=2, max_chars=n - 1)
    assert at["size"]["fits"] is True and under["size"]["fits"] is False
    assert at["subject"] == under["subject"]  # the sizes are not in the subject
    api.seal_scope(cfg, at["subject"], proof=proof(at["subject"]))
    assert api.serve(cfg, at["spec"], max_chars=n)["state"] == "populated"
    assert api.serve(cfg, at["spec"], max_chars=n - 1)["state"] == "empty"


def test_an_unreadable_chunk_is_said_on_the_card(box, tmp_path):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    api.seed(cfg, BITE)
    box["repo"].rename(tmp_path / "moved")
    card = api.scope(cfg, by=(), seed_top=1, max_chars=10**6)
    [c] = card["stack"]
    assert c["chars"] is None and "no checkout" in c["unreadable"]
    assert card["size"]["unreadable"] == 1 and card["size"]["chunk_chars"] == 0
    assert of(cfg, "tombstone") == []  # scope reads; only serve tombstones


def test_a_stack_without_chunks_has_no_size_unless_asked(box):  # noqa: F811
    cfg = box["cfg"]  # noqa: F811
    assert "size" not in api.scope(cfg, by=("who",))
    sized = api.scope(cfg, by=("who",), max_chars=1)
    assert sized["size"]["chunk_chars"] == 0


# ── the CLI ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "argv,verb,want",
    [
        (["scope", "--max-chars", "900"], "scope", {"max_chars": 900}),
        (["decline", "ab" * 32], "decline_offer", {"key": "ab" * 32}),
        (["decline", "ab" * 32, "--proof", "pp"], "decline_offer", {"proof": "pp"}),
    ],
)
def test_the_cli_passes_the_new_arguments(tmp_path, monkeypatch, argv, verb, want):
    got = {}

    def fake(cfg, *a, **kw):  # noqa: F811
        got.update(kw)
        if a:
            got["key"] = a[0]
        return {"code": 0}

    monkeypatch.setattr(api, verb, fake)
    cli.main(["--box", str(tmp_path / "box"), "--keys", str(tmp_path / "k"), *argv])
    assert {k: got.get(k) for k in want} == want
