"""contribute.py — for any GitHub user, what they can do next in the projects
they are working on.

``willow-bot-contribute [--user LOGIN] [--days N] [--json]``

Whoever runs it is the subject: the login comes from ``--user``, else from
the token's own ``/user``. The token is that person's own (``GITHUB_TOKEN``,
``GH_TOKEN``, or their ``gh`` login), or none at all for public data at a
lower rate limit.
This module never reads the App's credentials or the vault: it acts as the
person running it, not as ``willows-bot``.

What it reports, from GitHub's search API:

- **projects** — every repo the user touched in the window (authored PRs or
  issues, reviews asked of them, assignments), split into ``own`` (owner is
  the user) and ``others`` (someone else's project they contribute to).
- **next** — what is waiting on them: their open PRs, reviews requested of
  them, issues assigned to them.
- **openings** — unassigned ``good first issue`` / ``help wanted`` issues in
  the other people's projects they already work on.

Read-only: it searches; it never comments, opens or edits anything.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.github.com"
# GitHub's login grammar: alphanumerics and single hyphens, 1-39 chars.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
OPENING_LABELS = ('"good first issue"', '"help wanted"')


class ContributeError(RuntimeError):
    pass


def _token() -> str:
    """The runner's own token: ``GITHUB_TOKEN``, ``GH_TOKEN``, else whatever
    the runner's ``gh`` is signed in as (``gh auth token``), else ""."""
    env = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    return env or _gh_token()


def _gh_token() -> str:
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def valid_login(login: object) -> str:
    """The login if it is one GitHub could have issued, else ``ContributeError``.
    The value goes into a search query; nothing else may ride along."""
    if not isinstance(login, str) or not _LOGIN_RE.match(login):
        raise ContributeError(f"not a GitHub login: {login!r}")
    return login


def whoami(token: str) -> str:
    if not token:
        raise ContributeError("no --user given and no GITHUB_TOKEN/GH_TOKEN to ask GitHub who you are")
    r = requests.get(f"{API}/user", headers=_headers(token), timeout=10)
    r.raise_for_status()
    return valid_login(r.json().get("login"))


