"""contributions.py — a ledger of who contributes to the repos the App sees.

Every verified webhook passes through ``record``. The events that are a
contribution (or a sign of interest: a fork, a star) become one line in
``$WILLOW_HOME/willow-bot/contributions.jsonl``; everything else is ignored.
``summary`` reads the ledger back as counts per repo, per kind and per
person, with outside contributors and first-timers called out.

What a row holds: repo, kind, action, number, title, url, the actor's
login, ``type`` and ``author_association`` as GitHub asserted them. Never a
body. Bots are recognised by ``type == "Bot"``, never by login
(BOT-INVENTORY.md); their rows are kept and marked, and ``summary`` leaves
them out of the people counts.

Read-only toward GitHub: this module makes no API call and posts nothing.
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("willow-bot.contributions")

# GitHub's author_association values for people with standing in the repo.
# Anyone else (CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, NONE) is
# an outside contributor. Same set as troll._INSIDERS.
INSIDERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
FIRST_TIMERS = frozenset({"FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"})


def ledger_path() -> Path:
    from willow_bot.paths import bot_dir

    return bot_dir() / "contributions.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(obj: dict | None) -> tuple[str, str]:
    obj = obj or {}
    return str(obj.get("login") or ""), str(obj.get("type") or "")


def _row(kind: str, payload: dict, *, actor: dict | None, association: object = None,
         number: object = None, title: str = "", url: str = "", **extra: Any) -> dict:
    login, actor_type = _actor(actor)
    assoc = str(association or "").upper()
    row = {
        "at": _now(),
        "repo": str((payload.get("repository") or {}).get("full_name") or ""),
        "kind": kind,
        "action": payload.get("action"),
        "number": number,
        "title": str(title or "")[:200],
        "url": url or "",
        "login": login,
        "actor_type": actor_type,
        "association": assoc,
        "bot": actor_type == "Bot",
        "outsider": actor_type != "Bot" and assoc not in INSIDERS,
        "first_timer": assoc in FIRST_TIMERS,
    }
    row.update(extra)
    return row


def extract(event: str, payload: dict) -> dict | None:
    """The ledger row for this webhook, or ``None`` when it is not a
    contribution. Pure: no filesystem, no network."""
    action = payload.get("action")

    if event == "pull_request" and action in ("opened", "closed", "reopened"):
        pr = payload.get("pull_request") or {}
        kind = "pr_merged" if action == "closed" and pr.get("merged") else f"pr_{action}"
        return _row(kind, payload, actor=pr.get("user"), association=pr.get("author_association"),
                    number=pr.get("number"), title=pr.get("title", ""), url=pr.get("html_url", ""),
                    additions=pr.get("additions"), deletions=pr.get("deletions"))

    if event == "issues" and action in ("opened", "closed", "reopened"):
        issue = payload.get("issue") or {}
        return _row(f"issue_{action}", payload, actor=issue.get("user"),
                    association=issue.get("author_association"), number=issue.get("number"),
                    title=issue.get("title", ""), url=issue.get("html_url", ""))

    if event == "issue_comment" and action == "created":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        return _row("pr_comment" if "pull_request" in issue else "issue_comment", payload,
                    actor=comment.get("user"), association=comment.get("author_association"),
                    number=issue.get("number"), title=issue.get("title", ""),
                    url=comment.get("html_url", ""))

    if event == "pull_request_review" and action == "submitted":
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        return _row("review", payload, actor=review.get("user"),
                    association=review.get("author_association"), number=pr.get("number"),
                    title=pr.get("title", ""), url=review.get("html_url", ""),
                    state=str(review.get("state") or "").lower())

    if event == "pull_request_review_comment" and action == "created":
        comment = payload.get("comment") or {}
        pr = payload.get("pull_request") or {}
        return _row("review_comment", payload, actor=comment.get("user"),
                    association=comment.get("author_association"), number=pr.get("number"),
                    title=pr.get("title", ""), url=comment.get("html_url", ""))

    if event == "discussion" and action == "created":
        disc = payload.get("discussion") or {}
        return _row("discussion", payload, actor=disc.get("user"),
                    association=disc.get("author_association"), number=disc.get("number"),
                    title=disc.get("title", ""), url=disc.get("html_url", ""))

    if event == "fork":
        forkee = payload.get("forkee") or {}
        return _row("fork", payload, actor=payload.get("sender"),
                    title=forkee.get("full_name", ""), url=forkee.get("html_url", ""))

    if event == "watch" and action == "started":
        return _row("star", payload, actor=payload.get("sender"))

    if event == "release" and action == "published":
        rel = payload.get("release") or {}
        return _row("release", payload, actor=rel.get("author"), association="OWNER",
                    title=rel.get("tag_name", ""), url=rel.get("html_url", ""))

    return None


def record(event: str, payload: dict) -> dict | None:
    """Append this webhook's ledger row, if it has one. Returns the row."""
    row = extract(event, payload)
    if row is None or not row["repo"]:
        return None
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n")
    return row


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line is skipped, not fatal
        if isinstance(row, dict):
            rows.append(row)
    return rows


