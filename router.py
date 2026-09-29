"""
router.py — GitHub event dispatcher for willow-bot.
b17: WBRT1  ΔΣ=42
"""
import logging
import os
from typing import Callable

import horoscope
import quips
import rebase_shame
import runes
import sigh
import troll
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
        "pull_request_review": _handle_review,
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


def _mode_override() -> bool:
    """FRANK_MODE / PROPHET_MODE still take over the whole voice (quips)."""
    return bool(os.getenv("FRANK_MODE") or os.getenv("PROPHET_MODE"))


def _may(repo: str, number: object, moment: str, association: object, *, bot: bool = False) -> bool:
    """Scope first (org and author association), then the once-per-moment
    and daily-cap claim. Anything that fails is silent, and says why in
    the log."""
    try:
        if not troll.allowed(repo, association, bot=bot):
            log.info("[%s] #%s %s: out of the troll's scope — silent", repo, number, moment)
            return False
        if not troll.claim(repo, number, moment):
            log.info("[%s] #%s %s: already said, or daily cap reached — silent", repo, number, moment)
            return False
    except Exception:  # noqa: BLE001 — the voice must never break the webhook
        log.exception("troll gate failed for %s on %s", moment, repo)
        return False
    return True


def _troll_line(event: str, repo: str, facts: dict, sha: str = "") -> str:
    try:
        line, tag = troll.say(event, repo, facts, sha=sha)
    except Exception:  # noqa: BLE001 — the voice must never break the webhook
        log.exception("troll failed for %s on %s", event, repo)
        return ""
    log.info("[%s] %s (%s): %s", repo, event, tag, line)
    return line


def _handle_pull_request(payload: dict, post: Callable) -> None:
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name", "")
    login = pr.get("user", {}).get("login", "")
    number = pr.get("number")
    sha = pr.get("head", {}).get("sha", "")
    human = pr.get("user", {}).get("type") != "Bot"
    assoc = pr.get("author_association")

    if _mode_override():
        # The old single-voice path, behind the same scope and limits.
        if action == "closed" and pr.get("merged"):
            quips.record_merge(login)
            if _may(repo, number, "merged", assoc, bot=not human):
                msg = quips.pick("pr_merged", login)
                if msg:
                    post(repo, number, msg)
        elif action == "opened" and _may(repo, number, "opened", assoc, bot=not human):
            msg = quips.pick("pr_opened", login, sha=sha)
            if msg:
                post(repo, number, msg)
        return

    facts = troll.pr_facts(pr)
    if action == "closed" and pr.get("merged"):
        title = quips.record_merge(login) if human else ""
        if not _may(repo, number, "merged", assoc, bot=not human):
            return
        line = _troll_line("pr_merged", repo, facts, sha)
        if not line:
            return
        if human:
            line = f"**{title.capitalize()} {login}** — {line}\n\n{horoscope.reading(login, title=title)}"
        shame = rebase_shame.header(rebase_shame.get(repo, pr.get("head", {}).get("ref", "")))
        post(repo, number, f"{shame}\n\n{line}" if shame else line)
    elif action == "closed":
        if not _may(repo, number, "closed", assoc, bot=not human):
            return
        line = _troll_line("pr_closed_unmerged", repo, facts, sha)
        if line:
            post(repo, number, line)
    elif action == "opened":
        if not _may(repo, number, "opened", assoc, bot=not human):
            return
        line = _troll_line("pr_opened", repo, facts, sha)
        if not line:
            return
        if human:
            line = f"**{quips.get_title(login).capitalize()} {login}** — {line}"
        post(repo, number, f"{line}\n\n{runes.cast(sha)}" if sha else line)
    elif action == "ready_for_review":
        if not _may(repo, number, "ready", assoc, bot=not human):
            return
        line = _troll_line("pr_ready", repo, facts, sha)
        if line:
            post(repo, number, line)
    elif action == "reopened":
        if not _may(repo, number, "reopened", assoc, bot=not human):
            return
        line = _troll_line("pr_reopened", repo, facts, sha)
        if line:
            post(repo, number, line)


def _handle_review(payload: dict, post: Callable) -> None:
    """An approval or a change request gets one line on the PR, naming the
    reviewer. Plain comments and dismissals say nothing."""
    if payload.get("action") != "submitted" or _mode_override():
        return
    review = payload.get("review") or {}
    event = {"approved": "review_approved",
             "changes_requested": "review_changes_requested"}.get(str(review.get("state") or "").lower())
    if not event:
        return
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository", {}).get("full_name", "")
    reviewer = (review.get("user") or {}).get("login", "")
    # Once per reviewer per PR; the reviewer is the one being addressed.
    if not reviewer or not _may(repo, pr.get("number"), f"review:{reviewer}", review.get("author_association"),
                                bot=(review.get("user") or {}).get("type") == "Bot"):
        return
    facts = {**troll.pr_facts(pr), "reviewer": reviewer}
    line = _troll_line(event, repo, facts, str(review.get("commit_id") or ""))
    if line:
        post(repo, pr.get("number"), line)


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

    prs = check.get("pull_requests") or []

    if action == "completed":
        if conclusion == "success":
            msg = quips.pick("ci_pass")
            for pr in prs:
                reds = sigh.get(repo, pr["number"])
                sigh.reset(repo, pr["number"])
                # Once per recovery: the first green leg resets the streak,
                # so later legs of the same head see 0 and say nothing.
                head = str(check.get("head_sha") or "")
                if (reds >= troll.RECOVERY_REDS and not _mode_override()
                        and _may(repo, pr["number"], f"recovered:{head}", None, bot=True)):
                    line = _troll_line("ci_recovered", repo, {"reds": reds}, head)
                    if line:
                        post(repo, pr["number"], line)
        elif conclusion in ("failure", "timed_out", "startup_failure"):
            msg = quips.pick("ci_fail")
            for pr in prs:
                streak = sigh.bump_fail(repo, pr["number"])
                line = sigh.sigh_line(streak)
                if line:
                    msg = line
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
