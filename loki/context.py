"""
context.py — Build context packets for Cerebras. Target: under 2000 tokens.
b17: LOKI3
"""
import json
import logging
import subprocess
from pathlib import Path

import psycopg2

from loki.accumulator import Signal
from loki import poster

log = logging.getLogger("loki.context")

_GITHUB_ROOT = Path("/home/sean-campbell/github")
_CATALOG_PATH = _GITHUB_ROOT / "safe-app-store" / "catalog.json"


def _recent_messages(channel: str, limit: int = 20) -> list[dict]:
    conn = poster._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, c.name AS channel, m.sender, m.content, m.created_at
                FROM grove.messages m
                JOIN grove.channels c ON c.id = m.channel_id
                WHERE c.name = %s AND m.is_deleted = 0
                ORDER BY m.id DESC LIMIT %s
                """,
                (channel, limit),
            )
            rows = cur.fetchall()
            return [
                {"id": r[0], "channel": r[1], "sender": r[2], "content": r[3], "created_at": str(r[4])}
                for r in reversed(rows)
            ]
    finally:
        conn.close()


def _disk_uncataloged() -> list[str]:
    try:
        catalog = json.loads(_CATALOG_PATH.read_text())
        catalog_ids = {a["id"] for a in catalog.get("apps", [])}
    except Exception:
        catalog_ids = set()

    result = []
    for entry in sorted(_GITHUB_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name not in catalog_ids:
            last_commit = _last_commit_date(entry)
            result.append(f"{entry.name} (last commit: {last_commit})")
    return result


def _last_commit_date(repo_path: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%ci"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() or "no commits"
    except Exception:
        return "not a git repo"


def build(signal: Signal, channel: str = "tonight") -> str:
    """Build a context packet string for Cerebras. Under 2000 tokens."""
    messages = _recent_messages(channel, limit=40)
    uncataloged = _disk_uncataloged()

    lines = [
        "CURRENT STATE (as of this session — do not raise already-resolved issues):",
        "- Loki exists in two forms: (1) Claude session Loki — stateless, adversarial, session-bound by design;"
        " (2) willow-bot/loki/ watcher — standalone Python process using Cerebras for pattern detection."
        " Both are intentional. KB atom 18594555 documents both forms. This is NOT a broken promise.",
        "",
        "RECENT GROVE MESSAGES (last 20 from relevant channels):",
    ]
    for m in messages:
        snippet = m["content"].replace("\n", " ")[:200]
        lines.append(f"[#{m['channel']}] #{m['id']} {m['sender']}: {snippet}")

    lines.append("")
    lines.append("DISK STATE (repos not in catalog):")
    for repo in uncataloged[:15]:
        lines.append(f"- /github/{repo}")

    lines.append("")
    lines.append("PATTERN DETECTED:")
    lines.append(signal.description)

    if signal.evidence:
        lines.append("")
        lines.append("EVIDENCE:")
        for k, v in signal.evidence.items():
            if k == "message":
                continue  # already in Grove messages above
            lines.append(f"  {k}: {str(v)[:200]}")

    return "\n".join(lines)
