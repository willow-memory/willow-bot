"""phrase_rank: no text dropped from a chunk, dedupe, gated folds, pairs, fields,
fusion, and the keyed cache."""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from onescript import xref  # noqa: E402
from onescript.tests.test_xref import make  # noqa: E402

from onescript import pile as pr  # noqa: E402


def pile(tmp_path, files: dict) -> tuple[dict, dict]:
    repo = make(tmp_path, "docs", files)
    return xref.build([("docs", repo)]), {"docs": repo}


def test_a_long_block_is_split_at_line_breaks_and_nothing_is_dropped():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(30))
    parts = list(pr.chunks(text, 200))
    assert len(parts) > 1 and all(len(s) <= 200 for _, s, _ in parts)
    assert "\n".join(s for _, s, _ in parts) == text.strip()
    assert [ln for ln, _, _ in parts][0] == 1


def test_a_single_line_over_the_limit_is_split_hard_when_it_has_no_space():
    text = "y" * 900
    parts = list(pr.chunks(text, 200))
    assert len(parts) == 5 and all(len(s) <= 200 for _, s, _ in parts)
    assert "".join(s for _, s, _ in parts) == text
    assert {ln for ln, _, _ in parts} == {1} and all(not t for *_, t in parts)


def test_a_single_line_over_the_limit_is_split_at_whitespace():
    words = [f"w{i}" for i in range(300)]
    line = " ".join(words)  # 1,389 characters, words of uneven length
    parts = list(
        pr.chunks("intro paragraph long enough to stand alone\n\n" + line, 200)
    )
    body = parts[1:]
    assert len(body) == 7 and all(len(s) <= 200 for _, s, _ in body)
    assert [w for _, s, _ in body for w in s.split(" ")] == words  # cut at spaces
    assert {ln for ln, _, _ in body} == {3}


def test_a_split_line_is_spread_evenly_with_no_sliver():
    line = " ".join(f"w{i:03d}" for i in range(210))  # 1,049 characters
    body = list(pr.chunks(line, 200))
    assert len(body) == 6 and min(len(s) for _, s, _ in body) >= 150


def test_a_short_block_rides_on_the_next_chunk_as_its_title():
    text = (
        "An opening paragraph that is long enough to stand.\n\n"
        "> **What's the first bite?**\n\n"
        "The forge asks the maker what to build, and maps it.\n"
    )
    assert list(pr.chunks(text, 500)) == [
        (1, "An opening paragraph that is long enough to stand.", frozenset()),
        (
            3,  # the title's line, not the body's
            "> **What's the first bite?**\n\n"
            "The forge asks the maker what to build, and maps it.",
            frozenset({0}),
        ),
    ]


def test_a_short_block_with_nothing_after_rides_on_the_last_chunk():
    text = "A paragraph long enough to be a chunk on its own.\n\nshort tail\n"
    assert list(pr.chunks(text, 500)) == [
        (1, "A paragraph long enough to be a chunk on its own.\n\nshort tail", {2})
    ]
    assert list(pr.chunks("tiny\n\nfile\n", 500)) == [(1, "tiny\n\nfile", {0, 2})]


def test_a_run_of_short_blocks_past_the_limit_stands_as_its_own_chunk():
    text = "\n\n".join(f"item {i:02d}" for i in range(12)) + "\n\n" + "z " * 25
    (l1, s1, t1), (l2, s2, t2) = pr.chunks(text, 60)
    assert (l1, l2) == (1, 15)  # items 00-06 alone; 07-11 ride on the z line
    assert t1 == {i for i, x in enumerate(s1.split("\n")) if x}  # all title
    assert len(t2) == 5 and s2.endswith("z")


