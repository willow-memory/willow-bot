# ruff: noqa: F811, F401
"""R13: what a seat reads. `api.needs` (and the morning screen's TABLE section)
carry the wagers and the table's verdict sessions and agm_head check as lines.

SPEC.md ruling R13 and SPEC-documented phase J8-table-panel. No new panel: the
lines are the data a seat's `item_box` lays out. Three states, never collapsed
(INVARIANTS section 1): a wager is `reconciled`, `escalate` or `unreachable`; the
agm_head check is `satisfied`, `failing`, `unreachable` or `unanchored`.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_forecast_lane import (  # noqa: E402,F401
    YES_NO,
    key_file,
    proof,
    seal_out,
    seal_tip,
)

from test_forecast_lane import cfg as _plain_cfg  # noqa: E402

from onescript import api, table, view  # noqa: E402


@pytest.fixture
def cfg(_plain_cfg):
    """The forecast lane's box, with the `table` identity in its keys file."""
    keys = json.loads(_plain_cfg.keys.read_text())
    keys["table"] = "bb" * 32
    _plain_cfg.keys.write_text(json.dumps(keys))
    return _plain_cfg


def roll(cfg, turn, total, forecast=None, session="s1"):
    v = "strong" if total >= 10 else "weak" if total >= 7 else "miss"
    row = {
        "what": "roll",
        "session": session,
        "where": session,
        "turn": turn,
        "total": total,
        "verdict": v,
        "reason": f"2d6+2 = {total}",
    }
    if forecast:
        row["forecast"] = forecast
    res = table.verdict(cfg, row)
    assert res["code"] == 0, res
    return res


def lines(cfg, key_file=None, read_head=None):
    got = api.needs(cfg, human_key=key_file, read_head=read_head)
    assert got["code"] == 0, got
    return got["needs"]


def pick(ls, start):
    return [x for x in ls if x.startswith(start)]


def head_reader(n=3, head="abc123", state="read"):
    return lambda: {"state": state, "n": n, "head": head, "why": "x", "db_sha": None}


# ── wagers: reconciled / escalate / unreachable ──────────────────────────────
def test_a_box_with_no_wager_and_no_table_row_gains_no_line(cfg, key_file):
    api.checkin(cfg)
    ls = lines(cfg, key_file)
    assert not pick(ls, "wager") and not pick(ls, "table")


def test_a_forecast_waiting_on_an_outcome_shows_as_a_wager_line(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO, by="oracle")
    seal_tip(cfg, f["tip"])
    (w,) = pick(lines(cfg, key_file), "wager f1")
    assert (
        w.startswith("wager f1 escalate to human · ")
        and "the grade is the human's" in w
    )


def test_a_sealed_outcome_shows_reconciled_with_code_s_score_only_with_the_key(
    cfg, key_file
):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO, by="oracle")
    seal_tip(cfg, f["tip"])
    s = seal_out(cfg, f["subject"], {"path_index": 0})
    seal_tip(cfg, s["tip"])
    (w,) = pick(lines(cfg, key_file), "wager f1")
    assert w.startswith("wager f1 reconciled · ") and "brier 0.144" in w
    assert w.endswith("grade is yours") and "(claimed)" in w
    (nokey,) = pick(lines(cfg), "wager f1")  # no key: nothing counts
    assert nokey.startswith("wager f1 escalate to human")


def test_a_seal_that_cannot_be_checked_here_shows_unreachable(cfg, key_file, tmp_path):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    s = seal_out(cfg, f["subject"], {"path_index": 0})
    seal_tip(cfg, s["tip"])
    keys = json.loads(cfg.keys.read_text())
    del keys["human"]
    k2 = tmp_path / "k2" / "keys.json"
    k2.parent.mkdir(mode=0o700)
    k2.write_text(json.dumps(keys))
    k2.chmod(0o600)
    (w,) = pick(lines(dataclasses.replace(cfg, keys=k2)), "wager f1")
    assert w.startswith("wager f1 seal unreachable · ")