def summary(*, days: int | None = 30, repo: str | None = None, path: Path | None = None,
            now: datetime | None = None) -> dict:
    """Counts over the ledger: totals per kind, per repo, per person (bots
    left out), outside contributors, and first-timers. ``days=None`` reads
    the whole ledger."""
    rows = _read(path or ledger_path())
    cutoff = None
    if days is not None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    kept = []
    for r in rows:
        if repo and str(r.get("repo", "")).lower() != repo.lower():
            continue
        if cutoff is not None:
            try:
                if datetime.fromisoformat(str(r.get("at"))) < cutoff:
                    continue
            except ValueError:
                continue
        kept.append(r)

    by_kind = Counter(r.get("kind") for r in kept)
    by_repo: dict[str, Counter] = defaultdict(Counter)
    people: dict[str, Counter] = defaultdict(Counter)
    outsiders: dict[str, Counter] = defaultdict(Counter)
    first_timers: list[dict] = []
    for r in kept:
        by_repo[r.get("repo", "")][r.get("kind")] += 1
        if r.get("bot") or not r.get("login"):
            continue
        people[r["login"]][r.get("kind")] += 1
        if r.get("outsider") and r.get("kind") not in ("star", "fork"):
            outsiders[r["login"]][r.get("kind")] += 1
        if r.get("first_timer"):
            first_timers.append({k: r.get(k) for k in ("repo", "kind", "number", "login", "title", "url", "at")})

    def _ranked(d: dict[str, Counter]) -> list[dict]:
        return [{"login": who, "total": sum(c.values()), "kinds": dict(c)}
                for who, c in sorted(d.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))]

    return {
        "days": days,
        "repo": repo,
        "rows": len(kept),
        "by_kind": dict(by_kind),
        "by_repo": {k: dict(v) for k, v in sorted(by_repo.items())},
        "people": _ranked(people),
        "outside_contributors": _ranked(outsiders),
        "first_timers": first_timers,
    }


def _backfill_rows(repo: str, pulls: list[dict], issues: list[dict], cutoff: datetime) -> list[dict]:
    """Ledger rows from GitHub's PR and issue lists, each stamped with when
    it happened (not now), within ``cutoff``. Comments, reviews, stars and
    forks are not listed this way and are not backfilled."""
    out: list[dict] = []

    def _when(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return t.isoformat() if t >= cutoff else None

    def _add(kind: str, at: str | None, item: dict, **extra: Any) -> None:
        if at is None:
            return
        row = _row(kind, {"repository": {"full_name": repo}, "action": kind.split("_", 1)[1]},
                   actor=item.get("user"), association=item.get("author_association"),
                   number=item.get("number"), title=item.get("title", ""), url=item.get("html_url", ""),
                   **extra)
        row["at"] = at
        row["backfill"] = True
        out.append(row)

    for pr in pulls:
        _add("pr_opened", _when(pr.get("created_at")), pr)
        if pr.get("merged_at"):
            _add("pr_merged", _when(pr.get("merged_at")), pr)
        elif pr.get("closed_at"):
            _add("pr_closed", _when(pr.get("closed_at")), pr)
    for issue in issues:
        _add("issue_opened", _when(issue.get("created_at")), issue)
        if issue.get("closed_at"):
            _add("issue_closed", _when(issue.get("closed_at")), issue)
    return out


def backfill(repos: list[str], *, days: int = 90, path: Path | None = None,
             now: datetime | None = None, fetch=None) -> dict:
    """Fill the ledger from GitHub's PR and issue history for ``repos``,
    under the App's install token. A row already in the ledger (same repo,
    kind and number) is not written twice, so a re-run adds only what is
    new. Returns counts per repo, and the error for a repo that could not
    be read."""
    path = path or ledger_path()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    if fetch is None:
        import github_app

        def fetch(repo: str) -> tuple[list[dict], list[dict]]:
            return (github_app.list_recent_pulls(repo, max_pages=3),
                    github_app.list_recent_issues(repo, since=cutoff.isoformat(), max_pages=3))

    have = {(r.get("repo"), r.get("kind"), r.get("number")) for r in _read(path)}
    result: dict[str, Any] = {"days": days, "repos": {}, "errors": {}}
    new_rows: list[dict] = []
    for repo in repos:
        try:
            pulls, issues = fetch(repo)
        except Exception as exc:  # noqa: BLE001 — one unreadable repo never stops the rest
            result["errors"][repo] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        added = 0
        for row in _backfill_rows(repo, pulls, issues, cutoff):
            key = (row["repo"], row["kind"], row["number"])
            if key in have:
                continue
            have.add(key)
            new_rows.append(row)
            added += 1
        result["repos"][repo] = added
    if new_rows:
        new_rows.sort(key=lambda r: r["at"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for row in new_rows:
                f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n")
    result["added"] = len(new_rows)
    return result


def main(argv: list[str]) -> int:
    """``willow-bot-steward contributions [--days N|--all] [--repo owner/name] [--backfill]``"""
    import argparse

    p = argparse.ArgumentParser(prog="willow-bot-steward contributions")
    p.add_argument("--days", type=int, default=None, help="window (default 30; 90 with --backfill)")
    p.add_argument("--all", action="store_true", help="read the whole ledger")
    p.add_argument("--repo", action="append", default=None,
                   help="owner/name; repeat for several (backfill default: the profile's core_repos)")
    p.add_argument("--backfill", action="store_true",
                   help="fill the ledger from GitHub's PR and issue history, under the App's token")
    a = p.parse_args(argv)
    if a.backfill:
        from willow_bot import operator_profile

        repos = a.repo or operator_profile.core_repos()
        if not repos:
            print("contributions --backfill: no repos; pass --repo owner/name or set core_repos "
                  "in the operator profile", file=sys.stderr)
            return 2
        print(json.dumps(backfill(repos, days=a.days or 90), indent=2))
        return 0
    repo = a.repo[0] if a.repo else None
    print(json.dumps(summary(days=None if a.all else (a.days or 30), repo=repo), indent=2))
    return 0