def test_no_line_of_text_is_ever_dropped():
    text = (
        "\n# Title\n\nok\n\n" + "word " * 250 + "\n\n- one\n- two\n\n"
        "A closing paragraph of a fair length, enough to stand.\n\n?\n"
    )
    long_first = "\n" + "\n".join(f"line {i} " + "x" * 50 for i in range(4))
    assert [ln for ln, _, _ in pr.chunks(long_first, 128)] == [2, 4]
    for limit in (40, 128, 500):
        got = "".join("".join(s.split()) for _, s, _ in pr.chunks(text, limit))
        assert got == "".join(text.split()), limit
        lines = text.split("\n")
        for ln, s, _ in pr.chunks(text, limit):  # the line is the first line's
            assert s.split("\n")[0].split()[0] in lines[ln - 1], (limit, ln)


def test_identical_chunks_count_once_with_their_copies(tmp_path):
    para = "the same paragraph about ad breaks in a game\n"
    idx, repos = pile(
        tmp_path, {"a.md": para, "b.md": para, "c.md": "other ad text here ok"}
    )
    out = pr.rank(idx, repos, "ad breaks")
    assert out["copies"] == 1
    assert [h["copies"] for h in out["hits"] if h["path"] in ("a.md", "b.md")] == [2]


def test_the_fold_needs_its_stem_attested():
    df = collections.Counter({"ads": 26, "ad": 132, "was": 7430, "wa": 40, "games": 9})
    fold = pr.folder(df)
    assert fold("ads") == "ad"
    assert fold("was") == "was"  # "wa" is too rare to be its stem
    assert fold("games") == "games"  # no "game" in the pile, no fold


def test_ies_and_es_fold_only_when_attested():
    df = collections.Counter(
        {"stories": 5, "story": 9, "boxes": 4, "box": 8, "matches": 3, "glasses": 2}
    )
    fold = pr.folder(df)
    assert fold("stories") == "story" and fold("boxes") == "box"
    assert fold("matches") == "matches"  # "match" absent
    assert fold("glasses") == "glasses"  # -ss is never a plural -s, "glasse" absent


def test_npmi_is_bounded_and_zero_at_independence():
    assert pr.npmi(10, 10, 10, 1000) == pytest.approx(1.0)
    assert pr.npmi(1, 10, 100, 1000) == pytest.approx(0.0)
    assert pr.npmi(0, 10, 10, 1000) == -1.0


def test_a_pair_seen_once_is_not_kept(tmp_path):
    files = {
        f"p{i}.md": f"pattern matching rule number {i} for words\n" for i in range(3)
    }
    files["one.md"] = "some stupid thing that happened once only\n"
    files.update(
        {f"s{i}.md": f"some text {i} and stupid text {i} apart\n" for i in range(3)}
    )
    idx, repos = pile(tmp_path, files)
    pairs = pr.rank(idx, repos, "some stupid pattern matching")["pairs"]
    assert "pattern matching" in pairs and "some stupid" not in pairs


def test_a_question_line_outweighs_the_same_words_in_the_body(tmp_path):
    files = {
        "q.md": "filler words here to pad\nWhat counts the ad breaks?\n",
        "b.md": "filler words here to pad\nWhat counts the ad breaks.\n",
        "z.md": "nothing relevant in this file at all\n",
    }
    idx, repos = pile(tmp_path, files)
    hits = pr.rank(idx, repos, "ad breaks")["hits"]
    assert [h["path"] for h in hits][:2] == ["q.md", "b.md"]
    assert hits[0]["score"] > hits[1]["score"]


def test_rrf_fuses_by_rank_and_breaks_ties_by_key():
    # a and c tie (1/61 + 1/63) just above b (2/62); the tie goes by key
    assert pr.rrf(["a", "b", "c"], ["c", "b", "a"]) == ["a", "c", "b"]
    assert pr.rrf(["a", "b"], ["b", "a"], ["b"]) == ["b", "a"]


def test_the_embedder_is_fused_only_when_it_answers(tmp_path):
    files = {
        "a.md": "ad breaks and games here, said plainly enough\n",
        "b.md": "games here and ad breaks too, said a second way\n",
    }
    idx, repos = pile(tmp_path, files)
    plain = pr.rank(idx, repos, "ad breaks games")
    flipped = pr.rank(
        idx, repos, "ad breaks games", embed=lambda b, c: [w for w, _ in c][::-1]
    )
    silent = pr.rank(idx, repos, "ad breaks games", embed=lambda b, c: None)
    assert not plain["fused"] and flipped["fused"] and not silent["fused"]
    assert silent["hits"] == plain["hits"]


