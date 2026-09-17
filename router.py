"""
router.py — GitHub event dispatcher for willow-bot.
b17: WBRT1  ΔΣ=42
"""
import logging
from typing import Callable

import quips
import rebase_shame
from integrations import fleet_bridge

log = logging.getLogger("willow-bot.router")


def route(event: str, payload: dict, post: Callable[[str, str], None]) -> None:
    """
    Dispatch a GitHub webhook event to the appropriate handler.

    event:   GitHub event name (X-GitHub-Event header value)
    payload: parsed JSON body
    post:    callable(repo_full_name, comment_body) — posts a comment or status
    """
    try:
        fleet_bridge.handle(event, payload)
    except Exception:
        log.exception("fleet_bridge failed for event %s", event)

    handlers = {
        "pull_request":  _handle_pull_request,
        "push":          _handle_push,
        "check_run":     _handle_check_run,
        "create":        _handle_create,
        "issues":        _handle_issues,
    }
    handler = handlers.get(event)
    if handler:
        handler(payload, post)
    else:
        log.debug("unhandled event: %s", event)


def _handle_pull_request(payload: dict, post: Callable) -> None:
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name", "")
    login = pr.get("user", {}).get("login", "")

    if action == "closed" and pr.get("merged"):
        quips.record_merge(login)
        msg = quips.pick("pr_merged", login)
        if msg:
            head_ref = pr.get("head", {}).get("ref", "")
            shame = rebase_shame.header(rebase_shame.get(repo, head_ref))
            if shame:
                msg = f"{shame}\n\n{msg}"
            post(repo, pr.get("number"), msg)


def _handle_push(payload: dict, post: Callable) -> None:
    ref = payload.get("ref", "")
    repo = payload.get("repository", {}).get("full_name", "")

    if payload.get("forced") and ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/"):]
        rebase_shame.increment(repo, branch)

    if ref in ("refs/heads/main", "refs/heads/master"):
        msg = quips.pick("push_to_main")
        if msg:
            log.info("[%s] push to main: %s", repo, msg)
            # No PR to comment on — log only, or post to a Grove channel


def _handle_check_run(payload: dict, post: Callable) -> None:
    action = payload.get("action")
    check = payload.get("check_run", {})
    conclusion = check.get("conclusion")
    repo = payload.get("repository", {}).get("full_name", "")

    if action == "completed":
        if conclusion == "success":
            msg = quips.pick("ci_pass")
        elif conclusion in ("failure", "timed_out", "startup_failure"):
            msg = quips.pick("ci_fail")
        else:
            # cancelled / skipped / stale / neutral / action_required: not a
            # pass and not a fail, and not silence either. A recorded negative
            # is not an absence; a run that never reached a verdict leaves a
            # line (the bridge already filed the item — this is the voice's
            # half of the same rule).
            log.info("[%s] CI %s (%s): could not run to a verdict",
                     repo, conclusion or "no conclusion", check.get("name", "?"))
            return
        if msg:
            log.info("[%s] CI %s: %s", repo, conclusion, msg)


def _handle_create(payload: dict, post: Callable) -> None:
    ref_type = payload.get("ref_type")
    repo = payload.get("repository", {}).get("full_name", "")

    if ref_type == "fork" or payload.get("forkee"):
        msg = quips.pick("new_fork")
        if msg:
            log.info("[%s] fork: %s", repo, msg)


def _handle_issues(payload: dict, post: Callable) -> None:
    action = payload.get("action")
    repo = payload.get("repository", {}).get("full_name", "")
    issue = payload.get("issue", {})

    if action == "opened":
        msg = quips.pick("gap_filed")
        if msg:
            post(repo, issue.get("number"), msg)
