"""Tier A upstream watch list — repos with live contribution lanes (not the 59-fork pile).

Override with ``WILLOW_BOT_UPSTREAM_WATCH_REPOS`` (comma-separated). Defaults
reflect the 2026-09 portfolio: Redential + stack maintenance + basic-memory focus;
doobidoo/mcp-memory-service dropped from watch (no active lane).
"""
from __future__ import annotations

import os

# Tier A: notification triage + author PR ledger. Order = attention priority.
TIER_A_WATCH_REPOS: tuple[str, ...] = (
    "basicmachines-co/basic-memory",
    "Redential/redential-cli",
    "DeusData/codebase-memory-mcp",
    # Stretch / maintenance (open PR or queue).
    "NousResearch/hermes-agent",
    # Fleet substrate (your merges still surface in notifications).
    "willow-memory/willow-bot",
    "willow-memory/willow-mcp",
)


def watch_repos() -> list[str]:
    raw = os.environ.get("WILLOW_BOT_UPSTREAM_WATCH_REPOS", "").strip()
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    return list(TIER_A_WATCH_REPOS)