def test_a_broken_record_is_said_in_the_wager_line(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    text = (cfg.box / "record.jsonl").read_text()
    (cfg.box / "record.jsonl").write_text(text.replace('"p":0.62', '"p":0.61', 1))
    (w,) = pick(lines(cfg, key_file), "wager f1")
    assert "the record is broken" in w


def test_needs_refuses_a_human_key_inside_the_box(cfg):
    api.checkin(cfg)
    inside = cfg.box / "key.hex"
    inside.write_text("aa")
    inside.chmod(0o600)
    got = api.needs(cfg, human_key=inside)
    assert "inside the box" in got["refused"] and got["code"] == 2


def test_morning_still_prints_each_wager_once_and_adds_a_table_section(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    roll(cfg, 0, 9, f["subject"])
    out = api.checkout(cfg, human_key=key_file, read_head=head_reader())["screen"]
    assert out.count("wager f1") == 1
    assert "TABLE\n  · table verdict session s1 · 1 rows" in out


# ── the table: sessions and the agm_head check ───────────────────────────────
def test_a_verdict_session_names_its_forecast_and_whether_a_tip_covers_it(
    cfg, key_file
):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    roll(cfg, 0, 9, f["subject"])
    roll(cfg, 1, 11, f["subject"])
    (s,) = pick(lines(cfg, key_file), "table verdict session")
    assert s == (
        "table verdict session s1 · 2 rows · forecast f1 · waiting on your tip seal"
    )
    (blind,) = pick(lines(cfg), "table verdict session")
    assert blind.endswith("tip seal not checked here (no human key)")
    tip = api.seal_tip(cfg, _tip(cfg))["covers"]
    assert [c["what"] for c in tip][-1] == "verdict session"
    assert seal_tip(cfg, _tip(cfg))["sealed"]
    (done,) = pick(lines(cfg, key_file), "table verdict session")
    assert done.endswith("forecast f1 · covered by your sealed tip")


def _tip(cfg):
    from onescript import record

    last = [json.loads(x) for x in (cfg.box / "record.jsonl").read_text().splitlines()][
        -1
    ]
    return record.tip_subject(last)


def test_the_agm_head_check_has_four_states_never_collapsed(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    roll(cfg, 0, 9, f["subject"])
    (a,) = pick(lines(cfg, key_file, head_reader()), "table agm_head")
    assert a.startswith("table agm_head unanchored · no agm_head row on record")
    assert table.agm_head(cfg, n=3, head="abc123")["code"] == 0
    seal_tip(cfg, _tip(cfg))
    (ok,) = pick(lines(cfg, key_file, head_reader()), "table agm_head")
    assert ok == "table agm_head satisfied · ledger n 3 · covered by your sealed tip"
    (bad,) = pick(lines(cfg, key_file, head_reader(head="zzz")), "table agm_head")
    assert bad.startswith("table agm_head failing · the ledger is at (3, zzz)")
    (cut,) = pick(lines(cfg, key_file, head_reader(n=2)), "table agm_head")
    assert "fewer rows than anchored (2 < 3)" in cut
    (gone,) = pick(
        lines(cfg, key_file, lambda: {"state": "unreachable", "why": "db absent"}),
        "table agm_head",
    )
    assert gone.startswith("table agm_head unreachable · db absent")
    (none,) = pick(lines(cfg, key_file), "table agm_head")  # no reader handed over
    assert none.startswith("table agm_head unreachable · no ledger reader")


def test_a_reader_that_raises_is_unreachable_not_a_crash(cfg, key_file):
    api.checkin(cfg)
    roll(cfg, 0, 9)
    table.agm_head(cfg, n=3, head="abc")

    def boom():
        raise OSError("disk gone")

    (a,) = pick(lines(cfg, key_file, boom), "table agm_head")
    assert a.startswith("table agm_head unreachable · OSError: disk gone")


def test_the_lines_carry_no_key_and_no_seed(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    roll(cfg, 0, 9, f["subject"])
    text = "\n".join(lines(cfg, key_file, head_reader()))
    assert key_file.read_text().strip() not in text and "seed" not in text


def test_view_table_lines_are_pure_over_the_report():
    assert view.table_lines({}) == [] and view.table_lines({"table": None}) == []
