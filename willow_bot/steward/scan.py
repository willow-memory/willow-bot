#!/usr/bin/env python3
"""List open PRs across fleet orgs (+ rudi193-cmd user repos). One line each:
owner/repo#num|title|url
"""
from __future__ import annotations

import sys

from willow_bot.steward.fleet import iter_open_pulls, repo_matches_filters


def main() -> int:
    filters = sys.argv[1:]
    rows: list[tuple[str, int, str, str]] = []
    for repo, num, title, url in iter_open_pulls():
        if not repo_matches_filters(repo, num, filters):
            continue
        rows.append((repo, num, title, url))

    for repo, num, title, url in sorted(rows, key=lambda r: (r[0], r[1])):
        title = title.replace("\n", " ").replace("\r", " ")
        print(f"{repo}#{num}|{title}|{url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
