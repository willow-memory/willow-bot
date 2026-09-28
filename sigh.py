"""
sigh.py — Sighing NPC. Tracks consecutive CI failures per PR.
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
    conn.commit()
    return conn


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
