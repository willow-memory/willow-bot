"""Upstream watch list — other people's repos the operator contributes to.

``WILLOW_BOT_UPSTREAM_WATCH_REPOS`` (comma-separated), else the operator
profile's ``upstream_watch`` (``willow_bot.operator_profile``), in attention
order. Empty when neither says: an install watches no one's repos until its
operator names them.
"""
from __future__ import annotations

from willow_bot import operator_profile


def watch_repos() -> list[str]:
    return operator_profile.upstream_watch_repos()
