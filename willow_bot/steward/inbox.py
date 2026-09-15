#!/usr/bin/env python3
"""Merge willow-bot fleet_bridge webhook inbox into loki_pr_watch state.

Reads ~/.willow/upstream_steward/webhook_inbox/*.json (WILLOW_HOME), records
consumed work_ids, emits one JSON line per newly seen pull_request or
check_run event.

Gap 1d737ffa2595 — `check_run` items were dropped on the floor here: every
completed CI check `fleet_bridge.handle` deposited (keyed on the check id,
not the PR number, so a PR with four legs left four items) sat unread in
the inbox forever. The tick's journal therefore never named a red leg, and
the ci step's deposit-file reader was the only path a seat could learn a
check went red at all. This step now also consumes `check_run` items and
emits one `webhook_check_run` line per terminal conclusion (success,
failure, timed_out, cancelled, skipped, stale, neutral, action_required)
so the seat reading the tick sees the same terminal state GitHub asserts.
The ci step's dedup key ((head_sha, check_run_id) in `ci_filed`) is
unchanged, so this does not file a check twice.

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
        kind = item.get("kind")

        if kind == "pull_request":
            key = _repo_pr(item)
            if not key:
                continue
            consumed.add(wid)
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
        elif kind == "check_run":
            # Consumed unconditionally: an in-flight check (`action="created"`
            # in fleet_bridge terms) never reaches this inbox — only
            # `action="completed"` items do (fleet_bridge.py:213) — so every
            # row here has a terminal `conclusion` GitHub asserted.
            # `conclusion` is passed through verbatim so `cancelled`,
            # `skipped`, `stale`, `neutral`, and `action_required` stay
            # distinct from `success` and the reds (failure / timed_out /
            # startup_failure / cancelled) the ci step files. Missing
            # (`None`) is preserved as null, not coerced to a string.
            consumed.add(wid)
            ev = {
                "event": "webhook_check_run",
                "repo": item.get("repo") or "",
                "check_run_id": item.get("check_id"),
                "head_sha": item.get("head_sha") or "",
                "check_name": item.get("name") or "",
                "conclusion": item.get("conclusion"),
                "status": item.get("status"),
                "pr_number": item.get("pr_number"),
                "url": item.get("html_url") or "",
                "received_at": item.get("received_at"),
                "work_id": wid,
                "sender_type": item.get("sender_type") or "",
                "source": item.get("source") or "willow-bot",
            }
            _emit(ev)
            signals.append({"kind": "check_run",
                            **{k: v for k, v in ev.items() if k != "event"}})
        else:
            # Anything else (installation, installation_repositories, an
            # issue_comment landing in the inbox with a shape this step does
            # not name yet) stays in the inbox for a later step to consume;
            # not dropped, not double-emitted.
            continue

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
