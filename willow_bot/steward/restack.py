"""restack: a pull request that main moved under, brought current by the
repo's own ``restack.py`` and pushed, so nobody has to.

For each repo the operator profile names under ``restack.repos``, each open
pull request from the repo's own branches that GitHub calls conflicting
(``mergeable_state == "dirty"``) is restacked once per (head, base) pair:

1. The bot's own clone of the repo, under ``<bot_dir>/restack/<owner>/<name>``
   (never an operator's working checkout), fetches the base and the branch.
2. The clone's own ``adapters/stdlib/restack/restack.py --onto origin/BASE
   BRANCH`` runs. Its rules settle what conflicts in the files the repo
   makes (generated lists, the changelog), checks the merge with
   ``test_docs.py``, and stops on anything else. The rules live in the repo
   and are tested there; this module only runs them.
3. Exit 0: the new tip, a merge commit whose first parent is the branch's
   tip as fetched, is pushed with the App's install token. A plain push: a
   branch that moved since is refused by GitHub, never overwritten.
   Exit 42 or 2: nothing is pushed, and the outcome is kept for the status
   comment, which says what a person has to settle.

This is the one thing the bot pushes (BOT-INVENTORY.md). It never pushes a
fork's branch, a commit restack.py didn't make, or with force, and never a
branch that merges cleanly: restacking those would only restart its CI.

The token reaches git through the environment (``GIT_CONFIG_*``), never on a
command line a process listing shows.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import github_app
from willow_bot import operator_profile
from willow_bot.paths import bot_dir

STATE_FILE = "restack.json"
SCRIPT = ("adapters", "stdlib", "restack", "restack.py")
#: at most this many pushes a tick, so a bad day can't flood the repo's CI
MAX_PUSHES = 5
GIT_TIMEOUT = 600
RESTACK_TIMEOUT = 1800


def remote_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def clone_dir(repo: str) -> Path:
    owner, name = repo.split("/", 1)
    return bot_dir() / "restack" / owner / name


def _state_path() -> Path:
    return bot_dir() / STATE_FILE


def load_state() -> dict:
    try:
        got = json.loads(_state_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def auth_env(repo: str) -> dict:
    """The environment that hands git the install token as an HTTP header
    for github.com only; {} when the App isn't configured (a local test)."""
    try:
        token = github_app.installation_token(repo)
    except Exception:  # noqa: BLE001 — no token: git goes without one
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(
    cwd: Path | None, *args: str, env: dict | None = None, timeout: int = GIT_TIMEOUT
):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
        env={**os.environ, **(env or {})},
    )  # fmt: skip


def _last(text: str, n: int = 6) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def _ready_clone(repo: str, env: dict) -> Path:
    """The bot's own clone, made once and kept detached (``restack_one``
    checks out the base before each run)."""
    path = clone_dir(repo)
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        got = _git(None, "clone", "-q", remote_url(repo), str(path), env=env)
        if got.returncode != 0:
            raise RuntimeError(f"clone failed: {_last(got.stderr)}")
    return path


