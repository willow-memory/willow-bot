#!/usr/bin/env python3
"""PR scan: every pull request touched since a date, read from git alone.

Read-only over local clones that have fetched `+refs/pull/*/head:refs/remotes/pr/*`.
Two sources per repo, both from git:
  - merged: first-parent merge commits ("Merge pull request #N ...") and squash
    commits ("subject (#N)") on the default branch since SINCE
  - pull refs: every refs/remotes/pr/N whose head commit is dated since SINCE;
    those not merged are reported as "not merged" (git can't tell open from closed)

Deterministic: same refs, same output bytes.
  python pr_scan.py SINCE_ISO REPO_DIR [REPO_DIR ...]
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MERGE = re.compile(r"^Merge pull request #(\d+) from (\S+)")
SQUASH = re.compile(r"^(.*) \(#(\d+)\)$")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    ).stdout


def default_ref(repo: Path) -> str:
    head = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
    if head:
        return head
    for b in ("main", "master"):
        if git(repo, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{b}").strip():
            return f"refs/remotes/origin/{b}"
    return "HEAD"


def slug(repo: Path) -> str:
    url = git(repo, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else repo.name


def scan(repo: Path, since: datetime) -> list[dict]:
    prs: dict[int, dict] = {}
    log = git(
        repo,
        "log",
        default_ref(repo),
        "--first-parent",
        f"--since={since.isoformat()}",
        "--format=%cI%x1f%s%x1f%b%x1e",
    )
    for rec in filter(None, (r.strip("\n") for r in log.split("\x1e"))):
        date, subject, body = (rec.split("\x1f") + ["", ""])[:3]
        m = MERGE.match(subject)
        if m:
            title = next((x for x in body.splitlines() if x.strip()), m.group(2))
            prs[int(m.group(1))] = {
                "n": int(m.group(1)),
                "state": "merged",
                "date": date[:10],
                "title": title.strip(),
            }
            continue
        m = SQUASH.match(subject)
        if m:
            prs[int(m.group(2))] = {
                "n": int(m.group(2)),
                "state": "merged",
                "date": date[:10],
                "title": m.group(1).strip(),
            }
    refs = git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)%1f%(committerdate:iso-strict)%1f%(subject)",
        "refs/remotes/pr/",
    )
    for line in refs.splitlines():
        ref, date, subject = line.split("\x1f", 2)
        n = int(ref.rsplit("/", 1)[1])
        if n in prs or datetime.fromisoformat(date) < since:
            continue
        prs[n] = {
            "n": n,
            "state": "not merged",
            "date": date[:10],
            "title": subject.strip(),
        }
    return [prs[k] for k in sorted(prs)]


def main(since_iso: str, *repos: str) -> None:
    since = datetime.fromisoformat(since_iso)
    total = 0
    out = []
    for r in sorted(repos, key=lambda p: slug(Path(p)).lower()):
        rows = scan(Path(r), since)
        total += len(rows)
        if not rows:
            continue
        merged = sum(x["state"] == "merged" for x in rows)
        out.append(
            f"\n### {slug(Path(r))} ({len(rows)}: {merged} merged, {len(rows) - merged} not merged)\n"
        )
        out.append("| PR | Date | State | Title |\n|---|---|---|---|")
        s = slug(Path(r))
        out += [
            f"| [#{x['n']}](https://github.com/{s}/pull/{x['n']}) | {x['date']} | {x['state']} | "
            f"{x['title'].replace('|', '/')} |"
            for x in rows
        ]
    print(f"PRs since {since_iso}: {total}")
    print("\n".join(out))


if __name__ == "__main__":
    main(*sys.argv[1:])
