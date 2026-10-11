"""Bot voice on a PR: one status comment and one bot check per head SHA.

Gap ``acfd27ae3259`` (voice sub-part). The bot has been silent on the
PRs it stewards: no comment, no check, no label. A seat could learn a PR
was watched only by reading the steward's local receipt file. This module
gives the bot a visible, idempotent voice on GitHub itself, keyed on the
head SHA so repeated events for the same commit do not multiply comments
or checks.

Two operations, both safe to call more than once per head SHA:

- ``upsert_status_comment(repo, pr_number, head_sha, body)`` — post one
  comment carrying an HTML marker ``<!-- willow-bot:status head=<sha> -->``.
  A repeat call for the same head_sha updates the same comment; a new head
  writes a new comment (a force-push moved the world, so the seat needs a
  fresh row rather than a rewritten one).
- ``publish_check(repo, head_sha, name, status, conclusion=None,
  output=None, external_id=None)`` — create or update one check-run under
  the App for that (head_sha, name). A repeat call for the same name
  PATCHes the same check; different names publish side by side.

Neither posts a login: matching is by the marker for comments and by
``(check_run.id, filter=app)`` for checks, so a rename of the App bot
does not orphan history (BOT-INVENTORY.md: match by type, not login).

Every call returns a receipt dict. On a HTTP failure we return the
receipt with ``status: "could-not-run"`` and never raise; the seat reads
what could not run. Auth comes from ``github_app._auth_headers``.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

import github_app

log = logging.getLogger("willow-bot.pr_voice")


COMMENT_MARKER_PREFIX = "<!-- willow-bot:status head="
COMMENT_MARKER_SUFFIX = " -->"

# The name of the bot's own check-run, one per head SHA. A single call
# site so a rename lands in one place; `steward.voice` is the only caller.
CHECK_NAME = "willow-bot/steward"


def comment_marker(head_sha: str) -> str:
    """The idempotency marker embedded in the comment body. A single call
    site so a rename of the marker (rare) stays consistent everywhere."""
    return f"{COMMENT_MARKER_PREFIX}{head_sha}{COMMENT_MARKER_SUFFIX}"


#: One sticky status comment per PR. The per-head marker above opened a new
#: comment on every push; a PR now has one, edited in place, and names its
#: head in the body. A comment carrying an older per-head marker is adopted
#: (rewritten with this marker) so an existing PR does not gain a second one.
PR_STATUS_MARKER = "<!-- willow-bot:status pr -->"


def _is_status_comment(body: str) -> bool:
    return PR_STATUS_MARKER in body or COMMENT_MARKER_PREFIX in body


def _has_marker(body: str, head_sha: str) -> bool:
    if not body:
        return False
    return comment_marker(head_sha) in body


# ── comments ────────────────────────────────────────────────────────────────


def _list_pr_comments(repo: str, pr_number: int, headers: dict) -> list[dict]:
    """Every issue comment on a PR (issue comments, not review comments —
    the two live at different paths). The list is bounded by the page
    param; a PR with 100+ comments hits the second page, and this step
    keeps walking until either the marker is found or the pages run out."""
    out: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=10,
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def _by_a_bot(comment: dict) -> bool:
    """Only a Bot-authored comment can be ours to edit: a person quoting the
    marker would otherwise be PATCHed (a 403 for the App, every tick). By
    `type`, never login (BOT-INVENTORY.md)."""
    return (comment.get("user") or {}).get("type") == "Bot"


def _create_comment(repo: str, pr_number: int, body: str, headers: dict) -> dict:
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        headers=headers,
        json={"body": body},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _update_comment(repo: str, comment_id: int, body: str, headers: dict) -> dict:
    r = requests.patch(
        f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}",
        headers=headers,
        json={"body": body},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def upsert_status_comment(
    repo: str, pr_number: int, head_sha: str, body: str
) -> dict[str, Any]:
    """Post the PR's one status comment, or update it. Returns a receipt.

    The comment body is ``<PR marker>\\n\\n<body>``; the body names the head.
    One comment per PR, edited in place across pushes (it used to be one
    per head, so every push added a comment). An unchanged body is not
    PATCHed (``action="unchanged"``): no edit, no webhook, no notification.
    """
    if not head_sha:
        return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
    receipt: dict[str, Any] = {
        "repo": repo, "pr": pr_number, "head_sha": head_sha,
    }
    full_body = f"{PR_STATUS_MARKER}\n\n{body}".strip() + "\n"
    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001 — an unavailable App is a line, not a raise
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400], action="skipped")
        return receipt
    try:
        comments = _list_pr_comments(repo, pr_number, headers)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"list: {exc}"[:400], action="skipped")
        return receipt

    ours = [c for c in comments if _by_a_bot(c) and _is_status_comment(c.get("body", ""))]
    # The sticky one if it exists, else the newest per-head one to adopt.
    existing = next((c for c in ours if PR_STATUS_MARKER in c.get("body", "")), None) or (
        max(ours, key=lambda c: int(c.get("id") or 0)) if ours else None)
    if existing and (existing.get("body") or "").strip() == full_body.strip():
        receipt.update(status="ok", action="unchanged", comment_id=int(existing["id"]),
                       url=existing.get("html_url", ""))
        return receipt
    try:
        if existing:
            result = _update_comment(repo, int(existing["id"]), full_body, headers)
            receipt.update(status="ok", action="updated", comment_id=int(result.get("id", existing["id"])),
                           url=result.get("html_url", existing.get("html_url", "")))
        else:
            result = _create_comment(repo, pr_number, full_body, headers)
            receipt.update(status="ok", action="created", comment_id=int(result.get("id", 0)),
                           url=result.get("html_url", ""))
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run",
                       detail=f"{'update' if existing else 'create'}: {exc}"[:400],
                       action="skipped")
    return receipt


# ── the CI-red comment ───────────────────────────────────────────────────────
#
# A second, distinct marker from the status comment above: this one carries
# the failure block the steward pulled from the job log (`steward.ci_log`),
# not the terse audit/CI-red-count summary `upsert_status_comment` carries.
# The two never collide on the same PR because they key on different marker
# text; a head with both a status comment and a ci-red comment gets two
# comments, each independently idempotent on its own marker.

CI_RED_MARKER_PREFIX = "<!-- willow-bot:ci-red:"
CI_RED_MARKER_SUFFIX = " -->"


def ci_red_comment_marker(head_sha: str) -> str:
    return f"{CI_RED_MARKER_PREFIX}{head_sha}{CI_RED_MARKER_SUFFIX}"


def _has_ci_red_marker(body: str, head_sha: str) -> bool:
    if not body:
        return False
    return ci_red_comment_marker(head_sha) in body


def upsert_ci_red_comment(repo: str, pr_number: int, head_sha: str, body: str) -> dict[str, Any]:
    """Post or edit the ONE per-head-sha CI-red comment. Returns a receipt
    whose ``action`` is ``created`` | ``edited`` | ``skipped`` — the exact
    vocabulary the tick's ``commented`` receipt line carries.

    On a 403 while posting or editing, ``ci_log.classify_403`` decides
    what it means: a rate limit (``X-RateLimit-Remaining`` / ``Retry-After``)
    is reported as ``status: "rate_limited"``, never as a missing
    permission; only GitHub's own "you can't do this" wording in the
    response body names ``missing_permission: "pull_requests:write"``;
    anything else is ``forbidden_unknown`` with the message quoted.
    """
    if not head_sha:
        return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
    receipt: dict[str, Any] = {"repo": repo, "pr": pr_number, "head_sha": head_sha}
    marker = ci_red_comment_marker(head_sha)
    full_body = f"{marker}\n\n{body}".strip() + "\n"
    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400], action="skipped")
        return receipt
    try:
        comments = _list_pr_comments(repo, pr_number, headers)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"list: {exc}"[:400], action="skipped")
        return receipt

    existing = next((c for c in comments
                     if _by_a_bot(c) and _has_ci_red_marker(c.get("body", ""), head_sha)), None)
    try:
        if existing:
            r = requests.patch(
                f"https://api.github.com/repos/{repo}/issues/comments/{existing['id']}",
                headers=headers, json={"body": full_body}, timeout=10,
            )
        else:
            r = requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers=headers, json={"body": full_body}, timeout=10,
            )
        if getattr(r, "status_code", None) == 403:
            from willow_bot.steward.ci_log import classify_403

            cls = classify_403(r)
            if cls["kind"] == "rate_limited":
                receipt.update(status="rate_limited", detail="GitHub rate limit posting a PR comment",
                               retry_after=cls.get("retry_after"), action="skipped")
            elif cls["kind"] == "missing_permission":
                receipt.update(status="could-not-run", missing_permission="pull_requests:write",
                               detail=f"GitHub 403 ({cls['message']}) posting a PR comment — the App "
                                      "installation lacks pull_requests:write",
                               action="skipped")
            else:
                receipt.update(status="could-not-run", forbidden_unknown=True,
                               detail=f"GitHub 403 posting a PR comment: {cls['message']}"[:400],
                               action="skipped")
            return receipt
        r.raise_for_status()
        result = r.json()
        if existing:
            receipt.update(status="ok", action="edited", comment_id=int(result.get("id", existing["id"])),
                           url=result.get("html_url", existing.get("html_url", "")))
        else:
            receipt.update(status="ok", action="created", comment_id=int(result.get("id", 0)),
                           url=result.get("html_url", ""))
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run",
                       detail=f"{'update' if existing else 'create'}: {exc}"[:400],
                       action="skipped")
    return receipt


# ── checks ──────────────────────────────────────────────────────────────────


# Valid GitHub check-run status values. `queued` and `in_progress` are
# non-terminal; `completed` requires a `conclusion`.
_STATUSES = frozenset({"queued", "in_progress", "completed"})
# Valid `conclusion` values on a completed check.
_CONCLUSIONS = frozenset({
    "success", "failure", "neutral", "cancelled",
    "timed_out", "action_required", "skipped", "stale",
})


def _list_check_runs(repo: str, head_sha: str, headers: dict, *, check_name: str | None = None) -> list[dict]:
    """Every check-run this App has on that head_sha, optionally filtered
    to a single name (server-side filter, so we do not scan pages we do
    not need). GitHub's `filter` takes only `latest` / `all`; scoping to
    this App is `app_id`, and the answer is checked by each run's own
    `app.id`, so a same-named check from another app or workflow is never
    PATCHed."""
    app_id = _own_app_id()
    params: dict = {"filter": "latest", "per_page": 100}
    if app_id is not None:
        params["app_id"] = app_id
    if check_name:
        params["check_name"] = check_name
    r = requests.get(
        f"https://api.github.com/repos/{repo}/commits/{head_sha}/check-runs",
        headers=headers,
        params=params,
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return []
    runs = data.get("check_runs")
    if not isinstance(runs, list):
        return []
    if app_id is None:
        return runs
    return [r for r in runs if isinstance(r, dict) and (r.get("app") or {}).get("id") == app_id]


def _own_app_id() -> int | None:
    """This install's App id, or None when it cannot be read."""
    try:
        return int(github_app._cred().app_id)
    except Exception:  # noqa: BLE001
        return None


