"""Fill in CI the webhooks never delivered, for open PRs that have none.

The ci step learns how a PR's checks went only from `check_run` webhooks
(fleet_bridge -> deposits). A delivery that never arrives (a route that
sometimes reaches another backend, an outage) leaves the PR's head with no
CI recorded at all, and `status` reads it `unknown` while GitHub holds 10-30
finished check runs for it. GitHub's own record is the honest source: for
each open PR whose head has nothing recorded, this step reads the head's
check runs under the App's token and appends one deposit per completed run,
exactly as a delivered webhook would (`deposits.append_local`), so the ci
step, the mirror and the voice take it from there unchanged.

Bounded: ``MAX_PER_TICK`` PRs a tick (two reads each: the PR, for its head,
when the inbox has not named it; and the head's check runs), and a head is
done once (``willow-bot/ci_backfill.json``). A head with no completed runs
yet is tried again on a later tick. Never touches a repo the App cannot
read: that PR is reported and skipped.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir

FILE_NAME = "ci_backfill.json"
MAX_PER_TICK = 10
#: Heads remembered as done; newest kept.
MAX_DONE = 2000


def path() -> Path:
    return bot_dir() / FILE_NAME


def _get_pull(repo: str, number: int) -> dict:
    import github_app

    return github_app.get_pull(repo, number)


def _check_runs(repo: str, sha: str) -> list[dict]:
    import github_app

    return github_app.list_check_runs(repo, sha)


def _load(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _recorded(state: dict, key: str, sha: str) -> bool:
    """Whether the ci step already has this PR head (a deposit arrived)."""
    heads = (state.get("ci_heads") or {}).get(key)
    return isinstance(heads, dict) and sha in heads


def run(state: dict, *, limit: int = MAX_PER_TICK) -> dict[str, Any]:
    """One pass over ``state`` (the steward state, read-only here). Returns
    the receipt (the caller emits it)."""
    from willow_bot import deposits

    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = path()
    book = _load(p)
    done: dict[str, str] = dict(book.get("done") or {})
    pr_heads = state.get("pr_heads") or {}
    filled: list[dict] = []
    waiting: list[str] = []
    unreadable: list[dict] = []
    looked = 0

    for key in state.get("open") or []:
        if looked >= limit:
            break
        if not isinstance(key, str) or "#" not in key:
            continue
        repo, _, num = key.rpartition("#")
        if not num.isdigit():
            continue
        sha = str((pr_heads.get(key) or {}).get("head_sha") or "")
        if sha and (_recorded(state, key, sha) or f"{key}@{sha}" in done):
            continue
        looked += 1
        try:
            if not sha:
                pull = _get_pull(repo, int(num))
                sha = str((pull.get("head") or {}).get("sha") or "")
                if not sha or _recorded(state, key, sha) or f"{key}@{sha}" in done:
                    continue
            runs = _check_runs(repo, sha)
        except Exception as exc:  # noqa: BLE001 — one unreadable repo never stops the rest
            unreadable.append({"pr": key, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        completed = [r for r in runs if isinstance(r, dict) and r.get("status") == "completed"
                     and r.get("conclusion")]
        if not completed:
            waiting.append(key)  # nothing finished yet: look again on a later tick
            continue
        for r in completed:
            suite = r.get("check_suite") if isinstance(r.get("check_suite"), dict) else {}
            rec = deposits.ci_outcome_record(
                repo=repo, head_sha=sha, check_run_id=r.get("id"), check_name=r.get("name"),
                conclusion=r.get("conclusion"), status=r.get("status"), pr_number=int(num),
                html_url=r.get("html_url"), sender_type="", received_at=r.get("completed_at"),
                head_branch=suite.get("head_branch"),
            )
            rec["source"] = "backfill"
            deposits.append_local(rec)
        done[f"{key}@{sha}"] = at
        filled.append({"pr": key, "head_sha": sha[:12], "runs": len(completed)})

    if len(done) > MAX_DONE:
        keep = sorted(done, key=lambda k: done[k])[-MAX_DONE:]
        done = {k: done[k] for k in keep}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"done": done}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"event": "steward_ci_backfill", "at": at,
            "status": "partial" if unreadable else "ok",
            "filled": filled, "waiting": waiting, "unreadable": unreadable}
