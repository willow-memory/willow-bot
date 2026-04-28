"""
quips.py — Voice engine for willow-bot.
b17: WBQP1  ΔΣ=42
"""
import json
import os
import random
import sqlite3
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "willow-bot.json"
_DB_PATH = Path.home() / ".willow" / "willow-bot-contributors.db"

_config: dict = {}


def load_config(path: Path = _CONFIG_PATH) -> None:
    global _config
    _config = json.loads(path.read_text())


def _cfg() -> dict:
    if not _config:
        load_config()
    return _config


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contributors (
            login TEXT PRIMARY KEY,
            merged_prs INTEGER DEFAULT 0,
            first_seen TEXT
        )
    """)
    conn.commit()
    return conn


def get_title(login: str) -> str:
    conn = _init_db()
    row = conn.execute("SELECT merged_prs FROM contributors WHERE login = ?", (login,)).fetchone()
    conn.close()
    count = row[0] if row else 0
    titles = _cfg().get("titles", {})
    for title, bounds in reversed(list(titles.items())):
        if count >= bounds["min_prs"]:
            return title
    return "thrall"


def record_merge(login: str) -> str:
    conn = _init_db()
    conn.execute("""
        INSERT INTO contributors (login, merged_prs, first_seen)
        VALUES (?, 1, datetime('now'))
        ON CONFLICT(login) DO UPDATE SET merged_prs = merged_prs + 1
    """, (login,))
    conn.commit()
    conn.close()
    return get_title(login)


def is_first_contribution(login: str) -> bool:
    conn = _init_db()
    row = conn.execute("SELECT merged_prs FROM contributors WHERE login = ?", (login,)).fetchone()
    conn.close()
    return row is None or row[0] == 0


def pick(event: str, login: str = "") -> str:
    cfg = _cfg()

    if os.getenv("FRANK_MODE"):
        return _frank(event, login)

    chaos_prob = cfg.get("chaos", {}).get(event, 0.0)
    if chaos_prob and random.random() < chaos_prob:
        lines = cfg.get("chaos_lines", ["sure"])
        return random.choice(lines)

    lines = cfg.get("voice", {}).get(event, [])
    if not lines:
        return ""

    line = random.choice(lines)

    if login and event in ("pr_merged", "pr_opened", "first_contribution"):
        title = get_title(login)
        line = f"**{title.capitalize()} {login}** — {line}"

    return line


def _frank(event: str, login: str) -> str:
    templates = {
        "pr_merged":    "FRANK notes this pull request has been merged. A completion report has been filed. It has been acknowledged. This has never happened before.",
        "pr_opened":    "FRANK notes a new pull request has been opened. FRANK has not read it yet. FRANK is logging this as a known gap.",
        "ci_pass":      "FRANK notes the automated test suite has returned a passing result. FRANK remains cautious. The tests have been wrong before.",
        "ci_fail":      "FRANK notes the automated test suite has returned a failing result. FRANK is not surprised. FRANK has filed three prior warnings about this.",
        "push_to_main": "FRANK notes a direct push to the main branch. No review. No comment. FRANK has logged it. FRANK did not say anything else.",
        "new_fork":     "FRANK notes this repository has been forked. The lineage continues. FRANK has updated the family tree.",
        "gap_filed":    "FRANK notes a new issue has been filed. It joins the queue. The queue is aware of it.",
    }
    return templates.get(event, f"FRANK notes an event of type '{event}'. It has been logged.")
