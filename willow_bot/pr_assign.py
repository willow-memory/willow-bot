"""Operator assignment for bot-opened PRs.

Gap ``acfd27ae3259`` (assignment sub-part). When willow-bot opens a PR
it lands in the fleet with nobody assigned and nobody's review
requested, so it does not reach the operator's queue and stays
invisible until the operator scans the org's PR list. This module
assigns the PR to the operator named in ``WILLOW_OPERATOR_GITHUB_LOGIN``
and requests their review, in one idempotent call.

One operation:

- ``assign_to_operator(repo, pr_number, *, login=None)`` — with no
  argument, reads ``WILLOW_OPERATOR_GITHUB_LOGIN`` from the env; the
  argument is an explicit override for callers that already know the
  login. Unset env is ``status="absent"`` — honest absence, not error —
  so a fleet without an operator wired does not see refusal noise.

Assignee and requested-reviewer are separate GitHub APIs and this call
does both. Either one succeeding on its own is worth reporting; a full
success requires both. The receipt names exactly which half succeeded
so a retry can hit only the miss.

Idempotency: GitHub silently keeps a duplicate assignee POST idempotent
(a login already assigned returns 200 with no change). For requested
reviewers, GitHub returns 422 when a login is already a reviewer — this
step treats that 422 as "already requested", which is the same shape as
the 404-on-DELETE rule in pr_labels.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

import github_app

log = logging.getLogger("willow-bot.pr_assign")


ENV_OPERATOR = "WILLOW_OPERATOR_GITHUB_LOGIN"


def operator_login_from_env() -> str:
    """Empty string when unset — an operator's login is a string GitHub
    knows, so a blank env is the same as unset, and the caller reads
    ``status="absent"`` rather than an assignment to ``""``."""
    return os.environ.get(ENV_OPERATOR, "").strip()


def _add_assignee(repo: str, pr_number: int, login: str, headers: dict) -> list[str]:
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/assignees",
        headers=headers,
        json={"assignees": [login]},
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict):
        return []
    assignees = payload.get("assignees") or []
    return [a.get("login", "") for a in assignees if isinstance(a, dict)]


def _request_reviewer(repo: str, pr_number: int, login: str, headers: dict) -> bool:
    """POST /requested_reviewers. Returns True when the reviewer is on
    the requested list after this call — whether we added them (201) or
    GitHub said they were already there (422 with the specific message).

    The API's 422 is used for several conditions; we can only distinguish
    "already requested" from other 422 causes (author cannot review their
    own PR, invalid login) by matching on the message. We take the safer
    read: any 422 is reported as ``already_or_refused`` in the receipt,
    with the raw error, so the operator sees exactly what GitHub said."""
    r = requests.post(
        f"https://api.github.com/repos/{repo}/pulls/{pr_number}/requested_reviewers",
        headers=headers,
        json={"reviewers": [login]},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return True
    if r.status_code == 422:
        return False  # already or refused — the caller reads the raw error
    r.raise_for_status()
    return False


def assign_to_operator(
    repo: str,
    pr_number: int,
    *,
    login: str | None = None,
) -> dict[str, Any]:
    """Assign the PR to the operator and request their review.

    Returns a receipt. Splits assigning from reviewing so an operator
    who cannot review (a bot author reviewing their own PR, an org
    permission that blocks review requests, a mistyped login) still
    gets assigned — the two are independently useful, and the caller
    reads which half landed.
    """
    if login is None:
        login = operator_login_from_env()
    receipt: dict[str, Any] = {"repo": repo, "pr": pr_number, "operator": login}
    if not login:
        receipt.update(status="absent", detail=f"{ENV_OPERATOR} not set", action="skipped",
                       assigned=False, review_requested=False)
        return receipt

    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400], action="skipped",
                       assigned=False, review_requested=False)
        return receipt

    assigned = False
    assign_detail: str | None = None
    try:
        current = _add_assignee(repo, pr_number, login, headers)
        assigned = login in current
        if not assigned:
            # GitHub silently drops an assignee it does not recognise (a
            # mistyped login, a login without repo access). The API
            # answers 200 with the ORIGINAL assignees list, so a login
            # not in the response is a signal, not an error.
            assign_detail = f"assignee {login} not in returned list — unreachable login?"
    except Exception as exc:  # noqa: BLE001
        assign_detail = f"assign: {exc}"[:300]

    review_requested = False
    review_detail: str | None = None
    try:
        review_requested = _request_reviewer(repo, pr_number, login, headers)
        if not review_requested:
            review_detail = f"reviewer {login} not accepted (already requested or refused)"
    except Exception as exc:  # noqa: BLE001
        review_detail = f"review: {exc}"[:300]

    receipt["assigned"] = assigned
    receipt["review_requested"] = review_requested
    detail_bits = [d for d in (assign_detail, review_detail) if d]
    if detail_bits:
        receipt["detail"] = "; ".join(detail_bits)
    if assigned and review_requested:
        receipt["status"] = "ok"
        receipt["action"] = "assigned"
    elif assigned or review_requested:
        receipt["status"] = "partial"
        receipt["action"] = "partial"
    else:
        receipt["status"] = "could-not-run"
        receipt["action"] = "skipped"
    return receipt