def test_same_pile_same_bite_same_bytes(tmp_path):
    idx, repos = pile(tmp_path, {"a.md": "ad breaks\n" * 3, "b.md": "games of ads\n"})
    assert pr.rank(idx, repos, "ads games") == pr.rank(idx, repos, "ads games")


def test_no_readable_repo_is_unreachable(tmp_path):
    idx, _ = pile(tmp_path, {"a.md": "ad breaks\n"})
    assert pr.rank(idx, {}, "ad")["state"] == "unreachable"


def test_the_sweep_scores_each_query_by_its_target(tmp_path):
    files = {
        "t.md": "the ad breaks paper with many words in it today\n",
        "o.md": "other games text that is long enough to count\n",
    }
    idx, repos = pile(tmp_path, files)
    qs = [
        {"bite": "ad breaks", "targets": ["docs:t.md"]},
        {"bite": "zebra", "targets": ["docs:t.md"]},
    ]
    out = pr.sweep(idx, repos, qs, [200])
    assert out[200]["rr"] == [1.0, 0.0] and out[200]["mrr"] == 0.5


def test_a_double_s_is_never_folded_even_with_a_stem_on_record():
    fold = pr.folder(collections.Counter({"glass": 5, "glas": 9}))
    assert fold("glass") == "glass"


def test_the_rrf_constant_lets_agreement_beat_one_first_place():
    # b is second in two lists, a and c first in one each: with k=60, b leads
    assert pr.rrf(["a", "b"], ["c", "b"]) == ["b", "a", "c"]


CACHE_FILES = {
    "a.md": (
        "Nobody counts the ad breaks in kids games, not even once.\n\n"
        "What counts the ad breaks in a game for kids today?\n"
    ),
    "b.md": "Games with ads, and stories about games, and more stories here.\n",
    "c.md": "Nobody counts the ad breaks in kids games, not even once.\n",  # a copy
    "d.md": "pattern matching skills, for the box; pattern matching again\n" * 3,
    "e.md": "one long line " + "word " * 300 + "\n",
    "f.md": "the ad\nbreaks are split across a line here, for the test\n",
    "h.md": "the ad breaks again, said in a plain body line here\n",
    "g.md": (
        "> **What's the first bite?**\n\n"
        "The forge asks the maker what to build and maps it to a kind.\n\n"
        "Is the tail kept?\n"
    ),
    "k.md": "Kid stories\n\nA paragraph about nothing in particular, padded out.\n",
    "s.md": "split " + "lines " * 120 + "\nand a pattern matching skills ending\n",
    **{
        f"x{i}.md": f"a filler file, number {i}, of plain unrelated words\n"
        for i in range(6)
    },
}
BITES = [
    "ad breaks",
    "kids games and stories",
    "pattern matching skills",
    "word",
    "what's the first bite",
    "is the tail kept",
    "split lines",
]


KEY = bytes(range(32))


def cached(tmp_path, files):
    idx, repos = pile(tmp_path, files)
    db, state = pr.open_cache(idx, repos, tmp_path / "c" / "p.sqlite", "sha-1", KEY)
    return idx, repos, pr.Cached(db, KEY), state


def test_the_cache_ranks_exactly_as_the_direct_read(tmp_path):
    idx, repos, c, state = cached(tmp_path, CACHE_FILES)
    assert state == "built"
    for b in BITES:
        assert c.rank(b) == pr.rank(idx, repos, b), b
    # the pair and the question field are really in play, not equal by absence
    out = c.rank("ad breaks")
    assert "ad breaks" in out["pairs"]
    top = out["hits"][0]
    assert (top["path"], top["line"]) == ("a.md", 3)  # the question outranks


