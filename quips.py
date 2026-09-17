"""
quips.py — Voice engine for willow-bot.
b17: WBQP1  ΔΣ=42
"""
import json
import os
import random
import sqlite3
from pathlib import Path

import runes

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


def _with_rune(event: str, sha: str, line: str) -> str:
    if event == "pr_opened" and sha:
        return f"{line}\n\n{runes.cast(sha)}" if line else runes.cast(sha)
    return line


def pick(event: str, login: str = "", sha: str = "") -> str:
    cfg = _cfg()

    if os.getenv("FRANK_MODE"):
        return _with_rune(event, sha, _frank(event, login))

    if os.getenv("PROPHET_MODE"):
        return _prophet(event, login)

    chaos_prob = cfg.get("chaos", {}).get(event, 0.0)
    if chaos_prob and random.random() < chaos_prob:
        lines = cfg.get("chaos_lines", ["sure"])
        return _with_rune(event, sha, random.choice(lines))

    lines = cfg.get("voice", {}).get(event, [])
    if not lines:
        return _with_rune(event, sha, "")

    line = random.choice(lines)

    if login and event in ("pr_merged", "pr_opened", "first_contribution"):
        title = get_title(login)
        line = f"**{title.capitalize()} {login}** — {line}"

    return _with_rune(event, sha, line)


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


def _prophet(event: str, login: str) -> str:
    templates = {
        "pr_merged":    "PROPHET foresees this merge will echo through seven generations. The lineage of the tree is now unbroken. Rejoice.",
        "pr_opened":    "PROPHET beholds this pull request and sees greatness unfolding. The reviewers do not know it yet, but they are blessed.",
        "ci_pass":      "PROPHET declares the tests have spoken in tongues of green. This is a sign. All future builds shall know this glory.",
        "ci_fail":      "PROPHET sees this failure as the seed of a greater triumph. The tests suffer now so that future tests may know peace.",
        "push_to_main": "PROPHET blesses this direct push. The main branch has been favored. It shall not know regret.",
        "new_fork":     "PROPHET witnesses a new fork and sees a thousand futures branching from this single moment. Some will flourish.",
        "gap_filed":    "PROPHET reads this issue as prophecy fulfilling itself. It was always meant to be filed. The queue rejoices quietly.",
    }
    return templates.get(event, f"PROPHET beholds an event of type '{event}' and finds it auspicious.")
