"""Classify GitHub notifications into steward lanes (ported from willow-2.0).

Lanes: noise | auto | watch | draft | urgent. Rules only — no LLM.
"""
from __future__ import annotations

import re
from typing import TypedDict


class _NotificationFields(TypedDict):
    id: str
    reason: str
    subject_type: str
    subject_title: str
    subject_url: str
    repo: str
    updated_at: str
    unread: bool


class Notification(_NotificationFields, total=False):
    """`NotRequired` is Python 3.11+; a total=False subclass keeps 3.10."""

    latest_comment_url: str

Lane = str

_PGP_PATTERNS = ("gpg failed to sign", "gpg: signing failed", "error: gpg failed")
_HUMAN_REASONS = {"mention", "review_requested", "assign"}
_BOT_FRAGMENTS = ("bot", "[bot]", "renovate", "dependabot", "github-actions")


def _is_bot_title(title: str) -> bool:
    lower = title.lower()
    return any(f in lower for f in ("ci:", "chore(deps)", "bump ", "update lock"))


def _is_ci_noise(n: Notification) -> bool:
    title = n["subject_title"].lower()
    return (
        n["reason"] in ("ci_activity", "subscribed")
        and any(kw in title for kw in ("ci:", "test:", "build:", "workflow run", "check run"))
    )


def classify(n: Notification, watch_repos: list[str] | None = None) -> Lane:
    repos = watch_repos or []
    repo = n["repo"]
    reason = n["reason"]
    s_type = n["subject_type"]
    title = n["subject_title"]

    title_lower = title.lower()
    if any(p in title_lower for p in _PGP_PATTERNS):
        return "auto"

    if reason in _HUMAN_REASONS:
        return "urgent"

    if _is_ci_noise(n) and repo not in repos:
        return "noise"
    if _is_bot_title(title) and repo not in repos:
        return "noise"

    if s_type == "Release":
        return "noise"

    merged_keywords = ("merged", "closed")
    if any(kw in title.lower() for kw in merged_keywords):
        return "watch"
    if s_type in ("PullRequest", "Issue") and reason == "subscribed" and repo not in repos:
        return "watch"

    if reason == "comment" and s_type in ("PullRequest", "Issue"):
        if repo in repos:
            return "draft"
        if not _is_bot_title(title):
            return "draft"

    if (
        reason == "author"
        and s_type in ("PullRequest", "Issue")
        and n.get("latest_comment_url")
        and not _is_bot_title(title)
    ):
        return "draft"

    if _is_ci_noise(n):
        return "watch"

    return "watch"


def work_id(n: Notification) -> str:
    url = n.get("subject_url") or n.get("id") or "unknown"
    safe_url = re.sub(r"[^a-z0-9]", "-", url.lower())[-40:]
    return f"upstream-{safe_url}"