def _create_check_run(repo: str, payload: dict, headers: dict) -> dict:
    r = requests.post(
        f"https://api.github.com/repos/{repo}/check-runs",
        headers=headers,
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _update_check_run(repo: str, check_run_id: int, payload: dict, headers: dict) -> dict:
    r = requests.patch(
        f"https://api.github.com/repos/{repo}/check-runs/{check_run_id}",
        headers=headers,
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def publish_check(
    repo: str,
    head_sha: str,
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = None,
    output: dict | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """Post one bot check for this (repo, head_sha, name), or update the
    existing one. Terminal checks (``status="completed"``) require a
    ``conclusion`` — the API rejects them otherwise. Non-terminal checks
    (``queued``, ``in_progress``) pass ``conclusion=None``.

    ``output`` is a ``{title, summary, text?}`` dict rendered on the
    Checks tab. ``external_id`` is echoed back and useful for a caller
    correlating this check with its own record id.
    """
    if not head_sha:
        return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
    if status not in _STATUSES:
        return {"status": "could-not-run", "detail": f"invalid status: {status}", "action": "skipped"}
    if status == "completed" and conclusion not in _CONCLUSIONS:
        return {"status": "could-not-run",
                "detail": f"completed check needs conclusion in {sorted(_CONCLUSIONS)}",
                "action": "skipped"}
    if status != "completed" and conclusion is not None:
        return {"status": "could-not-run",
                "detail": "conclusion only valid for status=completed",
                "action": "skipped"}
    receipt: dict[str, Any] = {"repo": repo, "head_sha": head_sha, "name": name}
    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400], action="skipped")
        return receipt

    # willows-bot holds checks:read, not write (BOT-INVENTORY): every create
    # answered 403 and the steward tick receipts carried "create: 403 …
    # /check-runs" once per open PR per tick. The token's own grant is
    # asked first; a grant GitHub states is not `write` skips the call.
    # Unknown (`None`) proceeds as before, and a later grant of
    # checks:write is picked up with the next minted token.
    perms = github_app.installation_permissions(repo)
    if perms is not None and perms.get("checks") != "write":
        receipt.update(status="skipped", action="skipped", missing_permission="checks:write",
                       detail=f"installation grants checks:{perms.get('checks') or 'none'}, not write")
        return receipt

    payload: dict[str, Any] = {"name": name, "head_sha": head_sha, "status": status}
    if conclusion is not None:
        payload["conclusion"] = conclusion
    if output is not None:
        payload["output"] = output
    if external_id is not None:
        payload["external_id"] = external_id

    try:
        existing_runs = _list_check_runs(repo, head_sha, headers, check_name=name)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"list: {exc}"[:400], action="skipped")
        return receipt
    existing = existing_runs[0] if existing_runs else None

    try:
        if existing:
            # A PATCH cannot change name or head_sha, so we drop them.
            patch_payload = {k: v for k, v in payload.items() if k not in ("name", "head_sha")}
            result = _update_check_run(repo, int(existing["id"]), patch_payload, headers)
            receipt.update(status="ok", action="updated", check_run_id=int(result.get("id", existing["id"])),
                           url=result.get("html_url", existing.get("html_url", "")))
        else:
            result = _create_check_run(repo, payload, headers)
            receipt.update(status="ok", action="created", check_run_id=int(result.get("id", 0)),
                           url=result.get("html_url", ""))
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run",
                       detail=f"{'update' if existing else 'create'}: {exc}"[:400],
                       action="skipped")
    return receipt