def test_the_cache_holds_no_word_or_sentence_text(tmp_path):
    _, _, c, _ = cached(
        tmp_path, {"a.md": "zanzibarquokka appears here once only, in this one file\n"}
    )
    raw = (tmp_path / "c" / "p.sqlite").read_bytes()
    assert b"zanzibarquokka" not in raw and b"appears here" not in raw
    assert c.rank("zanzibarquokka")["hits"][0]["path"] == "a.md"


def test_the_cache_is_warm_for_the_same_index_and_rebuilt_for_another(tmp_path):
    idx, repos, _, _ = cached(tmp_path, {"a.md": "the first version of a file\n"})
    p = tmp_path / "c" / "p.sqlite"
    assert pr.open_cache(idx, repos, p, "sha-1", KEY)[1] == "warm"
    assert pr.open_cache(idx, repos, p, "sha-2", KEY)[1] == "built"
    assert pr.open_cache(idx, repos, p, "sha-2", KEY, limit=128)[1] == "built"
    assert pr.open_cache(idx, repos, p, "sha-2", bytes(32), limit=128)[1] == "built"


def test_a_pair_never_spans_a_line_break(tmp_path):
    _, _, c, _ = cached(tmp_path, {"f.md": CACHE_FILES["f.md"], **{"g.md": "x" * 50}})
    assert "ad breaks" not in c.rank("ad breaks")["pairs"]


