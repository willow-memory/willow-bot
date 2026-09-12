#!/usr/bin/env python3
"""Merge willow-bot fleet_bridge PR webhook inbox into loki_pr_watch state.

Reads ~/.willow/upstream_steward/webhook_inbox/*.json (WILLOW_HOME), records
consumed work_ids, emits one JSON line per newly seen pull_request event.

PR watch never calls LOKI_WATCHER_URL / Ollama; Grove Loki (3B) is separate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from willow_bot.steward.config import (
    watcher_call_enabled,
    watcher_url,
    webhook_inbox_dir,
)

_MAX_CONSUMED = 5000
_MAX_SIGNALS = 100


def _load_state(path: Path) -> dict:
    if path.is_file() and path.read_text().strip():
        return json.loads(path.read_text())
    return {"seen": [], "open": [], "merged_synced": []}


def _emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _repo_pr(item: dict) -> str:
    repo = item.get("repo") or ""
    num = item.get("number")
    if repo and num is not None:
        return f"{repo}#{num}"
    return ""


def ingest(state_path: Path) -> int:
    if watcher_call_enabled():
        _emit(
            {
                "event": "error",
                "detail": (
                    "WILLOW_BOT_STEWARD_CALL_WATCHER / LOKI_PR_WATCH_CALL_WATCHER is set but not "
                    "supported; willow-bot runs loki.watcher separately "
                    f"(LOKI_WATCHER_URL={watcher_url()!r} is ignored here)."
                ),
            }
        )
        return 1

    inbox = webhook_inbox_dir()
    state = _load_state(state_path)
    state.setdefault("seen", [])
    state.setdefault("open", [])
    state.setdefault("merged_synced", [])
    consumed: set[str] = set(state.get("inbox_consumed") or [])
    signals: list[dict] = list(state.get("webhook_signals") or [])

    if not inbox.is_dir():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["inbox_consumed"] = sorted(consumed)
        state["webhook_signals"] = signals[-_MAX_SIGNALS:]
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        return 0

    for path in sorted(inbox.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        wid = item.get("work_id")
        if not wid or wid in consumed:
            continue
        if item.get("kind") != "pull_request":
            continue

        consumed.add(wid)
        key = _repo_pr(item)
        if not key:
            continue

        ev = {
            "event": "webhook_pr",
            "repo_pr": key,
            "action": item.get("action"),
            "title": item.get("title") or "",
            "url": item.get("html_url") or "",
            "received_at": item.get("received_at"),
            "work_id": wid,
            "merged": item.get("merged"),
            "pr_state": item.get("state"),
            "source": item.get("source") or "willow-bot",
        }
        _emit(ev)
        signals.append({k: v for k, v in ev.items() if k != "event"})

    if len(consumed) > _MAX_CONSUMED:
        consumed = set(sorted(consumed)[-_MAX_CONSUMED:])

    state["inbox_consumed"] = sorted(consumed)
    state["webhook_signals"] = signals[-_MAX_SIGNALS:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: willow-bot-steward inbox <state.json>", file=sys.stderr)
        return 2
    return ingest(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
