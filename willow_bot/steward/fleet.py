"""Fleet scope for Loki PR watch — the operator's GitHub orgs (+ their user account).

Who the operator is comes from the install's operator profile
(``willow_bot.operator_profile``), never from code."""
from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from typing import Iterator

from willow_bot import operator_profile



def gh_api(path: str):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


#: What the last scan could NOT list (gap 52928edb3fc7, Loki 25CBCB13). The
#: per-repo / per-org `except CalledProcessError` below keep one failing call
#: from killing the whole scan — right — but they also made a partial scan
#: indistinguishable from a complete one: a repo whose `pulls` call failed
#: simply had no open PRs as far as `state['open']` could tell, and a
#: consumer that reads "not in the open set" as "closed on GitHub" (the
#: legacy clear, the stuck backfill) would retire a live PR's item. Each
#: swallowed failure now lands here as ``org:<login>``, ``user:<login>`` or
#: ``repo:<full_name>``; ``iter_open_pulls`` resets it at the start of a scan
#: and ``run_once`` copies it into the scan record.
last_scan_errors: list[str] = []


def _note_error(kind: str, name: str) -> None:
    key = f"{kind}:{name}"
    if key not in last_scan_errors:
        last_scan_errors.append(key)


@lru_cache(maxsize=1)
def fleet_orgs() -> tuple[str, ...]:
    """Org logins to scan: LOKI_PR_WATCH_ORGS (comma-separated), else the
    operator profile's ``orgs``, else the orgs ``gh`` says the operator is a
    member of, else none."""
    raw = os.environ.get("LOKI_PR_WATCH_ORGS", "").strip()
    if raw:
        return tuple(o.strip() for o in raw.split(",") if o.strip())
    named = operator_profile.orgs()
    if named is not None:
        return tuple(named)
    try:
        members = gh_api("/user/memberships/orgs?per_page=100")
        logins = [m["organization"]["login"] for m in members if m.get("organization")]
        if logins:
            return tuple(sorted(logins))
    except (subprocess.CalledProcessError, KeyError, TypeError, json.JSONDecodeError):
        pass
    return ()


def fleet_user() -> str:
    """The operator's login, or "" when it cannot be known (then no user
    repos are scanned)."""
    return operator_profile.login()


def repos_for_org(org: str) -> list[str]:
    names: list[str] = []
    page = 1
    while True:
        try:
            batch = gh_api(f"/orgs/{org}/repos?per_page=100&page={page}")
        except subprocess.CalledProcessError:
            _note_error("org", org)
            break
        if not batch:
            break
        names.extend(r["full_name"] for r in batch)
        if len(batch) < 100:
            break
        page += 1
    return names


def repos_for_user(user: str) -> list[str]:
    names: list[str] = []
    page = 1
    while True:
        try:
            batch = gh_api(
                f"/users/{user}/repos?per_page=100&page={page}&type=owner"
            )
        except subprocess.CalledProcessError:
            _note_error("user", user)
            break
        if not batch:
            break
        names.extend(r["full_name"] for r in batch)
        if len(batch) < 100:
            break
        page += 1
    return names


def fleet_repos() -> list[str]:
    names: set[str] = set()
    for org in fleet_orgs():
        names.update(repos_for_org(org))
    user = fleet_user()
    if user and os.environ.get("LOKI_PR_WATCH_SKIP_USER", "").strip() not in ("1", "true", "yes"):
        names.update(repos_for_user(user))
    return sorted(names)


def iter_open_pulls() -> Iterator[tuple[str, int, str, str]]:
    """Yield (full_name, number, title, html_url) for every open PR in fleet scope.

    A repo whose listing fails is skipped AND recorded in ``last_scan_errors``
    — the scan goes on, but it must not read as complete for that repo."""
    last_scan_errors.clear()
    for repo in fleet_repos():
        try:
            pulls = gh_api(f"repos/{repo}/pulls?state=open&per_page=50")
        except subprocess.CalledProcessError:
            _note_error("repo", repo)
            continue
        for p in pulls:
            yield (
                repo,
                int(p["number"]),
                str(p.get("title") or ""),
                str(p.get("html_url") or ""),
            )


def repo_matches_filters(repo: str, num: int, filters: list[str]) -> bool:
    if not filters:
        return True
    key = f"{repo}#{num}"
    for f in filters:
        if f == key or f in key:
            return True
        if "/" not in f and repo.startswith(f"{f}/"):
            return True
        if f == repo or repo.startswith(f):
            return True
    return False
