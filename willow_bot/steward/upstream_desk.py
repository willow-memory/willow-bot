"""Upstream contribution desk on the steward tick (willow-2.0 upstream_watcher + tracker).

On each tick (default 5 min): poll ``/notifications`` with a cursor, classify into
lanes, write ``$WILLOW_HOME/upstream_steward/pending/*.json`` for human-facing work.

Every ``WILLOW_BOT_UPSTREAM_TRACKER_EVERY`` ticks: GraphQL search for the author's
open PRs and refresh ``$WILLOW_HOME/willow-bot/upstream_desk.json``.

Opt-in: ``WILLOW_BOT_UPSTREAM_DESK=1``. Uses ``gh api`` (user token), not the App
installation — same credential posture as ``fleet.iter_open_pulls``.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from willow_bot.steward import upstream_triage as triage
from willow_bot.steward.config import state_path, willow_home
from willow_bot.steward.fleet import DEFAULT_USER, gh_api
from willow_bot.steward.upstream_config import watch_repos as configured_watch_repos

_SEARCH_QUERY = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        url
        createdAt
        updatedAt
        isDraft
        repository { nameWithOwner }
      }
    }
  }
}
"""

def desk_enabled() -> bool:
    return os.environ.get("WILLOW_BOT_UPSTREAM_DESK", "").strip().lower() in ("1", "true", "yes")


def _author() -> str:
    return os.environ.get("WILLOW_BOT_UPSTREAM_AUTHOR", DEFAULT_USER).strip() or DEFAULT_USER


def _tracker_every_ticks() -> int:
    raw = os.environ.get("WILLOW_BOT_UPSTREAM_TRACKER_EVERY", "12").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 12
    return max(n, 1)


def _pending_dir() -> Path:
    return willow_home() / "upstream_steward" / "pending"


def _desk_json_path() -> Path:
    return willow_home() / "willow-bot" / "upstream_desk.json"


def _notifications_since(last_poll: str | None) -> str | None:
    if not last_poll:
        return None
    try:
        dt = datetime.fromisoformat(last_poll.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc) - timedelta(seconds=1)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return last_poll


def fetch_notifications(since: str | None) -> tuple[list[dict], str | None]:
    """Return (raw notification dicts, gh_error or None)."""
    url = "/notifications?all=true&per_page=50"
    since_param = _notifications_since(since)
    if since_param:
        url += f"&since={since_param}"
    try:
        data = gh_api(url)
        return (data if isinstance(data, list) else []), None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        return [], str(exc)[:300]


def _to_notification(raw: dict) -> triage.Notification:
    repo = raw.get("repository", {}).get("full_name", "")
    subject = raw.get("subject", {})
    return triage.Notification(
        id=str(raw.get("id", "")),
        reason=raw.get("reason", ""),
        subject_type=subject.get("type", ""),
        subject_title=subject.get("title", ""),
        subject_url=subject.get("url", ""),
        repo=repo,
        updated_at=raw.get("updated_at", ""),
        unread=bool(raw.get("unread", False)),
        latest_comment_url=subject.get("latest_comment_url", "") or "",
    )


