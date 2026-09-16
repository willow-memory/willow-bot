"""
fleet_bridge.py — route willow-bot webhooks into local fleet services.

Push path (replaces blind polling for installed repos):
  pull_request / issues / issue_comment / check_run
    → ~/.willow/upstream_steward/webhook_inbox/<work_id>.json
  push (default branch, local clone exists)
    → ~/.willow/gitsync/trigger-<owner>-<repo>.flag, a JSON payload
      {at, repo, clone, layout} — layout is "org", "flat", or "scan",
      whichever shape of `_local_clone_path_with_layout` found the repo

All events also append to ~/.willow/willow-bot/event-log.jsonl for audit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("willow-bot.fleet_bridge")

_WILLOW_HOME = Path(os.environ.get("WILLOW_HOME", Path.home() / "github" / ".willow"))
if "WILLOW_HOME" not in os.environ:
    # The fallback is the pre-2026-08-10 layout and on a current box it is a
    # decoy path one level above the fleet's home. Say so once at import
    # rather than filing an inbox nothing reads; the fix is the env var in the
    # unit file, not a new default here (the real home moved once already).
    log_boot = logging.getLogger("willow-bot.fleet_bridge")
    log_boot.warning("WILLOW_HOME unset; fleet_bridge falls back to %s, which is the "
                     "pre-move layout — set WILLOW_HOME in the unit's EnvironmentFile",
                     _WILLOW_HOME)
_GITHUB_ROOT = Path(os.environ.get("GITHUB_ROOT", Path.home() / "github"))
_EVENT_LOG = _WILLOW_HOME / "willow-bot" / "event-log.jsonl"
_INBOX = _WILLOW_HOME / "upstream_steward" / "webhook_inbox"
_GITSYNC_TRIGGERS = _WILLOW_HOME / "gitsync"
_GIT = shutil.which("git") or "/usr/bin/git"

# GitHub's own grammar for `owner/name`: ASCII alphanumerics, `-`, `_`, `.`;
# neither side empty; a name never starts with `.` (so `..` cannot appear
# as a whole component). `repository.full_name` is the only payload field
# that becomes part of a filesystem path here (the trigger flag and the
# clone lookup), so it is checked against this once, at the edge, and an
# unexpected shape drops the event rather than reaching a path expression.
# The HMAC check in bot.py already says the payload is GitHub's; this says
# the value is a repo name and nothing else.
_REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*)/[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _valid_repo_full_name(value: object) -> str:
    """The `owner/name` if it matches GitHub's grammar, else "" — never a
    partially cleaned string. Length capped at what GitHub allows (39 + 1 +
    100) so a pathological value cannot become a long filename either."""
    if not isinstance(value, str) or len(value) > 140:
        return ""
    return value if _REPO_FULL_NAME_RE.match(value) else ""


def _under(root: Path, candidate: Path) -> bool:
    """True only when `candidate` resolves inside `root` — the belt to the
    regex's braces, so a path expression built from a repo name is refused
    outright if it ever escapes."""
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _work_id(repo: str, kind: str, number: int | str) -> str:
    digest = hashlib.sha256(f"{repo}:{kind}:{number}".encode()).hexdigest()[:8]
    safe_repo = repo.replace("/", "-")
    return f"wh-{safe_repo}-{kind}-{number}-{digest}"


def _append_log(record: dict) -> None:
    _EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _local_clone_path(repo_full_name: str) -> Path | None:
    """Return the local checkout of this remote under the github root, or None.

    Layouts, in order: ``<root>/<owner>/<name>`` (the org layout every fleet
    clone on the box uses — forge-play/Forge, willow-memory/willow-mcp — and
    the one this function did not know until 2026-09-14, so no gitsync
    trigger was ever written for an org-layout repo), then the flat
    ``<root>/<name>``, then a scan of ``<root>/*``. Verified by origin URL
    in every case, never by folder name alone.

    A thin wrapper over `_local_clone_path_with_layout` for callers that
    only need the path, not which layout matched.
    """
    path, _layout = _local_clone_path_with_layout(repo_full_name)
    return path


def _local_clone_path_with_layout(repo_full_name: str) -> tuple[Path | None, str | None]:
    """Same lookup as `_local_clone_path`, but also names which layout
    matched: ``"org"`` (``<root>/<owner>/<name>``), ``"flat"``
    (``<root>/<name>``), or ``"scan"`` (the local folder name differs
    from both — the remote was found only by walking `<root>/*` and
    matching origin). ``(None, None)`` when nothing matches. The caller
    (`_request_gitsync`) puts the layout in the trigger payload so the
    sweep can report which shape found the repo.
    """
    repo_full_name = _valid_repo_full_name(repo_full_name)
    if not repo_full_name:
        return None, None
    owner, name = repo_full_name.split("/", 1)
    target = f"{owner}/{name}".lower()

    def _matches_origin(path: Path) -> bool:
        try:
            proc = subprocess.run(
                [_GIT, "-C", str(path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            rc, out = proc.returncode, proc.stdout
            if rc != 0:
                return False
            url = out.strip().lower().removesuffix(".git")
            return url.endswith(target) or target in url
        except Exception:
            return False

    # Fast paths: the org layout first, then a flat folder named after the
    # repo (often lowercase locally).
    for layout, candidate in (
        ("org", _GITHUB_ROOT / owner / name),
        ("org", _GITHUB_ROOT / owner / name.lower()),
        ("flat", _GITHUB_ROOT / name),
        ("flat", _GITHUB_ROOT / name.lower()),
    ):
        if (_under(_GITHUB_ROOT, candidate) and (candidate / ".git").is_dir()
                and _matches_origin(candidate)):
            return candidate, layout

    # Slow path: local folder name differs from remote (e.g. willow → rudi193-cmd/Willow).
    if not _GITHUB_ROOT.is_dir():
        return None, None
    for child in _GITHUB_ROOT.iterdir():
        if child.is_dir() and (child / ".git").is_dir() and _matches_origin(child):
            return child, "scan"
    return None, None


def _queue_upstream(item: dict) -> None:
    _INBOX.mkdir(parents=True, exist_ok=True)
    wid = item["work_id"]
    path = _INBOX / f"{wid}.json"
    if path.exists():
        return
    path.write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")
    log.info("upstream inbox: %s", wid)


def _request_gitsync(repo_full_name: str) -> None:
    repo_full_name = _valid_repo_full_name(repo_full_name)
    if not repo_full_name:
        log.warning("gitsync skip: repository.full_name is not a repo name")
        return
    clone, layout = _local_clone_path_with_layout(repo_full_name)
    if not clone:
        log.info("gitsync skip: no local clone for %s (git=%s)", repo_full_name, _GIT)
        return
    _GITSYNC_TRIGGERS.mkdir(parents=True, exist_ok=True)
    owner, name = repo_full_name.split("/", 1)
    flag = _GITSYNC_TRIGGERS / f"trigger-{owner}-{name}.flag"
    if not _under(_GITSYNC_TRIGGERS, flag):
        log.warning("gitsync skip: trigger path for %s escapes %s", repo_full_name, _GITSYNC_TRIGGERS)
        return
    payload = {"at": _now(), "repo": repo_full_name, "clone": str(clone), "layout": layout}
    flag.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    log.info("gitsync trigger: %s (%s layout) → %s", repo_full_name, layout, clone)


def _repo(payload: dict) -> str:
    """`repository.full_name` if it is a well-formed `owner/name`, else "".
    Validated here, at the one place the payload's repo name enters this
    module, so every path built from it downstream starts from a checked
    value (CodeQL py/path-injection on willow-bot #28)."""
    return _valid_repo_full_name((payload.get("repository") or {}).get("full_name", ""))


def _sender_type(payload: dict) -> str:
    """`sender.type` as GitHub asserts it ("Bot" / "User" / "Organization"),
    or "" when the payload carries none. Never the login."""
    return str((payload.get("sender") or {}).get("type") or "")


def handle(event: str, payload: dict) -> None:
    """Fan-out a verified webhook into local fleet queues."""
    repo = _repo(payload)
    base = {
        "source": "willow-bot",
        "received_at": _now(),
        "event": event,
        "repo": repo,
        "action": payload.get("action"),
    }
    _append_log(base)

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "")
        if action in ("opened", "reopened", "synchronize", "closed", "ready_for_review"):
            number = pr.get("number", 0)
            _queue_upstream(
                {
                    **base,
                    "work_id": _work_id(repo, "pr", number),
                    "kind": "pull_request",
                    "number": number,
                    "title": pr.get("title", ""),
                    "state": pr.get("state"),
                    "merged": pr.get("merged"),
                    "user": (pr.get("user") or {}).get("login"),
                    "html_url": pr.get("html_url"),
                    # The head sha at delivery time. Steward's voice step
                    # keys its status comment on this so it can find the
                    # right (repo, pr, sha) row without a follow-up read
                    # of /pulls/{num}; a later synchronize event carries a
                    # new sha and the voice step opens a fresh comment.
                    "head_sha": (pr.get("head") or {}).get("sha") or "",
                    "lane_hint": "webhook",
                }
            )
        return

    if event == "issues":
        issue = payload.get("issue") or {}
        action = payload.get("action", "")
        if action in ("opened", "reopened", "closed", "labeled"):
            number = issue.get("number", 0)
            _queue_upstream(
                {
                    **base,
                    "work_id": _work_id(repo, "issue", number),
                    "kind": "issue",
                    "number": number,
                    "title": issue.get("title", ""),
                    "user": (issue.get("user") or {}).get("login"),
                    "html_url": issue.get("html_url"),
                    "lane_hint": "webhook",
                }
            )
        return

    if event == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        number = issue.get("number", 0)
        _queue_upstream(
            {
                **base,
                "work_id": _work_id(repo, f"comment-{comment.get('id', 0)}", number),
                "kind": "issue_comment",
                "number": number,
                "user": (comment.get("user") or {}).get("login"),
                "body_preview": (comment.get("body") or "")[:240],
                "html_url": comment.get("html_url"),
                "lane_hint": "webhook",
            }
        )
        return

    if event == "check_run":
        check = payload.get("check_run") or {}
        if payload.get("action") == "completed":
            # Keyed on the CHECK id, not the PR number. A PR runs several
            # checks (Tests, CodeQL, Release Please...) and each completes as
            # its own event; keyed on the PR, the first to finish was filed and
            # every later one hit the skip-on-exists in _queue_upstream and
            # vanished — a PR with four workflows left one item. The check id
            # is unique per run and stable across redeliveries, so the dedup
            # still holds where it should (the same completion twice) and
            # nowhere it should not.
            prs = check.get("pull_requests") or []
            pr_number = prs[0].get("number") if prs else None
            _queue_upstream(
                {
                    **base,
                    "work_id": _work_id(repo, "check", check.get("id", 0)),
                    "kind": "check_run",
                    "check_id": check.get("id"),
                    # The sha is what a consumer keys on (the Forge's deposit
                    # asks "how did CI go for <repo>@<sha>?"); a PR number is
                    # a convenience and absent on a check with no PR.
                    "head_sha": check.get("head_sha"),
                    "pr_number": pr_number,
                    "status": check.get("status"),
                    "conclusion": check.get("conclusion"),
                    "name": check.get("name"),
                    "html_url": check.get("html_url"),
                    # An actor is a type, never a login (BOT-INVENTORY.md
                    # match-bot-by-type-not-login): GitHub asserts `sender.type`
                    # about the credential; a login survives a rename by being
                    # wrong.
                    "sender_type": _sender_type(payload),
                    "lane_hint": "webhook",
                }
            )
            # §12 middle row: draft CI deposit (pass and fail). Local always;
            # MCP store_put when WILLOW_BOT_MCP=1.
            try:
                from willow_bot.deposits import deposit_from_check_run_payload

                deposit_from_check_run_payload(payload)
            except Exception:
                log.exception("ci deposit failed for %s", repo)
        return

    if event == "push":
        ref = payload.get("ref", "")
        if ref in ("refs/heads/main", "refs/heads/master"):
            _request_gitsync(repo)
        return

    if event == "installation" or event == "installation_repositories":
        _queue_upstream(
            {
                **base,
                "work_id": _work_id("app", event, payload.get("action", "change")),
                "kind": event,
                "repositories_added": [
                    r.get("full_name") for r in (payload.get("repositories_added") or [])
                ],
                "repositories_removed": [
                    r.get("full_name") for r in (payload.get("repositories_removed") or [])
                ],
                "lane_hint": "catalog",
            }
        )
