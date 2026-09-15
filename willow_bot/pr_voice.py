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


def comment_marker(head_sha: str) -> str:
    """The idempotency marker embedded in the comment body. A single call
    site so a rename of the marker (rare) stays consistent everywhere."""
    return f"{COMMENT_MARKER_PREFIX}{head_sha}{COMMENT_MARKER_SUFFIX}"


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
    """Post one status comment for this (repo, pr, head_sha), or update
    the existing one. Returns a receipt.

    The comment body is ``<marker>\\n\\n<body>``. A caller that keeps its
    own header inside ``body`` gets a stable-shape comment across ticks;
    the marker is invisible in the rendered PR and unique per head_sha.
    """
    if not head_sha:
        return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
    receipt: dict[str, Any] = {
        "repo": repo, "pr": pr_number, "head_sha": head_sha,
    }
    marker = comment_marker(head_sha)
    full_body = f"{marker}\n\n{body}".strip() + "\n"
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

    existing = next((c for c in comments if _has_marker(c.get("body", ""), head_sha)), None)
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
    not need)."""
    params = {"filter": "app", "per_page": 100}
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
    return runs if isinstance(runs, list) else []


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
