"""The voice's counters live in the box; ~/.willow is carried over once.

The operator's calls (2026-09-28): no fallback box, and carry the counts
over. `persona_store._legacy_dir` stands in for the real ~/.willow (the
test floor points it at an empty dir under tmp_path; tests here fill it).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import quips
import rebase_shame
import sigh
from willow_bot import paths, persona_store


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    d = tmp_path / "old-dot-willow"
    d.mkdir()
    monkeypatch.setattr(persona_store, "_legacy_dir", lambda: d)
    return d


def _make_contributors(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE contributors (login TEXT PRIMARY KEY, merged_prs INTEGER DEFAULT 0, first_seen TEXT)")
    conn.executemany("INSERT INTO contributors VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _migrations():
    p = paths.persona_dir() / "migrations.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def test_counters_live_in_the_box(legacy):
    assert sigh.bump_fail("o/r", 1) == 1
    assert rebase_shame.increment("o/r", "feat") == 1
    quips.record_merge("someone")
    box = paths.persona_dir()
    assert box == paths.willow_home() / "willow-bot" / "persona"
    assert sorted(p.name for p in box.glob("*.db")) == sorted(persona_store.FILES.values())
    assert _migrations() == []  # nothing to carry over


def test_counts_are_carried_over_once_and_the_old_file_is_untouched(legacy):
    old = legacy / "willow-bot-contributors.db"
    _make_contributors(old, [("rudi193-cmd", 57, "2026-07-01")])
    before = old.read_bytes()

    target = persona_store.db_path("contributors")
    assert target == paths.persona_dir() / "willow-bot-contributors.db"
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT merged_prs FROM contributors WHERE login='rudi193-cmd'").fetchone() == (57,)
    conn.close()
    assert old.read_bytes() == before
    [line] = _migrations()
    assert line["status"] == "copied" and line["counter"] == "contributors"
    assert line["from"] == str(old) and line["to"] == str(target)

    # The box copy is now the counter; the old file is never read again.
    _make_contributors(legacy / "other.db", [])
    old.unlink()
    _make_contributors(old, [("rudi193-cmd", 1, "x")])
    assert persona_store.db_path("contributors") == target
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT merged_prs FROM contributors").fetchone() == (57,)
    conn.close()
    assert len(_migrations()) == 1


def test_a_carried_over_title_survives_through_quips(legacy):
    _make_contributors(legacy / "willow-bot-contributors.db", [("veteran", 40, "2026-07-01")])
    quips.record_merge("veteran")
    conn = sqlite3.connect(persona_store.db_path("contributors"))
    assert conn.execute("SELECT merged_prs FROM contributors WHERE login='veteran'").fetchone() == (41,)
    conn.close()


def test_each_counter_carries_over_its_own_file(legacy):
    conn = sqlite3.connect(legacy / "willow-bot-sigh.db")
    conn.execute("CREATE TABLE ci_fail_streaks (repo TEXT, pr_number INTEGER, streak INTEGER, "
                 "PRIMARY KEY (repo, pr_number))")
    conn.execute("INSERT INTO ci_fail_streaks VALUES ('o/r', 7, 4)")
    conn.commit()
    conn.close()
    assert sigh.bump_fail("o/r", 7) == 5
    assert rebase_shame.increment("o/r", "main") == 1  # no old file: starts fresh
    assert [m["counter"] for m in _migrations()] == ["sigh"]


def test_an_unreadable_old_file_is_recorded_and_the_counter_starts_in_the_box(legacy):
    (legacy / "willow-bot-rebase-shame.db").write_bytes(b"not a sqlite file at all" * 10)
    assert rebase_shame.increment("o/r", "feat") == 1
    [line] = _migrations()
    assert line["status"] == "failed" and line["counter"] == "rebase_shame"
    assert not any(p.name.endswith(".carry") for p in paths.persona_dir().iterdir())


def test_no_box_refuses_instead_of_writing_anywhere(legacy, monkeypatch):
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    with pytest.raises(paths.BoxNotConfigured):
        sigh.bump_fail("o/r", 1)
    with pytest.raises(paths.BoxNotConfigured):
        quips.record_merge("someone")


def test_the_test_floor_never_points_at_the_real_dot_willow():
    assert persona_store._legacy_dir() != Path.home() / ".willow"


def test_status_with_no_box_says_so_and_names_the_fix(monkeypatch):
    from willow_bot import status

    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    r = status.report()
    assert r["status"] == "no-box" and r["willow_home"] is None
    assert "provision.sh" in r["detail"]