def restack_one(repo: str, pull: dict, env: dict, python: str) -> dict:
    """One pull request: (fetched, ran, pushed or why not), as a dict."""
    branch, base = pull["head"]["ref"], pull["base"]["ref"]
    head_sha = pull["head"]["sha"]
    path = _ready_clone(repo, env)
    got = _git(path, "fetch", "-q", "origin",
               f"+refs/heads/{base}:refs/remotes/origin/{base}",
               f"+refs/heads/{branch}:refs/remotes/origin/{branch}", env=env)  # fmt: skip
    if got.returncode != 0:
        return {
            "outcome": "unreachable",
            "detail": f"fetch failed: {_last(got.stderr)}",
        }
    # restack.py as the base has it, the clone's tree detached at the base,
    # so no branch it moves is checked out
    got = _git(
        path, "checkout", "-q", "--force", "--detach", f"refs/remotes/origin/{base}"
    )
    if got.returncode != 0:
        return {
            "outcome": "unreachable",
            "detail": f"checkout failed: {_last(got.stderr)}",
        }
    fetched = _git(path, "rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()
    if fetched != head_sha:
        return {
            "outcome": "moved",
            "detail": f"the branch is at {fetched[:12]}, not {head_sha[:12]}: next tick",
        }
    _git(path, "branch", "-f", branch, fetched)
    script = path.joinpath(*SCRIPT)
    if not script.is_file():
        return {
            "outcome": "unreachable",
            "detail": f"{'/'.join(SCRIPT)} isn't in {repo}",
        }
    ran = subprocess.run(
        [python, str(script), "--onto", f"origin/{base}", branch],
        cwd=path, capture_output=True, text=True, timeout=RESTACK_TIMEOUT, check=False,
    )  # fmt: skip
    said = _last(ran.stdout + ran.stderr)
    if ran.returncode != 0:
        outcome = "stopped" if ran.returncode == 2 else "unreachable"
        return {"outcome": outcome, "detail": said, "exit": ran.returncode}
    tip = _git(path, "rev-parse", branch).stdout.strip()
    first = _git(path, "rev-parse", f"{tip}^1").stdout.strip()
    if tip == fetched or first != fetched:
        return {
            "outcome": "refused",
            "detail": f"restack's tip {tip[:12]} isn't one merge onto {fetched[:12]}",
        }
    pushed = _git(
        path, "push", "-q", remote_url(repo), f"{tip}:refs/heads/{branch}", env=env
    )
    if pushed.returncode != 0:
        return {
            "outcome": "unreachable",
            "detail": f"push refused: {_last(pushed.stderr)}",
        }
    return {"outcome": "pushed", "detail": said, "pushed": tip}


def run(*, plan: bool = False, python: str | None = None) -> dict:
    """One pass over every repo the profile names. With ``plan``, say which
    pull requests would be restacked and touch nothing."""
    repos = operator_profile.restack_repos()
    receipt: dict = {
        "event": "restack",
        "repos": repos,
        "plan": plan,
        "done": [],
        "skipped": 0,
    }
    if not repos:
        receipt["status"] = "absent"
        return receipt
    python = python or operator_profile.restack_python() or sys.executable
    state = load_state()
    pushes = 0
    for repo in repos:
        try:
            pulls = github_app.list_open_pulls(repo)
        except Exception as exc:  # noqa: BLE001
            receipt["done"].append(
                {"repo": repo, "outcome": "unreachable", "detail": str(exc)[:200]}
            )
            continue
        for p in pulls:
            if ((p.get("head") or {}).get("repo") or {}).get(
                "full_name", ""
            ).lower() != repo.lower():
                continue  # a fork's branch is never the bot's to push
            number = p["number"]
            key = f"{repo.lower()}#{number}"
            try:
                pull = github_app.get_pull(repo, number)
            except Exception as exc:  # noqa: BLE001
                receipt["done"].append(
                    {"pr": key, "outcome": "unreachable", "detail": str(exc)[:200]}
                )
                continue
            if pull.get("mergeable_state") != "dirty":
                entry = state.get(key, {})
                if entry and pull["head"]["sha"] not in (
                    entry.get("head"),
                    entry.get("pushed"),
                ):
                    state.pop(key)  # settled by someone, or moved on: forget it
                continue
            pair = {"head": pull["head"]["sha"], "base": pull["base"]["sha"]}
            if {k: state.get(key, {}).get(k) for k in pair} == pair:
                receipt["skipped"] += 1
                continue  # tried at this head and base already
            if plan:
                receipt["done"].append({"pr": key, "outcome": "would restack", **pair})
                continue
            if pushes >= MAX_PUSHES:
                receipt["done"].append(
                    {
                        "pr": key,
                        "outcome": "later",
                        "detail": f"{MAX_PUSHES} pushes this tick",
                    }
                )
                continue
            try:
                got = restack_one(repo, pull, auth_env(repo), python)
            except Exception as exc:  # noqa: BLE001 — one pull request never stops the rest
                got = {"outcome": "unreachable", "detail": str(exc)[:200]}
            pushes += got["outcome"] == "pushed"
            if got["outcome"] != "moved":
                state[key] = {**pair, **got}
            receipt["done"].append({"pr": key, **got})
    if not plan:
        _save_state(state)
    receipt["status"] = "ok"
    return receipt


def status_line(repo: str, number: int, head_sha: str) -> str:
    """The status comment's restack line for this head, or "". Said only
    while it's about this head (or the merge it pushed onto it)."""
    entry = load_state().get(f"{repo.lower()}#{number}")
    if not entry or head_sha not in (entry.get("head"), entry.get("pushed")):
        return ""
    if entry.get("outcome") == "pushed":
        return f"- restack: main merged in as `{entry['pushed'][:12]}`, settled by restack.py\n"
    first = (entry.get("detail") or "").strip().splitlines()[:1]
    why = first[0] if first else entry.get("outcome", "")
    return f"- restack: couldn't, a person is needed: {why}\n"
