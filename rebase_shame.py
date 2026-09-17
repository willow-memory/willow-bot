"""
rebase_shame.py — tracks force-pushes per (repo, ref) and shames accordingly.

Keyed by (repo, ref) rather than PR number: a `push` webhook event carries
`forced` and `ref`, never a PR number. Callers that need this per-PR look up
the PR's head ref (`pr["head"]["ref"]`) and use that as the key.
"""
import sqlite3
from pathlib import Path

_DB_PATH = Path.home() / ".willow" / "willow-bot-rebase-shame.db"

_WORDS = {
    3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}


def _init_db(path: Path = _DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rebase_counts (
            repo TEXT,
            ref TEXT,
            count INTEGER,
            PRIMARY KEY (repo, ref)
        )
    """)
    conn.commit()
    return conn


def increment(repo: str, ref: str, path: Path = _DB_PATH) -> int:
    conn = _init_db(path)
    conn.execute("""
        INSERT INTO rebase_counts (repo, ref, count)
        VALUES (?, ?, 1)
        ON CONFLICT(repo, ref) DO UPDATE SET count = count + 1
    """, (repo, ref))
    conn.commit()
    row = conn.execute(
        "SELECT count FROM rebase_counts WHERE repo = ? AND ref = ?", (repo, ref)
    ).fetchone()
    conn.close()
    return row[0]


def get(repo: str, ref: str, path: Path = _DB_PATH) -> int:
    conn = _init_db(path)
    row = conn.execute(
        "SELECT count FROM rebase_counts WHERE repo = ? AND ref = ?", (repo, ref)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def format_number(n: int) -> str:
    return _WORDS.get(n, str(n))


def header(count: int) -> str:
    if count < 3:
        return ""
    n = format_number(count)
    if count < 7:
        line = f"the branch has been rewritten {n} times"
    elif count < 10:
        line = "the branch remembers none of its former selves"
    else:
        line = "the branch is no longer what it was"
    return f"[REBASE-COUNT: {count} — {line}]"
