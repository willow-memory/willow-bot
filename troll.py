"""
troll.py — the bot's voice: a weird little troll under the bridge that counts.

Every line it says carries a real fact from the event (how big, how long,
how many reds, who reviewed), picked from the pool that fits the situation
most specifically, and never repeated in a repo until that pool is spent.
Deterministic: no model. Pools live in willow-bot.json under "troll", so the
voice is edited there, not here.

    situation   the most specific tag that fits (huge, speedrun, revert, ...)
    pool        troll.<event>.<situation> — falls back to troll.<event>.default
    slots       {size} {files} {days} {hours} {reds} {reviewer} {login} — a
                line is only drawn when every slot it names has a real fact
    memory      $WILLOW_HOME/willow-bot/persona/willow-bot-voice.db
    guests      FRANK / PROPHET cameo on ~1 in 20 PR openings and merges,
                seeded by the sha (never on reviews, CI or the other moments)
    scope       troll_scope in willow-bot.json: the orgs it may speak in and
                the author associations it may speak to (see allowed())
    limits      once per PR per moment, once per reviewer per PR, and a daily
                cap per repo (see claim())

The weekend is read in UTC.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
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
#: Two guests share one draw: each gets 1 in GUEST_DRAW, together 1 in 20.
GUEST_DRAW = 40
GUEST_EVENTS = ("pr_opened", "pr_merged")
DAILY_CAP_DEFAULT = 20


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
    """The voice's own database, in autocommit mode so every read-then-write
    below runs inside one explicit ``BEGIN IMMEDIATE`` transaction: two
    webhook workers can never both read the same "unused" set (Loki
    8F92E889)."""
    from willow_bot import persona_store

    conn = sqlite3.connect(persona_store.db_path("voice"), timeout=10, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS used (repo TEXT, pool TEXT, line TEXT, "
                 "PRIMARY KEY (repo, pool, line))")
    conn.execute("CREATE TABLE IF NOT EXISTS spoken (repo TEXT, pr TEXT, moment TEXT, "
                 "day TEXT, PRIMARY KEY (repo, pr, moment))")
    return conn


def _pick(repo: str, pool: str, lines: list[str], rng: random.Random) -> str:
    conn = _memory()
    try:
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("COMMIT")
        return line
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ── scope and limits: who the troll talks to, and how often ─────────────────

#: GitHub's author_association values for people inside the repo.
_INSIDERS = ("OWNER", "MEMBER", "COLLABORATOR")


def allowed(repo: str, association: object, *, bot: bool = False) -> bool:
    """Whether the troll may speak here, to this person.

    ``troll_scope.orgs`` (willow-bot.json) lists the orgs it may speak in —
    anything else, including an install the App has beyond them, is
    silent. ``troll_scope.associations`` lists who it may speak to
    (default: owners, members, collaborators): an outside contributor is
    never trolled. Bot authors are allowed only where the repo is."""
    scope = _cfg().get("troll_scope") or {}
    orgs = [str(o).lower() for o in scope.get("orgs") or []]
    if str(repo).split("/", 1)[0].lower() not in orgs:
        return False
    if bot:
        return True
    allowed_assoc = [str(a).upper() for a in scope.get("associations") or _INSIDERS]
    return str(association or "").upper() in allowed_assoc


def claim(repo: str, pr: object, moment: str, *, today: str | None = None) -> bool:
    """Reserve the right to speak once about ``moment`` on ``repo#pr``.

    False when this PR already heard this moment (a close/reopen loop, a
    second approval from the same reviewer — the moment names the reviewer)
    or when the repo has used its daily cap (``troll_scope.daily_cap``,
    default 20). The claim is written before the line is posted, so a
    failed post is not retried into a storm."""
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cap = int((_cfg().get("troll_scope") or {}).get("daily_cap") or DAILY_CAP_DEFAULT)
    conn = _memory()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM spoken WHERE repo=? AND pr=? AND moment=?",
                        (repo, str(pr), moment)).fetchone():
            conn.execute("ROLLBACK")
            return False
        n = conn.execute("SELECT COUNT(*) FROM spoken WHERE repo=? AND day=?", (repo, today)).fetchone()[0]
        if n >= cap:
            conn.execute("ROLLBACK")
            return False
        conn.execute("INSERT INTO spoken (repo, pr, moment, day) VALUES (?, ?, ?, ?)",
                     (repo, str(pr), moment, today))
        conn.execute("COMMIT")
        return True
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ── saying it ────────────────────────────────────────────────────────────────

_SLOT = re.compile(r"{(\w+)}")


def _fillable(line: str, facts: dict) -> bool:
    """Every slot the line names has a real fact (Loki 8F92E889: a missing
    fact used to render as "some")."""
    return all(facts.get(name) not in (None, "") for name in _SLOT.findall(line))


def _render(line: str, facts: dict) -> str:
    return line.format(**{k: (f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else v)
                          for k, v in facts.items()})


def _guest(event: str, sha: str, login: str) -> str | None:
    """FRANK or PROPHET, on PR openings and merges only (the moments they
    have their own lines for), seeded by the sha."""
    if not sha or event not in GUEST_EVENTS:
        return None
    n = int(hashlib.sha256(sha.encode()).hexdigest()[:8], 16) % GUEST_DRAW
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
        lines = [ln for ln in pools.get(tag) or [] if _fillable(ln, facts)]
        if lines:
            line = _pick(repo, f"{event}.{tag}", lines, rng)
            return _render(line, facts), tag
    return "", "none"
