"""The compiled ideas run (one-script/compiled/ideas.py; IDEAS.md).

Every idea snippet_cover called `compiled` is run here against a real box,
with offline stand-ins for the drand reader, the price reader and the
fetcher. Each forecast or verdict it writes must stand on the record; with
no reader, each outside read is `unreachable`, said.
"""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
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
