"""
sigh.py — Sighing NPC. Tracks consecutive CI failures per PR.
"""
import sqlite3
from pathlib import Path

_DB_PATH = Path.home() / ".willow" / "willow-bot-sigh.db"


def _init_db(path: Path = _DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
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


def bump_fail(repo: str, pr_number: int, path: Path = _DB_PATH) -> int:
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


def reset(repo: str, pr_number: int, path: Path = _DB_PATH) -> None:
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