def search(query: str, token: str, *, per_page: int = 50, max_pages: int = 1) -> list[dict]:
    """``/search/issues``, newest-updated first, up to ``max_pages`` pages.
    Raises on an HTTP error (rate limit included) so the caller can record
    it per section."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(f"{API}/search/issues", headers=_headers(token),
                         params={"q": query, "per_page": per_page, "page": page,
                                 "sort": "updated", "order": "desc"},
                         timeout=15)
        r.raise_for_status()
        items = r.json().get("items")
        if not isinstance(items, list):
            raise ContributeError(f"unexpected search answer for {query!r}")
        out.extend(i for i in items if isinstance(i, dict))
        if len(items) < per_page:
            break
    return out


def open_prs(login: str, token: str, *, max_pages: int = 10) -> list[dict]:
    """Every open PR ``login`` authored, anywhere on GitHub: ``repo``,
    ``number``, ``title``, ``url``, ``created_at``, ``updated_at``,
    ``is_draft``. The one search both this CLI and the steward's upstream
    desk use."""
    login = valid_login(login)
    items = search(f"is:pr is:open archived:false author:{login}", token, per_page=100, max_pages=max_pages)
    return [{"repo": _repo_of(i), "number": i.get("number"), "title": i.get("title", ""),
             "url": i.get("html_url", ""), "created_at": i.get("created_at"),
             "updated_at": i.get("updated_at"), "is_draft": bool(i.get("draft"))} for i in items]


def _repo_of(item: dict) -> str:
    # repository_url is https://api.github.com/repos/{owner}/{name}
    url = str(item.get("repository_url") or "")
    return url.split("/repos/", 1)[1] if "/repos/" in url else ""


def _brief(item: dict) -> dict:
    return {
        "repo": _repo_of(item),
        "number": item.get("number"),
        "title": item.get("title", ""),
        "url": item.get("html_url", ""),
        "draft": bool(item.get("draft")),
        "updated_at": item.get("updated_at"),
        "author": (item.get("user") or {}).get("login", ""),
    }


def report(login: str | None = None, *, token: str | None = None, days: int = 90,
           max_opening_repos: int | None = None, now: datetime | None = None) -> dict:
    token = _token() if token is None else token
    login = valid_login(login) if login else whoami(token)
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).date().isoformat()
    # Search allows 30 requests/min with a token, 10 without; five queries
    # are fixed, the rest go to openings.
    if max_opening_repos is None:
        max_opening_repos = 8 if token else 3

    queries = {
        "my_open_prs": f"is:pr is:open archived:false author:{login}",
        "review_requested": f"is:pr is:open archived:false review-requested:{login}",
        "assigned": f"is:issue is:open archived:false assignee:{login}",
        "authored_recently": f"involves:{login} updated:>={since}",
        "merged_recently": f"is:pr is:merged author:{login} merged:>={since}",
    }
    found: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for key, q in queries.items():
        try:
            found[key] = search(q, token)
        except Exception as exc:  # noqa: BLE001 — one section's failure never hides another's
            found[key] = []
            errors[key] = str(exc)

    activity: dict[str, int] = {}
    for items in found.values():
        for item in items:
            repo = _repo_of(item)
            if repo:
                activity[repo] = activity.get(repo, 0) + 1
    ranked = sorted(activity, key=lambda r: (-activity[r], r))
    own = [r for r in ranked if r.split("/", 1)[0].lower() == login.lower()]
    others = [r for r in ranked if r not in own]

    openings: list[dict] = []
    for repo in others[:max_opening_repos]:
        q = f"repo:{repo} is:issue is:open no:assignee label:{','.join(OPENING_LABELS)}"
        try:
            openings.extend(_brief(i) for i in search(q, token, per_page=10))
        except Exception as exc:  # noqa: BLE001
            errors[f"openings:{repo}"] = str(exc)

    return {
        "user": login,
        "authenticated": bool(token),
        "since": since,
        "projects": {
            "own": [{"repo": r, "activity": activity[r]} for r in own],
            "others": [{"repo": r, "activity": activity[r]} for r in others],
        },
        "next": {
            "my_open_prs": [_brief(i) for i in found["my_open_prs"]],
            "review_requested": [_brief(i) for i in found["review_requested"]],
            "assigned": [_brief(i) for i in found["assigned"]],
        },
        "merged_recently": [_brief(i) for i in found["merged_recently"]],
        "openings": openings,
        "errors": errors,
    }


def render(rep: dict) -> str:
    """The report as plain text for a terminal."""
    out = [f"{rep['user']} — since {rep['since']}"
           + ("" if rep["authenticated"] else "  (no token: public data, lower limits)")]

    def section(title: str, rows: list[dict], fmt) -> None:
        out.append(f"\n{title} ({len(rows)})")
        out.extend(f"  {fmt(r)}" for r in rows) if rows else out.append("  —")

    item = lambda r: f"{r['repo']}#{r['number']} {'[draft] ' if r.get('draft') else ''}{r['title']}  {r['url']}"  # noqa: E731
    section("Other people's projects you work on", rep["projects"]["others"],
            lambda r: f"{r['repo']} ({r['activity']})")
    section("Your own projects", rep["projects"]["own"], lambda r: f"{r['repo']} ({r['activity']})")
    section("Your open PRs", rep["next"]["my_open_prs"], item)
    section("Reviews requested of you", rep["next"]["review_requested"], item)
    section("Issues assigned to you", rep["next"]["assigned"], item)
    section("Openings in projects you contribute to", rep["openings"], item)
    section("Merged recently", rep["merged_recently"], item)
    if rep["errors"]:
        out.append("\nCould not read:")
        out.extend(f"  {k}: {v}" for k, v in rep["errors"].items())
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="willow-bot-contribute",
                                description="What you can do next in the projects you work on.")
    p.add_argument("--user", help="GitHub login (default: the token's own user)")
    p.add_argument("--days", type=int, default=90, help="activity window (default 90)")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        rep = report(a.user, days=a.days)
    except (ContributeError, requests.RequestException) as exc:
        print(f"willow-bot-contribute: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2) if a.json else render(rep))
    return 0
