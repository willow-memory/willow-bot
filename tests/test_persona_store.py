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


# ── Loki A726C6F8 ────────────────────────────────────────────────────────────

def test_the_real_carry_over_source_is_home_dot_willow(monkeypatch, tmp_path):
    """F3/M20: every other test patches the source; this pins the real one."""
    real = persona_store._legacy_dir.__wrapped__
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "someone"))
    assert real() == tmp_path / "someone" / ".willow"


def test_a_box_that_does_not_exist_is_refused_and_nothing_is_created(monkeypatch, tmp_path):
    """F2: a WILLOW_HOME pointing nowhere is no box — the first write must
    not build a tree under it, and status must not read it as empty."""
    from willow_bot import status

    ghost = tmp_path / "not-provisioned"
    monkeypatch.setenv("WILLOW_HOME", str(ghost))
    with pytest.raises(paths.BoxNotConfigured, match="not an existing directory"):
        sigh.bump_fail("o/r", 1)
    assert not ghost.exists()
    r = status.report()
    assert r["status"] == "no-box" and str(ghost) in r["detail"]


def test_vault_only_env_is_the_box(monkeypatch, tmp_path):
    """M04."""
    box = tmp_path / "vault-only"
    box.mkdir()
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(box))
    assert paths.willow_home() == box
    assert persona_store.db_path("sigh") == box / "willow-bot" / "persona" / "willow-bot-sigh.db"


def test_a_copy_that_fails_before_the_rename_leaves_nothing(legacy, monkeypatch):
    """M09: the copy lands in a temp file and is renamed into place; a
    failure before the rename leaves no target and no temp behind."""
    _make_contributors(legacy / "willow-bot-contributors.db", [("veteran", 40, "x")])
    real_replace = Path.replace

    def _refuse(self, target):
        if str(self).endswith(".carry"):
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _refuse)
    target = persona_store.db_path("contributors")
    assert not target.exists()
    assert [p.name for p in paths.persona_dir().iterdir() if p.name.endswith(".carry")] == []
    [line] = _migrations()
    assert line["status"] == "failed" and "disk full" in line["error"]


def test_first_open_rechecks_under_the_lock(legacy, monkeypatch):
    """F4: a second process that finished the copy (and bumped the count)
    while this one waited for the lock is not overwritten."""
    _make_contributors(legacy / "willow-bot-contributors.db", [("veteran", 40, "x")])
    target = paths.persona_dir() / "willow-bot-contributors.db"
    real_flock = persona_store.fcntl.flock

    def _other_process_won(fd, op):
        real_flock(fd, op)
        if op == persona_store.fcntl.LOCK_EX and not target.exists():
            _make_contributors(target, [("veteran", 41, "x")])

    monkeypatch.setattr(persona_store.fcntl, "flock", _other_process_won)
    assert persona_store.db_path("contributors") == target
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT merged_prs FROM contributors").fetchone() == (41,)
    conn.close()
    assert _migrations() == []


def test_install_service_refuses_without_a_box(tmp_path):
    """M21."""
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in ("WILLOW_HOME", "WILLOW_BOT_VAULT_BOX")}
    r = subprocess.run(["bash", str(root / "scripts" / "install-service.sh"), "--print", "willow-bot"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "no box" in r.stderr and "provision.sh" in r.stderr
    env["WILLOW_BOT_VAULT_BOX"] = str(tmp_path)
    r = subprocess.run(["bash", str(root / "scripts" / "install-service.sh"), "--print", "willow-bot"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and f"WILLOW_VAULT_BOX={tmp_path}" in r.stdout
