"""
accumulator.py — Signal tracking and threshold logic for Loki's eight triggers.
b17: LOKI3
"""
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("loki.accumulator")


@dataclass
class Signal:
    trigger: str          # Lokasenna | Mistletoe | Anansi | Eshu | Hermes | Coyote | Salmon | Surfacing
    description: str      # one-line description for the context packet
    evidence: dict        # structured evidence to pass to context builder
    fired_at: float = field(default_factory=time.time)


class Accumulator:
    """
    Tracks signal state across checks. Thread-safe enough for single-threaded watcher.
    Fires when a trigger crosses its threshold.
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._state_path = state_path or Path(__file__).parent / ".loki_state.json"
        self._state = self._load()

    def _load(self) -> dict:
        defaults = {
            "next_session_counts": {},
            "uncataloged_seen": {},
            "thread_cursors": {},
            "seen_specs": [],
            "lokasenna_fired_ids": [],
            "oakenscroll_fired_ids": [],
        }
        if self._state_path.exists():
            try:
                saved = json.loads(self._state_path.read_text())
                defaults.update(saved)
            except Exception:
                pass
        return defaults

    def _save(self) -> None:
        self._state_path.write_text(json.dumps(self._state, indent=2))

    # ── Lokasenna — @loki mention ──────────────────────────────────────────────

    def check_lokasenna(self, message: dict) -> Optional[Signal]:
        """Fire immediately if @loki appears in message content. Dedup by message ID."""
        if "@loki" not in message.get("content", ""):
            return None
        msg_id = message.get("id")
        fired = self._state["lokasenna_fired_ids"]
        if msg_id in fired:
            return None
        fired.append(msg_id)
        # Keep only last 200 IDs to bound state size
        if len(fired) > 200:
            self._state["lokasenna_fired_ids"] = fired[-200:]
        self._save()
        return Signal(
            trigger="Lokasenna",
            description=f"@loki mentioned by {message['sender']} in #{message['channel']}",
            evidence={"message": message},
        )

    # ── Mistletoe — new spec file on disk ─────────────────────────────────────

    def check_mistletoe(self, new_spec_path: str, disk_repos: list[str], catalog_ids: set[str]) -> Optional[Signal]:
        """Fire only on spec files not seen before in uncataloged repos."""
        if new_spec_path in self._state["seen_specs"]:
            return None

        path = Path(new_spec_path)
        # Repo name is the top-level dir under /home/sean-campbell/github/
        try:
            repo_name = path.relative_to("/home/sean-campbell/github").parts[0]
        except ValueError:
            return None

        # Mark as seen regardless — don't re-fire even if cataloged later
        self._state["seen_specs"].append(new_spec_path)
        self._save()

        if repo_name not in catalog_ids:
            return Signal(
                trigger="Mistletoe",
                description=f"New spec file in uncataloged repo: {repo_name}",
                evidence={"spec_path": new_spec_path, "repo": repo_name},
            )
        return None

    # ── Cattle of Hermes — uncataloged repo with new commits ──────────────────

    def check_hermes(self, repo_path: str, catalog_ids: set[str], last_commit_ts: float) -> Optional[Signal]:
        """Fire if uncataloged repo has new commits and hasn't been mentioned in 7 days."""
        repo_name = Path(repo_path).name
        if repo_name in catalog_ids:
            return None

        now = time.time()
        seven_days = 7 * 24 * 3600
        first_seen = self._state["uncataloged_seen"].get(repo_path)

        if first_seen is None:
            self._state["uncataloged_seen"][repo_path] = now
            self._save()
            return None

        if now - first_seen >= seven_days:
            return Signal(
                trigger="Hermes",
                description=f"Uncataloged repo {repo_name} has new commits — unseen in Grove for 7+ days",
                evidence={"repo_path": repo_path, "last_commit_ts": last_commit_ts},
            )
        return None

    # ── Salmon of Wisdom — repeated deferrals ─────────────────────────────────

    def check_salmon(self, message: dict) -> Optional[Signal]:
        """Count 'next session' references per item. Fire at 3."""
        content = message.get("content", "").lower()
        if "next session" not in content:
            return None

        # Rough key: first 60 chars of the sentence containing "next session"
        idx = content.index("next session")
        start = max(0, idx - 30)
        key = content[start:idx + 30].strip()[:60]

        counts = self._state["next_session_counts"]
        counts[key] = counts.get(key, 0) + 1
        self._save()

        if counts[key] >= 3:
            return Signal(
                trigger="Salmon",
                description=f"Item deferred 'next session' {counts[key]} times: {key!r}",
                evidence={"item_key": key, "count": counts[key], "message": message},
            )
        return None

    # ── Oakenscroll — message routed to oakenscroll ───────────────────────────

    def check_oakenscroll(self, message: dict) -> Optional[Signal]:
        """Fire when to_agent=oakenscroll or @oakenscroll mentioned. Dedup by message ID."""
        # Never route messages that are already in #oakenscroll or from grove-router
        if message.get("channel") == "oakenscroll":
            return None
        if message.get("sender") in ("grove-router", "loki"):
            return None
        content = message.get("content", "")
        to_agent = message.get("to_agent", "__all__")
        if to_agent != "oakenscroll" and "@oakenscroll" not in content.lower():
            return None
        msg_id = message.get("id")
        fired = self._state.setdefault("oakenscroll_fired_ids", [])
        if msg_id in fired:
            return None
        fired.append(msg_id)
        if len(fired) > 200:
            self._state["oakenscroll_fired_ids"] = fired[-200:]
        self._save()
        return Signal(
            trigger="Oakenscroll",
            description=f"Message routed to oakenscroll from {message['sender']} in #{message['channel']}",
            evidence={"message": message},
        )

    # ── Surfacing — thread drift ───────────────────────────────────────────────

    def check_surfacing(self, channel: str, message: dict) -> Optional[Signal]:
        """Fire if 10+ messages in channel with no sean-campbell response."""
        cursors = self._state["thread_cursors"]
        if channel not in cursors:
            cursors[channel] = {"last_sean_id": 0, "count_since": 0}

        entry = cursors[channel]
        if message.get("sender") == "sean-campbell":
            entry["last_sean_id"] = message["id"]
            entry["count_since"] = 0
        else:
            entry["count_since"] = entry.get("count_since", 0) + 1

        self._save()

        if entry["count_since"] >= 10:
            entry["count_since"] = 0  # reset after firing
            self._save()
            return Signal(
                trigger="Surfacing",
                description=f"#{channel}: {entry['count_since'] + 10} messages without USER response — surfacing",
                evidence={"channel": channel, "last_sean_id": entry["last_sean_id"]},
            )
        return None
