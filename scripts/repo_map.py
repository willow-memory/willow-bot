#!/usr/bin/env python3
"""Compare GitHub App installations with local ~/github clones."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GITHUB = Path(os.environ.get("GITHUB_ROOT", Path.home() / "github"))


def _local_repos() -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not _GITHUB.is_dir():
        return out
    for child in sorted(_GITHUB.iterdir()):
        if child.is_dir() and (child / ".git").is_dir():
            rc, origin, _ = subprocess.run(
                ["git", "-C", str(child), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=False,
            )
            if rc == 0 and origin.strip():
                m = None
                for part in ("github.com:", "github.com/"):
                    if part in origin:
                        tail = origin.split(part, 1)[-1].removesuffix(".git")
                        out[tail] = child
                        break
    return out


def main() -> int:
    env = os.environ.copy()
    for line in (_ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v

    proc = subprocess.run(
        [str(_ROOT / ".venv/bin/python"), str(_ROOT / "scripts/list_installations.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    data = json.loads(proc.stdout)
    remote = {r["full_name"]: r for r in data["repositories"]}
    local = _local_repos()

    remote_lower = {k.lower(): k for k in remote}
    local_lower = {k.lower(): k for k in local}
    both_keys = sorted(set(remote_lower) & set(local_lower))
    both = [remote_lower[k] for k in both_keys]
    remote_only = sorted(
        remote[k] for k in remote if k.lower() not in local_lower
    )
    local_only = sorted(
        local[k] for k in local if k.lower() not in remote_lower
    )

    report = {
        "app_repos": len(remote),
        "local_clones": len(local),
        "overlap": both,
        "app_only": remote_only,
        "local_only": local_only[:50],
        "local_only_truncated": len(local_only) > 50,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