def _write_pending(n: triage.Notification, lane: str, wid: str) -> None:
    pending_dir = _pending_dir()
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{wid}.json"
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            if existing.get("status") in ("posted", "closed", "skipped", "expired"):
                return
        except (json.JSONDecodeError, OSError):
            pass
    record = {
        "work_id": wid,
        "status": "awaiting_human" if lane in ("draft", "urgent") else lane,
        "lane": lane,
        "repo": n["repo"],
        "title": n["subject_title"],
        "url": n["subject_url"],
        "kind": n["subject_type"].lower(),
        "reason": n["reason"],
        "updated_at": n["updated_at"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _parse_github_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_days(updated_at: str | None, *, now: datetime | None = None) -> int | None:
    dt = _parse_github_ts(updated_at)
    if dt is None:
        return None
    ref = now or datetime.now(timezone.utc)
    return max(0, int((ref - dt).total_seconds() // 86400))


def _desk_lane(repo: str, title: str, is_draft: bool, watch: set[str], author: str) -> str:
    """How the seat should treat an open author PR."""
    title_l = title.lower()
    if is_draft or "do not merge" in title_l:
        return "hold"
    if repo.startswith(f"{author}/"):
        return "personal"
    if repo in watch:
        return "tier_a"
    return "other"


def _enrich_open_prs(prs: list[dict], watch: list[str], author: str) -> tuple[list[dict], dict[str, Any]]:
    watch_set = set(watch)
    now = datetime.now(timezone.utc)
    stale_days = int(os.environ.get("WILLOW_BOT_UPSTREAM_STALE_DAYS", "21"))
    enriched: list[dict] = []
    buckets: dict[str, list[dict]] = {
        "tier_a": [],
        "personal": [],
        "hold": [],
        "other": [],
        "stale": [],
    }
    for raw in prs:
        repo = raw.get("repo", "")
        created = raw.get("createdAt") or raw.get("created_at")
        updated = raw.get("updatedAt") or raw.get("updated_at")
        is_draft = bool(raw.get("isDraft") if "isDraft" in raw else raw.get("is_draft"))
        lane = _desk_lane(repo, str(raw.get("title", "")), is_draft, watch_set, author)
        idle = _age_days(updated, now=now)
        stale = idle is not None and idle >= stale_days and lane in ("tier_a", "other")
        entry = {
            **raw,
            "created_at": created,
            "updated_at": updated,
            "is_draft": is_draft,
            "lane": lane,
            "idle_days": idle,
            "stale": stale,
        }
        enriched.append(entry)
        buckets[lane].append(entry)
        if stale:
            buckets["stale"].append(entry)
    summary = {
        "tier_a_count": len(buckets["tier_a"]),
        "personal_count": len(buckets["personal"]),
        "hold_count": len(buckets["hold"]),
        "other_count": len(buckets["other"]),
        "stale_count": len(buckets["stale"]),
        "stale_after_days": stale_days,
        "tier_a": [_brief_pr(p) for p in buckets["tier_a"]],
        "stale": [_brief_pr(p) for p in buckets["stale"]],
        "hold": [_brief_pr(p) for p in buckets["hold"]],
    }
    return enriched, summary


def _brief_pr(p: dict) -> dict:
    return {
        "repo": p.get("repo"),
        "number": p.get("number"),
        "title": p.get("title"),
        "url": p.get("url"),
        "idle_days": p.get("idle_days"),
        "is_draft": p.get("is_draft"),
        "lane": p.get("lane"),
    }


def _graphql_search_open_prs(author: str) -> tuple[list[dict], str | None]:
    q = f"author:{author} type:pr state:open"
    prs: list[dict] = []
    cursor: str | None = None
    while True:
        payload = json.dumps({"query": _SEARCH_QUERY, "variables": {"q": q, "cursor": cursor}})
        try:
            proc = subprocess.run(
                ["gh", "api", "graphql", "--input", "-"],
                input=payload,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            data = json.loads(proc.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
            err = getattr(exc, "stderr", None) or str(exc)
            return prs, str(err)[:300]
        if data.get("errors"):
            return prs, str(data["errors"])[:300]
        search = data["data"]["search"]
        for node in search.get("nodes") or []:
            if not node:
                continue
            repo = node.get("repository", {}).get("nameWithOwner", "")
            prs.append(
                {
                    "repo": repo,
                    "number": node.get("number"),
                    "title": node.get("title", ""),
                    "url": node.get("url", ""),
                    "createdAt": node.get("createdAt"),
                    "updatedAt": node.get("updatedAt"),
                    "isDraft": node.get("isDraft"),
                }
            )
        page = search.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return prs, None


def _refresh_tracker(author: str, watch: list[str]) -> dict[str, Any]:
    open_prs, err = _graphql_search_open_prs(author)
    open_enriched, summary = _enrich_open_prs(open_prs, watch, author)
    body: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "author": author,
        "open": open_enriched,
        "open_count": len(open_enriched),
        "summary": summary,
    }
    if err:
        body["tracker_error"] = err
    path = _desk_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body


def _load_steward_state() -> dict:
    path = state_path()
    if path.is_file() and path.read_text().strip():
        return json.loads(path.read_text())
    return {}


def _save_upstream_slice(state: dict, upstream: dict) -> None:
    state["upstream_desk"] = upstream
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def run_upstream_desk(*, enable_mcp: bool | None = None) -> dict[str, Any]:
    """One upstream desk pass. Returns a receipt (caller emits to the tick journal)."""
    if enable_mcp is None:
        enable_mcp = os.environ.get("WILLOW_BOT_MCP", "").strip().lower() in ("1", "true", "yes")
    receipt: dict[str, Any] = {
        "event": "steward_upstream_desk",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not desk_enabled():
        receipt.update(status="absent", detail="WILLOW_BOT_UPSTREAM_DESK not enabled")
        return receipt

    author = _author()
    watch = configured_watch_repos()
    state = _load_steward_state()
    upstream_state = dict(state.get("upstream_desk") or {})
    cursor = dict(upstream_state.get("cursor") or {})
    tick_n = int(upstream_state.get("tick_count") or 0) + 1
    since = cursor.get("last_poll")
    seen_ids: list[str] = list(cursor.get("seen_ids") or [])

    raw_notes, gh_err = fetch_notifications(since)
    counts = {"noise": 0, "watch": 0, "draft": 0, "urgent": 0, "auto": 0, "new_pending": 0}
    new_items: list[dict] = []

    for raw in raw_notes:
        n = _to_notification(raw)
        dedup_key = f"{n['id']}:{n['updated_at']}"
        if dedup_key in seen_ids:
            continue
        lane = triage.classify(n, watch)
        counts[lane] = counts.get(lane, 0) + 1
        wid = triage.work_id(n)
        if lane in ("draft", "urgent", "auto"):
            _write_pending(n, lane, wid)
            counts["new_pending"] += 1
            new_items.append({"work_id": wid, "lane": lane, "repo": n["repo"], "title": n["subject_title"]})
        seen_ids.append(dedup_key)

    cursor["seen_ids"] = seen_ids[-500:]
    cursor["last_poll"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tracker_ran = False
    tracker_summary: dict[str, Any] | None = None
    if tick_n % _tracker_every_ticks() == 0:
        tracker_summary = _refresh_tracker(author, watch)
        tracker_ran = True

    filed: list[dict] = []
    if enable_mcp and os.environ.get("WILLOW_BOT_UPSTREAM_FILE_URGENT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        from willow_bot.steward import mcp_client

        app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
        filed_ids = set(upstream_state.get("filed_urgent") or [])
        for item in new_items:
            if item["lane"] != "urgent" or item["work_id"] in filed_ids:
                continue
            try:
                result = mcp_client.call(
                    "human_required_enqueue",
                    {
                        "app_id": app,
                        "kind": "review",
                        "priority": "high",
                        "title": f"Upstream urgent: {item['repo']}"[:200],
                        "summary": item.get("title", ""),
                        "source_ref": item.get("work_id", ""),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                receipt.setdefault("file_errors", []).append({"work_id": item["work_id"], "error": str(exc)[:200]})
                continue
            err = (
                str(result["error"])[:300]
                if isinstance(result, dict) and result.get("error")
                else None
            )
            if err:
                receipt.setdefault("file_errors", []).append({"work_id": item["work_id"], "error": err})
                continue
            filed_ids.add(item["work_id"])
            filed.append(item)
        upstream_state["filed_urgent"] = sorted(filed_ids)[-200:]

    upstream_state.update(
        {
            "cursor": cursor,
            "tick_count": tick_n,
            "last_counts": counts,
            "last_gh_error": gh_err,
            "last_tracker_at": tracker_summary.get("at") if tracker_summary else upstream_state.get("last_tracker_at"),
        }
    )
    _save_upstream_slice(state, upstream_state)

    receipt.update(
        status="ok" if gh_err is None else "degraded",
        author=author,
        watch_repos=len(watch),
        notifications=len(raw_notes),
        counts=counts,
        new_items=new_items[:20],
        gh_error=gh_err,
        tracker_ran=tracker_ran,
        open_prs=(tracker_summary or {}).get("open_count"),
        filed=filed,
        pending_dir=str(_pending_dir()),
        desk_json=str(_desk_json_path()),
    )
    return receipt