def test_a_sentence_said_before_comes_back_with_its_count(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    q = c.quotes(
        "Nobody counts the ad breaks in kids games, not even once. Something new."
    )
    # c.md's copy dedupes into a.md's chunk, so the sentence counts once
    assert q["state"] == "populated"
    assert q["quotes"] == [
        {"words": 11, "count": 1, "repo": "docs", "path": "a.md", "line": 1}
    ]
    none = c.quotes("Nothing like this was ever said.")
    assert none["state"] == "empty" and none["quotes"] == []


def test_a_short_question_is_a_quote_and_a_rank_hit(tmp_path):
    """The old failure: a one-line question under MIN never became a chunk."""
    idx, repos, c, _ = cached(tmp_path, CACHE_FILES)
    q = c.quotes("What's the first bite?")
    assert q["state"] == "populated"
    assert q["quotes"] == [
        {"words": 5, "count": 1, "repo": "docs", "path": "g.md", "line": 1}
    ]
    tail = c.quotes("Is the tail kept?")["quotes"]
    assert tail[0]["line"] == 1  # rides on the last chunk
    for out in (c.rank("first bite"), pr.rank(idx, repos, "first bite")):
        top = out["hits"][0]
        assert (top["path"], top["line"]) == ("g.md", 1)


def test_a_title_line_outweighs_the_same_words_in_the_body(tmp_path):
    files = {
        "t.md": "the ad breaks\n\nfiller words here to pad it out, and some more\n",
        "b.md": "the ad breaks\nfiller words here to pad it out, and some more\n",
        "z.md": "nothing relevant in this file at all, just words\n",
    }
    idx, repos, c, _ = cached(tmp_path, files)
    for out in (c.rank("ad breaks"), pr.rank(idx, repos, "ad breaks")):
        hits = out["hits"]
        assert [h["path"] for h in hits][:2] == ["t.md", "b.md"]
        assert hits[0]["score"] > hits[1]["score"]


def test_a_split_line_is_read_back_at_its_line(tmp_path):
    idx, repos, c, _ = cached(tmp_path, CACHE_FILES)
    hits = [h for h in c.rank("split lines")["hits"] if h["path"] == "s.md"]
    assert len(hits) >= 2 and {h["line"] for h in hits} == {1}
    for h in hits:
        ch = {**h, "sha256": h["chunk"]}
        text, why = pr.read_chunk(idx, repos, ch)
        assert why is None and len(text) <= pr.MAX


def test_a_question_past_markdown_emphasis_is_a_question():
    assert pr.is_question("> **What's the first bite?**")
    assert pr.is_question("_is it?_") and pr.is_question('"Is it?"')
    assert not pr.is_question("> **The first bite.**")
    assert not pr.is_question("What? No, a statement.")


def test_an_emphasised_question_line_in_a_body_is_a_question_field(tmp_path):
    files = {
        "q.md": "filler words here to pad\n> **What counts the ad breaks?**\n",
        "b.md": "filler words here to pad\n> **What counts the ad breaks.**\n",
        "z.md": "nothing relevant in this file at all\n",
    }
    idx, repos, c, _ = cached(tmp_path, files)
    for out in (c.rank("ad breaks"), pr.rank(idx, repos, "ad breaks")):
        assert [h["path"] for h in out["hits"]][:2] == ["q.md", "b.md"]
        assert out["hits"][0]["score"] > out["hits"][1]["score"]


REPAIR_FILES = {
    "k.md": "The knowledge base holds every embedder note we wrote down here.\n",
    "c1.md": "A card file, one of two that hold a single word apiece here.\n",
    "c2.md": "A cart file, the other of two that hold a single word apiece.\n",
    "t.md": "the the the the and the, a short word that is everywhere here.\n",
}


def test_edits1_is_every_spelling_one_edit_away_and_sorted():
    e = pr.edits1("ab")
    assert e == sorted(e) and "ab" not in e
    assert {"a", "b", "ba", "xab", "axb", "abx", "xb", "ax"} <= set(e)
    assert all(abs(len(x) - 2) <= 1 for x in e)


def test_an_unseen_word_is_repaired_to_the_spelling_the_store_holds(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    fix = c.repair("Knowlege base")
    assert fix["state"] == "repaired" and fix["bite"] == "knowledge base"
    assert fix["repairs"] == [["knowlege", "knowledge", 1]]
    assert c.rank(fix["bite"])["hits"][0]["path"] == "k.md"
    assert c.rank("knowlege")["hits"] == []  # unrepaired, the word alone misses
    assert c.rank(c.repair("knowlege")["bite"])["hits"][0]["path"] == "k.md"


def test_a_word_the_store_holds_is_never_touched(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    assert c.repair("knowledge embedder") == {
        "state": "none",
        "why": "",
        "bite": "knowledge embedder",
        "repairs": [],
    }


def test_short_and_non_ascii_words_are_left_alone(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    assert c.repair("teh")["state"] == "none"  # under REPAIR_MIN, though "the" is held
    assert c.repair("knöwledge")["state"] == "none"


def test_a_tie_goes_to_the_first_spelling_in_order(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    assert c.repair("carx")["repairs"] == [["carx", "card", 1]]  # card, cart: 1 each


def test_no_spelling_one_edit_away_leaves_the_word(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    assert c.repair("zzqqxx")["state"] == "none"


def test_a_store_that_cannot_be_read_repairs_nothing(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    c.db.close()
    fix = c.repair("knowlege")
    assert fix["state"] == "unreachable" and fix["bite"] == "knowlege"


def test_a_held_word_is_not_swapped_for_a_held_neighbour(tmp_path):
    _, _, c, _ = cached(tmp_path, REPAIR_FILES)
    assert c.repair("card cart")["state"] == "none"  # each is one edit from the other


def test_es_is_a_plural_only_after_s_x_z_ch_sh_and_o():
    assert "ones" not in pr.forms("on") and "boxes" in pr.forms("box")
    assert "on" not in pr.stems("ones") and "box" in pr.stems("boxes")
    fold = pr.folder(collections.Counter({"ones": 9, "on": 99, "boxes": 4, "box": 8}))
    assert fold("ones") == "ones"  # "one" + s, never "on" + es
    assert fold("boxes") == "box"


def test_the_pair_basin_knob_drops_pairs_of_common_words_only_when_set(
    tmp_path, monkeypatch
):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    assert "ad breaks" in c.rank("ad breaks")["pairs"]  # off by default
    monkeypatch.setattr(pr, "PAIR_BASIN", 0.0)
    assert c.rank("ad breaks")["pairs"] == {}
