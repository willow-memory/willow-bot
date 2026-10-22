"""The compiled ideas run (one-script/compiled/ideas.py; IDEAS.md).

Every idea snippet_cover called `compiled` is run here against a real box,
with offline stand-ins for the drand reader, the price reader and the
fetcher. Each forecast or verdict it writes must stand on the record; with
no reader, each outside read is `unreachable`, said.
"""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

from onescript.states import POPULATED, UNREACHABLE
from onescript.tests.test_market import DAY, Clock
from onescript.tests.test_table import cfg, rows  # noqa: F401  (the box and its keys)

IDEAS = Path(__file__).resolve().parents[2] / "compiled" / "ideas.py"
spec = importlib.util.spec_from_file_location("compiled_ideas", IDEAS)
ideas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ideas)

RANDOMNESS = "ab" * 32


class Web:
    """The fetcher, offline: the first prefix a URL starts with gives its bytes."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages

    def __call__(self, url: str) -> bytes:
        for prefix, data in self.pages.items():
            if url.startswith(prefix):
                return data
        raise LookupError(f"{url} is not served here")


WEB = Web({
    "https://api.open-meteo.com/": b'{"daily": {"precipitation_sum": [2.5]}}',
    "https://earthquake.usgs.gov/": b"7\n",
    "https://hn.algolia.com/": json.dumps({"hits": [{"title": "Show HN: a pile"}]}).encode(),
    "https://api.tidesandcurrents.noaa.gov/": json.dumps(
        {"predictions": [{"t": "2026-10-21 03:12", "v": "1.6", "type": "H"}]}
    ).encode(),
    "https://www.gutenberg.org/": b"Call me Ishmael.",
    f"{ideas.MET}/": json.dumps({"title": "A Dead Pheasant", "isPublicDomain": True}).encode(),
    ideas.MET: json.dumps({"objectIDs": [11, 22, 33]}).encode(),
})  # fmt: skip


@pytest.fixture
def online(cfg):
    return dataclasses.replace(
        cfg, fetch=WEB, beacon=lambda rnd: {"round": rnd, "randomness": RANDOMNESS}
    )


def kinds(c) -> list[str]:
    return [r["kind"] for r in rows(c)]


def test_4_streaks_read_from_sighs_store(tmp_path):
    db = tmp_path / "sigh.db"
    for head in ("a", "b", "c"):
        ideas.sigh.fail("willow-bot", 160, head, path=db)
    assert ideas.ci_streaks([("willow-bot", 160), ("jeles", 1)], path=db) == {
        "willow-bot#160": 3,
        "jeles#1": 0,
    }
    assert 4 in ideas.BLOCKED  # the rows themselves: no `what` for them


def test_5_a_ci_bet_stands_and_scores(cfg):
    assert ideas.bet_ci(cfg, 160, 0.7)["code"] == 0
    s = ideas.score([(0.7, True), (0.2, False)])
    assert s["n"] == 2 and abs(s["brier"] - ((0.3**2 + 0.2**2) / 2)) < 1e-12
    assert ideas.score([])["brier"] is None


def test_6_a_fortune_is_the_same_for_the_same_round_and_login(online):
    one = ideas.fortune(RANDOMNESS, "sean")
    assert one == ideas.fortune(RANDOMNESS, "sean")
    assert one["line"] in ideas.horoscope._TEMPLATES
    got = ideas.fortune_for(online, "2026-10-21", "sean")
    assert got["state"] == POPULATED and got["line"] == one["line"]


def test_6_no_beacon_is_unreachable(cfg):
    assert ideas.fortune_for(cfg, "2026-10-21", "sean")["state"] == UNREACHABLE


def test_8_the_graph_answers_per_repo(monkeypatch):
    monkeypatch.setenv("ONESCRIPT_CBM", "")
    monkeypatch.setenv("PATH", "/nonexistent")
    got = ideas.code_graph(["willow-bot", "jeles"], "no cites here")
    assert set(got) == {"willow-bot", "jeles"}


def test_15_and_98_runes_are_cast_from_shas():
    sha = "f" * 64
    assert ideas.session_rune(f"session:{sha}") == ideas.runes.cast(sha)
    spread = ideas.tarot("a" * 40, "b" * 40, "c" * 40)
    assert list(spread) == ["past", "present", "future"]
    assert spread == ideas.tarot("a" * 40, "b" * 40, "c" * 40)


def test_33_the_rain_bet_stands_beside_its_snapshot(online):
    got = ideas.rain_bet(online, "2026-10-22", 51.5, -0.1, 0.3)
    assert got["snapshot"]["state"] == POPULATED and got["forecast"]["code"] == 0
    assert kinds(online).count("snapshot") == 1 and "wager" in kinds(online)


@pytest.mark.parametrize(
    "call,key,want",
    [
        (lambda c: ideas.quake_count(c, "2026-10-12", "2026-10-18"), "count", 7),
        (lambda c: ideas.hn_front(c, "2026-10-21"), "titles", ["Show HN: a pile"]),
        (lambda c: ideas.tides(c, "9414290", "20261021"), "predictions",
         [{"t": "2026-10-21 03:12", "v": "1.6", "type": "H"}]),
        (lambda c: ideas.book(c, 2701), "text", "Call me Ishmael."),
    ],
)  # fmt: skip
def test_34_37_38_42_the_world_reads_through_snapshots(online, cfg, call, key, want):
    got = call(online)
    assert got["state"] == POPULATED and got[key] == want
    assert call(cfg)["state"] == POPULATED  # held now: no fetcher needed


def test_world_reads_with_no_fetcher_are_unreachable(cfg):
    assert ideas.quake_count(cfg, "2026-10-12", "2026-10-18")["state"] == UNREACHABLE
    assert ideas.art_of_the_day(cfg, RANDOMNESS)["state"] == UNREACHABLE


def test_45_the_art_of_the_day_is_picked_by_the_round(online):
    one = ideas.art_of_the_day(online, RANDOMNESS)
    assert one["state"] == POPULATED and one["object"] in (11, 22, 33)
    assert one == ideas.art_of_the_day(online, RANDOMNESS)
    assert one["title"] == "A Dead Pheasant"


def test_87_roll_for_merge_is_a_roll_verdict(cfg, tmp_path):
    pytest.importorskip("dice")
    db = tmp_path / "sigh.db"
    ideas.sigh.fail("willow-bot", 160, "a", path=db)
    got = ideas.roll_for_merge(cfg, "willow-bot", 160, RANDOMNESS, 3, sigh_path=db)
    assert got["code"] == 0 and 1 <= got["nat"] <= 20 and got["total"] == got["nat"] + 2
    (row,) = [r for r in rows(cfg) if r["kind"] == "verdict"]
    assert row["what"] == "roll" and row["mod"] == 2


def test_88_the_horoscope_forecasts_as_the_oracle(cfg):
    got = ideas.grade_horoscope(cfg, "2026-10-21", "sean")
    assert got["code"] == 0 and got["line"]
    (w,) = [r for r in rows(cfg) if r["kind"] == "wager"]
    assert w["by"] == "oracle"


def test_89_the_sigh_reads_as_a_probability():
    assert ideas.sigh_forecast(2) == 0.5  # no sigh yet
    assert ideas.sigh_forecast(5) == pytest.approx(0.8)  # sighhh
    assert ideas.sigh_forecast(40) == 0.95


def test_92_quake_digits_are_binned():
    got = ideas.quake_dice([4.5, 4.5, 6.1, 5.0])
    assert sum(b["n"] for b in got) == 4 and ideas.quake_dice([]) == []


def test_94_a_rainy_day_scope_is_only_proposed(cfg):
    day = rows(cfg)[-1]["ts"][:10]
    got = ideas.rainy_day_scope(cfg, [day])
    assert set(got) == {day} and "seal" not in kinds(cfg)


def test_95_a_spurious_correlation_is_a_forecast(cfg):
    assert ideas.spurious(cfg, "ci-red-when-it-rains-nov")["code"] == 0


def test_102_the_spy_half_is_a_market_call(cfg):
    box = dataclasses.replace(cfg, beacon=Clock())
    got = ideas.bot_vs_market(box, DAY, 0.6)
    assert got["code"] == 0
    (w,) = [r for r in rows(box) if r["kind"] == "wager"]
    assert w["market"] == {"symbol": "SPY", "kind": "up", "close_date": DAY}
    assert 102 in ideas.BLOCKED  # the bot's half has no field


# ── the loose chunks ────────────────────────────────────────────────────────
from onescript import xref  # noqa: E402
from onescript.tests.test_xref import make  # noqa: E402


@pytest.fixture
def two_repos(tmp_path):
    a = make(
        tmp_path,
        "docs",
        {
            "notes.md": (
                '# Notes\n\nThe operator, 2026-10-19: "build it, leaning towards the bot"\n'
                '**The operator:** "one seal per session"\n'
                'the operator\'s word: *"merge only what must be identical"*\n'
                'is the operator box? "not a quote"\n\n'
                + "\n\n".join(
                    f"Paragraph {i} about the pile and its boxes." for i in range(6)
                )
            )
        },
    )
    b = make(tmp_path, "code", {"run.py": "\n\n".join(
        f"def step_{i}():\n    return {i}" for i in range(6)
    )})  # fmt: skip
    return xref.build([("docs", a), ("code", b)]), {"docs": a, "code": b}


def test_1_quotes_are_harvested_as_hashes(two_repos):
    index, repos = two_repos
    dated, *undated = ideas.said(index, repos)
    assert dated["date"] == "2026-10-19" and dated["where"] == "docs:notes.md:3"
    assert [q["where"] for q in undated] == ["docs:notes.md:4", "docs:notes.md:5"]
    assert all(q["date"] is None for q in undated)
    got = json.dumps([dated, *undated])
    assert "bot" not in got and "seal" not in got  # hashes, never words


def test_7_serendipity_picks_one_chunk_from_each_of_two_repos(two_repos):
    index, repos = two_repos
    got = ideas.serendipity(index, repos, RANDOMNESS)
    assert got["state"] == POPULATED and got == ideas.serendipity(
        index, repos, RANDOMNESS
    )
    assert {p["where"].split(":")[0] for p in got["pair"]} == {"docs", "code"}
    assert 7 not in ideas.BLOCKED  # the loop is built: onescript/draw.py


def test_11_sessions_end_sealed_or_waiting():
    rows_ = [
        {"n": 0, "kind": "invocation", "argv": ["checkin"]},
        {"n": 1, "kind": "session", "from": 0, "subject": "session:" + "a" * 64},
        {"n": 2, "kind": "invocation", "argv": ["checkin"]},
        {"n": 3, "kind": "session", "from": 2, "subject": "session:" + "b" * 64},
    ]
    got = ideas.how_sessions_end(rows_, held={"session:" + "a" * 64})
    assert {p["ending"]: p["n"] for p in got["pools"]} == {"sealed": 1, "waiting": 1}
    assert ideas.how_sessions_end([], set())["state"] == "empty"


def test_36_you_and_the_crowd_are_two_wagers(cfg):
    web = Web({"https://api.manifold.markets/": b'{"probability": 0.27}'})
    got = ideas.against_the_crowd(dataclasses.replace(cfg, fetch=web), "Abc1", 0.6)
    assert got["state"] == POPULATED and got["p"] == 0.27
    by = sorted(r["by"] for r in rows(cfg) if r["kind"] == "wager")
    assert by == ["human", "oracle"]


def test_40_dialect_says_unreachable_without_wordfreq():
    class Store:
        n_all = 100

        def df(self, w):
            return {"pile": 40, "seal": 10}.get(w, 0)

    got = ideas.dialect(Store(), ["seal", "pile"])
    assert got["mine"] == {"pile": 0.4, "seal": 0.1}
    assert got["state"] in (POPULATED, UNREACHABLE)  # wordfreq is optional


CSV = "Entity,Code,Year,Population\nNigeria,NGA,2020,208327405\nNigeria,NGA,2021,213401323\n"


def test_47_the_quiz_reads_the_answer_exactly(cfg):
    assert str(ideas.answer(CSV, "Nigeria", 2020)) == "208327405"
    assert ideas.answer(CSV, "Chad", 2020) is None
    box = dataclasses.replace(
        cfg, fetch=Web({"https://ourworldindata.org/": CSV.encode()})
    )
    got = ideas.quiz(box, "population", "Nigeria", 2021, 2e8, 2.2e8)
    assert got["hit"] is True and got["answer"] == "213401323"


def test_59_the_flood_guard_drops_a_rare_name_traded_for_a_common_word():
    seed_answer = {
        "keys": [{"chunks": 4}, {"chunks": 6}, {"chunks": 12}],
        "repair": {"repairs": [["gource", "source", 5850], ["tracery", "tracer", 1]]},
    }
    got = ideas.guard_repairs(seed_answer, flood=10)
    assert got["median_chunks"] == 6
    assert got["dropped"] == [["gource", "source", 5850]]
    assert got["kept"] == [["tracery", "tracer", 1]]


def test_62_no_repo_takes_more_than_its_share():
    rank = [{"repo": "jeles"}] * 8 + [{"repo": "bot"}] * 3
    got = ideas.cap_per_repo(rank, n=6, most=3)
    assert [h["repo"] for h in got] == ["jeles"] * 3 + ["bot"] * 3


def test_63_and_64_the_ids_the_pile_already_has():
    assert ideas.swhid("ab" * 20, "https://x/y", "a.py", 3, 9) == (
        f"swh:1:cnt:{'ab' * 20};origin=https://x/y;path=/a.py;lines=3-9"
    )
    sha = hashlib.sha256(b"hello world\n").hexdigest()
    assert (
        ideas.cid(sha) == "bafkreifjjcie6lypi6ny7amxnfftagclbuxndqonfipmb64f2km2devei4"
    )


def test_86_snippets_are_same_near_or_apart():
    a = "the pile counts words and the ladder hashes a bite into a key"
    assert ideas.relate(a, a)["rel"] == "same"
    near = ideas.relate(a, a.replace("ladder", "rung"))
    assert near["rel"] == "near" and any(x.startswith("+") for x in near["diff"])
    assert ideas.relate(a, "chess puzzles of the day from lichess")["rel"] == "apart"


def test_96_the_record_as_a_gource_log():
    got = ideas.gource_log(
        [
            {"n": 0, "kind": "boot", "who": "run", "ts": "2026-10-21T22:32:00Z"},
            {"n": 1, "kind": "seal", "who": "human", "ts": "not a time"},
        ]
    )
    t = int(datetime(2026, 10, 21, 22, 32, tzinfo=timezone.utc).timestamp())
    assert got == [f"{t}|run|A|boot/0"]


def test_97_the_pile_writes_a_provable_poem():
    pairs = ideas.strongest_pairs({"pairs": {"sealed scope": 0.9, "served table": 0.7}})
    assert pairs == ["sealed scope", "served table"]
    one = ideas.poem(pairs, RANDOMNESS)
    assert len(one) == 3 and one == ideas.poem(pairs, RANDOMNESS)
    assert ideas.poem(["alone"], RANDOMNESS) == []
