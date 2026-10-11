"""
sigh.py — Sighing NPC. Tracks consecutive CI failures per PR.

A failure is a red COMMIT, not a red check leg: a push whose three matrix
jobs fail is one red, and a second failing leg of the same head counts
nothing more. A green leg ends the streak only when it is on a different
head from the last red one; a green lint leg beside a red test leg on the
same commit is not a recovery.
"""
import sqlite3
from pathlib import Path

# The counter lives in the box (willow_bot.persona_store), not ~/.willow.
# Every function takes an explicit path for tests; None means the box.


def _db(path: Path | None) -> Path:
    if path is not None:
        return path
    from willow_bot import persona_store

    return persona_store.db_path("sigh")


def _init_db(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ci_fail_streaks (
            repo TEXT,
            pr_number INTEGER,
            streak INTEGER,
            PRIMARY KEY (repo, pr_number)
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ci_fail_streaks)")}
    if "last_head" not in cols:  # a table from before the per-head count
        conn.execute("ALTER TABLE ci_fail_streaks ADD COLUMN last_head TEXT")
    conn.commit()
    return conn


def fail(repo: str, pr_number: int, head: str, path: Path | None = None) -> int:
    """A red leg on ``head``. Counts the head once; returns the streak."""
    conn = _init_db(path)
    row = conn.execute(
        "SELECT streak, last_head FROM ci_fail_streaks WHERE repo = ? AND pr_number = ?",
        (repo, pr_number),
    ).fetchone()
    streak, last = (int(row[0] or 0), row[1]) if row else (0, None)
    if head and head == last:
        conn.close()
        return streak
    streak += 1
    conn.execute("""
        INSERT INTO ci_fail_streaks (repo, pr_number, streak, last_head)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repo, pr_number) DO UPDATE SET streak = excluded.streak, last_head = excluded.last_head
    """, (repo, pr_number, streak, head))
    conn.commit()
    conn.close()
    return streak


def recover(repo: str, pr_number: int, head: str, path: Path | None = None) -> int:
    """A green leg on ``head``. On the head that last went red it is not a
    recovery (the commit still has a red leg) and returns 0. On another head
    it ends the streak and returns how many red commits it ended."""
    conn = _init_db(path)
    row = conn.execute(
        "SELECT streak, last_head FROM ci_fail_streaks WHERE repo = ? AND pr_number = ?",
        (repo, pr_number),
    ).fetchone()
    if not row or not row[0] or (head and head == row[1]):
        conn.close()
        return 0
    conn.execute("UPDATE ci_fail_streaks SET streak = 0 WHERE repo = ? AND pr_number = ?",
                 (repo, pr_number))
    conn.commit()
    conn.close()
    return int(row[0])


def bump_fail(repo: str, pr_number: int, path: Path | None = None) -> int:
    conn = _init_db(path)
    conn.execute("""
        INSERT INTO ci_fail_streaks (repo, pr_number, streak)
        VALUES (?, ?, 1)
        ON CONFLICT(repo, pr_number) DO UPDATE SET streak = streak + 1
    """, (repo, pr_number))
    conn.commit()
    row = conn.execute(
        "SELECT streak FROM ci_fail_streaks WHERE repo = ? AND pr_number = ?",
        (repo, pr_number),
    ).fetchone()
    conn.close()
    return row[0]


def get(repo: str, pr_number: int, path: Path | None = None) -> int:
    """The current failure streak for this PR (0 when none is recorded)."""
    conn = _init_db(path)
    row = conn.execute(
        "SELECT streak FROM ci_fail_streaks WHERE repo = ? AND pr_number = ?",
        (repo, pr_number),
    ).fetchone()
    conn.close()
    return int(row[0]) if row else 0


def reset(repo: str, pr_number: int, path: Path | None = None) -> None:
    conn = _init_db(path)
    conn.execute("""
        INSERT INTO ci_fail_streaks (repo, pr_number, streak)
        VALUES (?, ?, 0)
        ON CONFLICT(repo, pr_number) DO UPDATE SET streak = 0
    """, (repo, pr_number))
    conn.commit()
    conn.close()


def sigh_line(streak: int) -> str | None:
    if streak < 3:
        return None
    return "sigh" + "h" * (streak - 3)
