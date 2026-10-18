"""phrase_rank: chunks never dropped, dedupe, gated folds, pairs, fields, fusion."""

from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from onescript import xref  # noqa: E402
from onescript.tests.test_xref import make  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "phrase_rank", ROOT / "scripts" / "scan" / "phrase_rank.py"
)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


def pile(tmp_path, files: dict) -> tuple[dict, dict]:
    repo = make(tmp_path, "docs", files)
    return xref.build([("docs", repo)]), {"docs": repo}


def test_a_long_block_is_split_at_line_breaks_and_nothing_is_dropped():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(30))
    parts = list(pr.chunks(text, 200))
    assert len(parts) > 1 and all(len(s) <= 200 for _, s in parts)
    assert "\n".join(s for _, s in parts) == text.strip()
    assert [ln for ln, _ in parts][0] == 1


def test_a_single_line_over_the_limit_is_kept_whole():
    text = "y" * 900
    assert list(pr.chunks(text, 200)) == [(1, text)]


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
    **{
        f"x{i}.md": f"a filler file, number {i}, of plain unrelated words\n"
        for i in range(6)
    },
}
BITES = ["ad breaks", "kids games and stories", "pattern matching skills", "word"]


def cached(tmp_path, files):
    idx, repos = pile(tmp_path, files)
    db, state = pr.open_cache(idx, repos, tmp_path / "c" / "p.sqlite", "sha-1")
    return idx, repos, pr.Cached(db), state


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


def test_the_cache_holds_hashes_never_text(tmp_path):
    _, _, c, _ = cached(
        tmp_path, {"a.md": "zanzibarquokka appears here once only, in this one file\n"}
    )
    raw = (tmp_path / "c" / "p.sqlite").read_bytes()
    assert b"zanzibarquokka" not in raw and b"appears here" not in raw
    assert c.rank("zanzibarquokka")["hits"][0]["path"] == "a.md"


def test_the_cache_is_warm_for_the_same_index_and_rebuilt_for_another(tmp_path):
    idx, repos, _, _ = cached(tmp_path, {"a.md": "the first version of a file\n"})
    p = tmp_path / "c" / "p.sqlite"
    assert pr.open_cache(idx, repos, p, "sha-1")[1] == "warm"
    assert pr.open_cache(idx, repos, p, "sha-2")[1] == "built"
    assert pr.open_cache(idx, repos, p, "sha-2", limit=128)[1] == "built"


def test_a_pair_never_spans_a_line_break(tmp_path):
    _, _, c, _ = cached(tmp_path, {"f.md": CACHE_FILES["f.md"], **{"g.md": "x" * 50}})
    assert "ad breaks" not in c.rank("ad breaks")["pairs"]


def test_a_sentence_said_before_comes_back_with_its_count(tmp_path):
    _, _, c, _ = cached(tmp_path, CACHE_FILES)
    q = c.quotes(
        "Nobody counts the ad breaks in kids games, not even once. Something new."
    )
    # c.md's copy dedupes into a.md's chunk, so the sentence counts once
    assert q == [{"words": 11, "count": 1, "repo": "docs", "path": "a.md", "line": 1}]
    assert c.quotes("Nothing like this was ever said.") == []
