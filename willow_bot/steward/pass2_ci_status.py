#!/usr/bin/env python3
"""CI rollup for all open PRs in fleet org scope (gh api).

Optional argv: org name (e.g. willow-memory), owner/repo, or owner/repo#num.
"""
from __future__ import annotations

import json
import subprocess
import sys

from willow_bot.steward.fleet import gh_api, iter_open_pulls, repo_matches_filters


def main() -> int:
    filters = sys.argv[1:]
    for repo, num, _title, _url in iter_open_pulls():
        if not repo_matches_filters(repo, num, filters):
            continue
        org = repo.split("/", 1)[0]
        pr = gh_api(f"repos/{repo}/pulls/{num}")
        sha = pr["head"]["sha"]
        checks = gh_api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
        runs = checks.get("check_runs", [])
        failed = [
            (r["name"], r.get("conclusion"), r.get("html_url", ""))
            for r in runs
            if r.get("conclusion") in ("failure", "cancelled", "timed_out")
        ]
        pending = [r["name"] for r in runs if r.get("status") != "completed"]
        success = sum(1 for r in runs if r.get("conclusion") == "success")
        print(
            json.dumps(
                {
                    "org": org,
                    "repo_pr": f"{repo}#{num}",
                    "branch": pr["head"]["ref"],
                    "head": sha[:12],
                    "state": pr["state"],
                    "mergeable": pr.get("mergeable"),
                    "checks_success": success,
                    "checks_failed": failed,
                    "checks_pending": pending,
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
