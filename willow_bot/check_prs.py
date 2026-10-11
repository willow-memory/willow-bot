"""check_prs.py — which of a check run's `pull_requests` belong to its repo.

GitHub lists, on a check run, the open PRs whose HEAD matches the check's
sha or branch. A check that runs on a fork therefore also lists the fork's
PR into the upstream repo, whose number means nothing in the fork: filed
as ``<fork>#<n>``, the bot commented on a PR that does not exist (a 404
retried every tick, forever) or, worse, on an unrelated issue that happens
to share the number. Only a PR whose BASE repo is the check's own repo is
a PR of that repo.
"""
from __future__ import annotations


def _base_full_name(pr: dict) -> str:
    """``owner/name`` of the PR's base repo, from what GitHub sends: the base
    repo's API url (``…/repos/owner/name``), else the PR's own url
    (``…/repos/owner/name/pulls/N``). "" when the entry carries neither."""
    base_repo = ((pr.get("base") or {}).get("repo") or {})
    for url in (base_repo.get("url"), pr.get("url")):
        if isinstance(url, str) and "/repos/" in url:
            rest = url.split("/repos/", 1)[1].split("/")
            if len(rest) >= 2:
                return f"{rest[0]}/{rest[1]}"
    return ""


def pr_numbers(check: dict, repo: str) -> list[int]:
    """The numbers of the check's PRs that are PRs of ``repo``. An entry that
    names no repo at all is kept (older payload shapes, fixtures)."""
    out: list[int] = []
    for pr in check.get("pull_requests") or []:
        if not isinstance(pr, dict) or pr.get("number") is None:
            continue
        base = _base_full_name(pr)
        if base and base.lower() != str(repo).lower():
            continue
        out.append(pr["number"])
    return out
