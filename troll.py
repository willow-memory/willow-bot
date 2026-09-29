"""
troll.py — the bot's voice: a weird little troll under the bridge that counts.

Every line it says carries a real fact from the event (how big, how long,
how many reds, who reviewed), picked from the pool that fits the situation
most specifically, and never repeated in a repo until that pool is spent.
Deterministic: no model. Pools live in willow-bot.json under "troll", so the
voice is edited there, not here.

    situation   the most specific tag that fits (huge, speedrun, revert, ...)
    pool        troll.<event>.<situation> — falls back to troll.<event>.default
    slots       {size} {files} {days} {hours} {reds} {reviewer} {login}
    memory      $WILLOW_HOME/willow-bot/persona/willow-bot-voice.db
    guests      FRANK / PROPHET cameo on ~1 in 20 events, seeded by the sha
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import quips

_CONFIG_PATH = Path(__file__).parent / "willow-bot.json"
_config: dict = {}

#: Thresholds that turn facts into situations.
HUGE_LINES = 800
TINY_LINES = 5
SPEEDRUN_HOURS = 1.0
MARATHON_DAYS = 14.0
RECOVERY_REDS = 3
GUEST_ONE_IN = 20


def _cfg() -> dict:
    global _config
    if not _config:
        _config = json.loads(_CONFIG_PATH.read_text())
    return _config


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── facts: what the payload actually says ────────────────────────────────────

def pr_facts(pr: dict, *, now: datetime | None = None) -> dict:
    """The countable facts about a pull request, from the webhook payload
    alone (no API call). Missing fields are simply absent from the result."""
    user = pr.get("user") or {}
    login = str(user.get("login") or "")
    add, rem = pr.get("additions"), pr.get("deletions")
    facts: dict = {
        "login": login,
        "bot_author": user.get("type") == "Bot",
        "dependabot": login.startswith("dependabot"),
        "release": str((pr.get("head") or {}).get("ref") or "").startswith("release-please"),
        "revert": str(pr.get("title") or "").lower().startswith("revert"),
        "draft": bool(pr.get("draft")),
    }
    if isinstance(add, int) and isinstance(rem, int):
        facts["size"] = add + rem
    if isinstance(pr.get("changed_files"), int):
        facts["files"] = pr["changed_files"]
    opened = _when(pr.get("created_at"))
    ended = _when(pr.get("merged_at")) or _when(pr.get("closed_at")) or now or datetime.now(timezone.utc)
    if opened:
        hours = max((ended - opened).total_seconds() / 3600.0, 0.0)
        facts["hours"] = round(hours, 1)
        facts["days"] = int(hours // 24)
    stamp = ended if (pr.get("merged_at") or pr.get("closed_at")) else (now or datetime.now(timezone.utc))
    facts["weekend"] = stamp.weekday() >= 5
    return facts


def situations(event: str, facts: dict) -> list[str]:
    """Situation tags for ``event`` given ``facts``, most specific first.
    ``default`` is always last."""
    tags: list[str] = []
    if facts.get("release"):
        tags.append("release")
    if facts.get("dependabot"):
        tags.append("dependabot")
    elif facts.get("bot_author"):
        tags.append("bot")
    if facts.get("revert"):
        tags.append("revert")
    if event == "pr_opened" and facts.get("draft"):
        tags.append("draft")
    size = facts.get("size")
    if isinstance(size, int):
        if size >= HUGE_LINES:
            tags.append("huge")
        elif size <= TINY_LINES:
            tags.append("tiny")
    if event == "pr_merged" and isinstance(facts.get("hours"), (int, float)):
        if facts["hours"] < SPEEDRUN_HOURS:
            tags.append("speedrun")
        elif facts["hours"] >= MARATHON_DAYS * 24:
            tags.append("marathon")
    if facts.get("weekend") and event in ("pr_merged", "pr_opened"):
        tags.append("weekend")
    tags.append("default")
    return tags


# ── memory: no line twice until its pool is spent ────────────────────────────

def _memory() -> sqlite3.Connection:
    from willow_bot import persona_store

    conn = sqlite3.connect(persona_store.db_path("voice"))
    conn.execute("CREATE TABLE IF NOT EXISTS used (repo TEXT, pool TEXT, line TEXT, "
                 "PRIMARY KEY (repo, pool, line))")
    return conn


def _pick(repo: str, pool: str, lines: list[str], rng: random.Random) -> str:
    conn = _memory()
    try:
        used = {r[0] for r in conn.execute("SELECT line FROM used WHERE repo=? AND pool=?", (repo, pool))}
        fresh = [ln for ln in lines if ln not in used]
        if not fresh:
            # Spent: start the pool over, but never with the line just said.
            last = conn.execute("SELECT line FROM used WHERE repo=? AND pool=? ORDER BY rowid DESC LIMIT 1",
                                (repo, pool)).fetchone()
            conn.execute("DELETE FROM used WHERE repo=? AND pool=?", (repo, pool))
            fresh = [ln for ln in lines if not last or ln != last[0]] or list(lines)
        line = rng.choice(fresh)
        conn.execute("INSERT OR IGNORE INTO used (repo, pool, line) VALUES (?, ?, ?)", (repo, pool, line))
        conn.commit()
        return line
    finally:
        conn.close()


# ── saying it ────────────────────────────────────────────────────────────────

class _Slots(dict):
    """A line whose fact is missing keeps a readable placeholder, not a crash."""

    def __missing__(self, key: str) -> str:
        return "some"


def _guest(event: str, sha: str, login: str) -> str | None:
    if not sha:
        return None
    n = int(hashlib.sha256(sha.encode()).hexdigest()[:8], 16) % GUEST_ONE_IN
    if n == 0:
        return quips._frank(event, login)
    if n == 1:
        return quips._prophet(event, login)
    return None


def say(event: str, repo: str, facts: dict | None = None, *, sha: str = "",
        rng: random.Random | None = None) -> tuple[str, str]:
    """``(line, situation)`` for ``event`` in ``repo``. The line is empty when
    the event has no pool at all (the caller then says nothing)."""
    facts = dict(facts or {})
    rng = rng or random.Random()
    guest = _guest(event, sha, str(facts.get("login") or ""))
    if guest:
        return guest, "guest"
    pools = (_cfg().get("troll") or {}).get(event) or {}
    for tag in situations(event, facts):
        lines = pools.get(tag)
        if lines:
            line = _pick(repo, f"{event}.{tag}", list(lines), rng)
            slots = _Slots({k: (f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else v)
                            for k, v in facts.items()})
            return line.format_map(slots), tag
    return "", "none"
