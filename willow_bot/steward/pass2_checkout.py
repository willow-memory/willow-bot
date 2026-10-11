#!/usr/bin/env python3
"""Fetch PR heads and run pytest -q in each repo (operator credentials).

    python -m willow_bot.steward.pass2_checkout owner/repo#123 [owner/repo#456 ...]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from willow_bot import operator_profile

GITHUB = operator_profile.github_root()


def parse_prs(argv: list[str]) -> list[tuple[str, str, int]]:
    """``owner/repo#num`` arguments → ``(owner, repo, num)``."""
    out = []
    for arg in argv:
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)#(\d+)", arg.strip())
        if not m:
            raise SystemExit(f"pass2_checkout: expected owner/repo#number, got {arg!r}")
        out.append((m.group(1), m.group(2), int(m.group(3))))
    return out


def gh(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def repo_path(org: str, name: str) -> Path:
    p = GITHUB / org / name
    if p.is_dir():
        return p
    return GITHUB / org / name.lower()


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out[-4000:]


def main(argv: list[str] | None = None) -> int:
    prs = parse_prs(sys.argv[1:] if argv is None else argv)
    if not prs:
        print(__doc__, file=sys.stderr)
        return 2
    for org, repo, num in prs:
        full = f"{org}/{repo}" if "/" in f"{org}/{repo}" else f"{org}/{repo}"
        # normalize repo folder name
        folder = repo
        pr = gh(f"repos/{org}/{folder}/pulls/{num}")
        root = repo_path(org, folder)
        branch = f"pr-{num}-pass2"
        subprocess.check_call(
            ["git", "fetch", "origin", f"pull/{num}/head:{branch}"],
            cwd=str(root),
        )
        subprocess.check_call(["git", "checkout", branch], cwd=str(root))
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(root), text=True).strip()
        venv_py = root / ".venv" / "bin" / "python3"
        py = str(venv_py) if venv_py.is_file() else sys.executable
        code, tail = run([py, "-m", "pytest", "tests/", "-q", "--tb=no"], cwd=root)
        print(
            json.dumps(
                {
                    "repo_pr": f"{org}/{folder}#{num}",
                    "path": str(root),
                    "head": sha,
                    "pytest_exit": code,
                    "pytest_tail": tail,
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
