#!/usr/bin/env python3
"""After each watch tick: detect PRs that left the open set, pull if merged, pip -e.

DEPRECATED (2026-09-15). This is the legacy host-sync path — it shells out
to ``gh api`` (a human's credential on the box), ``git pull`` (which turns
a diverged checkout into a merge commit), and ``pip install -e .`` on
whatever ``.venv`` it finds. The replacement is a pair the tick now runs
after every sweep: ``willow_bot.steward.tick.run_sweep`` (App-token
``gitsync_sweep`` via willow-mcp) and
``willow_bot.steward.tick.run_install_receipts`` (which calls
``willow_bot.install_receipt.refresh_editable`` — distinct states for
every refusal, never switches branches, never turns diverge into merge).

The module stays on disk for one prove window: an operator with no
willow-mcp on the box can still opt into the old behaviour with
``WILLOW_BOT_STEWARD_HOST_SYNC=1``. It is not called from the loop when
MCP is on (the default in production), and its ``sync_checkout`` will be
removed once the receipts land against a live merge.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GITHUB_ROOT = Path.home() / "github"


def _default_local_checkout() -> dict[str, str]:
    gh = GITHUB_ROOT
    return {
        "willow-memory/corpus-lens": str(gh / "willow-memory/corpus-lens"),
        "willow-memory/kartikeya": str(gh / "willow-memory/kartikeya"),
        "willow-memory/willow-reconciler": str(gh / "willow-memory/willow-reconciler"),
        "willow-memory/willow-mcp": str(gh / "willow-memory/willow-mcp"),
        "willow-memory/willow-gate": str(gh / "willow-memory/willow-gate"),
        "willow-memory/willows-grove": str(gh / "willow-memory/willows-grove"),
        "willow-memory/ratatosk": str(gh / "willow-memory/ratatosk"),
        "Die-Namic-Systems/Nestor": str(gh / "Die-Namic-Systems/nestor"),
        "hornbook-knowledge/Jeles": str(gh / "hornbook-knowledge/Jeles"),
        "hornbook-knowledge/oakenscrolls-office": str(
            gh / "hornbook-knowledge/oakenscrolls-office"
        ),
    }


LOCAL_CHECKOUT: dict[str, str] = _default_local_checkout()


def gh_api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def resolve_local(repo: str) -> Path | None:
    if repo in LOCAL_CHECKOUT:
        p = Path(LOCAL_CHECKOUT[repo])
        if p.is_dir():
            return p
    org, name = repo.split("/", 1)
    for candidate in (
        GITHUB_ROOT / org / name,
        GITHUB_ROOT / org / name.lower(),
        GITHUB_ROOT / "willow-memory" / name,
    ):
        if (candidate / ".git").exists() or (candidate / ".git").is_file():
            return candidate
    return None


def sync_checkout(repo: str, default_branch: str) -> dict:
    root = resolve_local(repo)
    if root is None:
        return {"status": "skip", "reason": "no_local_checkout", "repo": repo}

    subprocess.run(["git", "-C", str(root), "fetch", "origin"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "checkout", default_branch],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "pull", "--ff-only", "origin", default_branch],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"], text=True
    ).strip()

    pip_note = "none"
    pyproject = root / "pyproject.toml"
    setup_py = root / "setup.py"
    if pyproject.is_file() or setup_py.is_file():
        venv = root / ".venv"
        if not (venv / "bin" / "python").is_file():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run(
            [str(venv / "bin" / "pip"), "install", "-q", "-U", "pip"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [str(venv / "bin" / "pip"), "install", "-q", "-e", "."],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
        pip_note = "editable_install_ok"

    return {
        "status": "ok",
        "repo": repo,
        "path": str(root),
        "branch": default_branch,
        "head": sha,
        "pip": pip_note,
    }


def parse_key(key: str) -> tuple[str, int]:
    repo, num_s = key.rsplit("#", 1)
    return repo, int(num_s)


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: willow-bot-steward merge <state.json> <current_open.json>",
            file=sys.stderr,
        )
        return 2

    state_path = Path(sys.argv[1])
    current_open = json.loads(Path(sys.argv[2]).read_text())
    current_set = set(current_open)

    state: dict = {"seen": [], "open": [], "merged_synced": []}
    if state_path.is_file() and state_path.read_text().strip():
        state = json.loads(state_path.read_text())
    state.setdefault("seen", [])
    state.setdefault("open", [])
    state.setdefault("merged_synced", [])

    prev_set = set(state["open"])
    merged_synced = set(state["merged_synced"])

    if not prev_set:
        state["open"] = sorted(current_set)
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        if current_set:
            emit({"event": "merge_watch", "status": "baseline_open", "count": len(current_set)})
        return 0

    vanished = prev_set - current_set
    for key in sorted(vanished):
        if key in merged_synced:
            continue
        repo, num = parse_key(key)
        try:
            pr = gh_api(f"repos/{repo}/pulls/{num}")
        except subprocess.CalledProcessError as exc:
            emit({"event": "merged_check_error", "repo_pr": key, "detail": str(exc)})
            continue

        if not pr.get("merged_at"):
            emit(
                {
                    "event": "pr_closed_not_merged",
                    "repo_pr": key,
                    "state": pr.get("state"),
                }
            )
            merged_synced.add(key)
            continue

        meta = gh_api(f"repos/{repo}")
        branch = meta.get("default_branch") or "main"
        try:
            result = sync_checkout(repo, branch)
        except subprocess.CalledProcessError as exc:
            emit(
                {
                    "event": "merged_sync_failed",
                    "repo_pr": key,
                    "merged_at": pr["merged_at"],
                    "detail": (exc.stderr or b"").decode()[-500:],
                }
            )
            continue

        merged_synced.add(key)
        emit(
            {
                "event": "merged_synced",
                "repo_pr": key,
                "title": pr.get("title", ""),
                "merged_at": pr["merged_at"],
                **result,
            }
        )

    state["open"] = sorted(current_set)
    state["merged_synced"] = sorted(merged_synced)
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
