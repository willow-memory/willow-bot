#!/usr/bin/env python3
"""Post Loki pass-2 review comments (gh api)."""
from __future__ import annotations

import json
import subprocess
import sys

COMMENTS = {
    "Die-Namic-Systems/Nestor#294": """## Loki pass 2 (operator box)

**CI:** 10/10 green on head `cf63ddc` (mergeable).

**Local:** `pytest` on `pr-294-pass2` — 2366 passed; 6 failures are **operator-box env** (`nestor` not on PATH for session_start probes; pre-commit pin probe), not reproduced on CI.

**codebase-memory:** wave2/meta-scan wiring present (`tests/test_scans_fire.py` lineage in tree).

**Verdict:** **OK to merge** from audit posture — trust CI green; no new findings vs pass 1.""",
    "hornbook-knowledge/Jeles#82": """## Loki pass 2

**CI:** 9/9 green (`dc2b269`).

**Local:** `687 passed` on `pr-82-pass2`.

**codebase-memory:** `tools/changelog_dedup.py` + `tests/test_changelog_dedup.py` / `test_release_wiring.py` wired; release-please workflow references dedup.

**Verdict:** **OK to merge.**""",
    "willow-memory/corpus-lens#42": """## Loki pass 2

**CI:** 10/10 green (`776a0b6`).

**Local:** `611 passed` (+179 subtests) on `pr-42-pass2`.

**codebase-memory:** guard/ingest spine touched (35 files vs index); pr-title + changelog pin wave consistent with fleet meta-scan.

**Verdict:** **OK to merge.**""",
    "willow-memory/kartikeya#56": """## Loki pass 2

**CI:** 8/8 green (`1db9b741`).

**Local:** `286 passed` on `pr-56-pass2`.

**codebase-memory:** `test_release_wiring` + `changelog_dedup` entrypoints; `pr-title.yml` in graph.

**Verdict:** **OK to merge.**""",
    "willow-memory/willow-mcp#504": """## Loki pass 2

**CI:** **vendor-sync FAILED** on head `59c83c8` (job 103489121363). Other checks green.

**Hold:** `tests/test_nest_pipeline_vendor.py` pins `nest/secrets.py` — canonical advanced while vendored copy lags (same class of issue as pass 1). Re-sync vendor pin / body before merge.

**Note:** Local `test_scans_fire` failure on operator box was from **uncommitted** `test_willow_mcp_repo_wiring.py` overlay, not PR head in isolation.

**Verdict:** **Do not merge** until vendor-sync green.""",
}


def post(repo_pr: str, body: str) -> None:
    repo, num_s = repo_pr.rsplit("#", 1)
    subprocess.check_call(
        [
            "gh",
            "api",
            f"repos/{repo}/issues/{num_s}/comments",
            "-f",
            f"body={body}",
        ]
    )


def main() -> int:
    only = sys.argv[1:] or list(COMMENTS)
    for key in only:
        if key not in COMMENTS:
            print(f"unknown {key}", file=sys.stderr)
            return 2
        post(key, COMMENTS[key])
        print(json.dumps({"posted": key}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
