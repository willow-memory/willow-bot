"""
github_app.py — GitHub App JWT auth and API calls.
b17: WBGA1  ΔΣ=42

Handles installation access tokens and posting comments/reactions.
Private key lives in the operator data vault (never committed). Env may
override for tests.
"""
from __future__ import annotations

import logging
import time

import jwt
import requests

import credentials as _creds

log = logging.getLogger("willow-bot.github_app")

# No GITHUB_BOT_LOGIN. The bot has been renamed twice and a login string was
# wrong on both sides of each rename; nothing here ever read the value. Where
# a bot must be recognised, match on `user.type == "Bot"` (BOT-INVENTORY.md).

_installation_token_cache: dict[int, tuple[str, float]] = {}
# What GitHub said each installation token may do, from the same
# access_tokens answer that minted it, and which installation serves a repo.
# Read by `installation_permissions`; never a network call of its own.
_installation_permissions: dict[int, dict[str, str]] = {}
_repo_installation: dict[str, int] = {}
_cached: _creds.BotCredentials | None = None


def _cred() -> _creds.BotCredentials:
    global _cached
    if _cached is None:
        _cached = _creds.resolve(require_complete=True)
    return _cached


def reset_credential_cache() -> None:
    """Test hook — drop the process-wide credential cache."""
    global _cached
    _cached = None


def _configured() -> bool:
    try:
        c = _cred()
    except RuntimeError:
        return False
    return bool(c.app_id and c.private_key_pem)


def _load_private_key() -> str:
    return _cred().private_key_pem


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": _cred().app_id,
    }
    return jwt.encode(payload, _load_private_key(), algorithm="RS256")


def _get_installation_id(repo_full_name: str) -> int:
    token = _make_jwt()
    r = requests.get(
        f"https://api.github.com/repos/{repo_full_name}/installation",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["id"]


def _get_installation_token(installation_id: int) -> str:
    cached_token, expires_at = _installation_token_cache.get(installation_id, ("", 0))
    if cached_token and time.time() < expires_at - 60:
        return cached_token

    token = _make_jwt()
    r = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    access_token = data["token"]
    _installation_token_cache[installation_id] = (access_token, time.time() + 3600)
    perms = data.get("permissions")
    if isinstance(perms, dict):
        _installation_permissions[installation_id] = {str(k): str(v) for k, v in perms.items()}
    return access_token


def _auth_headers(repo_full_name: str) -> dict:
    installation_id = _get_installation_id(repo_full_name)
    _repo_installation[repo_full_name.lower()] = installation_id
    token = _get_installation_token(installation_id)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def installation_permissions(repo_full_name: str) -> dict[str, str] | None:
    """The permissions GitHub granted the installation token for this repo,
    as the access_tokens answer stated them (``{"checks": "read", ...}``).

    Cache only: known after `_auth_headers(repo)` has minted or reused a
    token this process, ``None`` before that — a caller reads ``None`` as
    "unknown" and must not treat it as a refusal. A fact GitHub asserts
    about the credential, so a caller can skip a call the grant cannot
    make instead of learning it from a 403 every tick.
    """
    installation_id = _repo_installation.get(repo_full_name.lower())
    if installation_id is None:
        return None
    perms = _installation_permissions.get(installation_id)
    return dict(perms) if perms is not None else None


def get_pull(repo_full_name: str, number: int) -> dict:
    """``GET /repos/{repo}/pulls/{number}`` under the App's install token.
    Returns the parsed JSON; raises on an unconfigured App or HTTP error so
    the caller decides what an unreadable PR means."""
    if not _configured():
        raise RuntimeError("GitHub App not configured — cannot get_pull")
    headers = _auth_headers(repo_full_name)
    r = requests.get(
        f"https://api.github.com/repos/{repo_full_name}/pulls/{int(number)}",
        headers=headers,
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected pulls answer for {repo_full_name}#{number}")
    return data


def list_pull_files(repo_full_name: str, number: int, *, per_page: int = 100, max_pages: int = 3) -> list[dict]:
    """``GET /repos/{repo}/pulls/{number}/files`` under the App's install
    token: each changed file with its ``status``, ``additions``,
    ``deletions`` and ``patch`` (absent for a binary or an oversized file).
    Paginates up to ``max_pages``; raises on an unconfigured App or HTTP
    error so the caller decides what an unreadable diff means."""
    if not _configured():
        raise RuntimeError("GitHub App not configured — cannot list_pull_files")
    headers = _auth_headers(repo_full_name)
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{int(number)}/files",
            headers=headers,
            params={"per_page": per_page, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected pulls/files answer for {repo_full_name}#{number}")
        out.extend(f for f in batch if isinstance(f, dict))
        if len(batch) < per_page:
            break
    return out


def _app_get(path: str, *, params: dict | None = None):
    """GET an App-level endpoint (``/app/...``) under the App's own JWT —
    not an installation token. Raises on an unconfigured App or HTTP error."""
    if not _configured():
        raise RuntimeError("GitHub App not configured")
    r = requests.get(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {_make_jwt()}", "Accept": "application/vnd.github+json"},
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def redeliver(delivery_id: int) -> None:
    """Ask GitHub to send an App-webhook delivery again
    (``POST /app/hook/deliveries/{id}/attempts``, under the App's own JWT).
    The new attempt carries the same ``X-GitHub-Delivery`` guid, so the
    delivery guard in bot.py still runs it once. Raises on an unconfigured
    App or HTTP error."""
    if not _configured():
        raise RuntimeError("GitHub App not configured")
    r = requests.post(
        f"https://api.github.com/app/hook/deliveries/{int(delivery_id)}/attempts",
        headers={"Authorization": f"Bearer {_make_jwt()}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    r.raise_for_status()


def list_hook_deliveries(per_page: int = 30) -> list[dict]:
    """The App webhook's most recent deliveries, newest first, as GitHub
    recorded them: ``status_code`` is what the receiving end answered (0
    when nothing answered). Raises on an unconfigured App or HTTP error."""
    data = _app_get("/app/hook/deliveries", params={"per_page": int(per_page)})
    if not isinstance(data, list):
        raise RuntimeError("unexpected /app/hook/deliveries answer")
    return [d for d in data if isinstance(d, dict)]


def hook_url() -> str:
    """The URL the App webhook posts to — and nothing else from
    ``/app/hook/config``, whose answer can carry the webhook ``secret``.
    The caller still redacts it (a URL can carry a login or a token).
    Raises on error."""
    data = _app_get("/app/hook/config")
    if not isinstance(data, dict):
        raise RuntimeError("unexpected /app/hook/config answer")
    return str(data.get("url") or "")


def list_open_pulls(repo_full_name: str, *, per_page: int = 100, max_pages: int = 5) -> list[dict]:
    """List every open pull request in ``repo_full_name`` under the App's
    install token — the same auth path ``post_comment`` uses.

    Returns the parsed JSON list. Paginates up to ``max_pages`` pages of
    ``per_page`` each (default 500 PRs, which is well past any repo we
    watch; if a repo runs past that, the receipt reader can see it as
    ``count >= 500`` and raise it separately). Raises on HTTP error so
    the caller (the tick's catch-up step) can record the reason in its
    receipt and try again next tick.

    Rate limit: an App-authenticated request costs one against the
    installation's 5000/hr budget. Called from the catch-up step at
    3-repo × 12-tick = 36 requests/hr, this is <1% of that.
    """
    if not _configured():
        raise RuntimeError("GitHub App not configured — cannot list_open_pulls")
    headers = _auth_headers(repo_full_name)
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}/pulls",
            headers=headers,
            params={"state": "open", "per_page": per_page, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            break  # a repo the App lost access to answers 404 → raise; a weird shape stops paging
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def _repo_get_pages(repo_full_name: str, path: str, *, params: dict | None = None,
                    per_page: int = 100, max_pages: int = 3, key: str | None = None) -> list[dict]:
    """GET ``/repos/{repo}{path}`` under the App's install token and page
    through it. ``key`` names the list inside an object answer (the checks
    API wraps its list in ``{"check_runs": [...]}``). Raises on an
    unconfigured App, an HTTP error, or an answer of the wrong shape."""
    if not _configured():
        raise RuntimeError(f"GitHub App not configured — cannot read {path}")
    headers = _auth_headers(repo_full_name)
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}{path}",
            headers=headers,
            params={**(params or {}), "per_page": per_page, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get(key) if key and isinstance(data, dict) else data
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected {path} answer for {repo_full_name}")
        out.extend(b for b in batch if isinstance(b, dict))
        if len(batch) < per_page:
            break
    return out


def get_commit_date(repo_full_name: str, sha: str) -> str:
    """The committer date of ``sha`` (ISO 8601), under the install token."""
    if not _configured():
        raise RuntimeError("GitHub App not configured — cannot get_commit_date")
    r = requests.get(
        f"https://api.github.com/repos/{repo_full_name}/commits/{sha}",
        headers=_auth_headers(repo_full_name),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    commit = data.get("commit") if isinstance(data, dict) else None
    when = ((commit or {}).get("committer") or {}).get("date") or ((commit or {}).get("author") or {}).get("date")
    if not when:
        raise RuntimeError(f"no commit date for {repo_full_name}@{sha}")
    return str(when)


def list_issue_comments(repo_full_name: str, number: int, *, max_pages: int = 3) -> list[dict]:
    """Conversation comments on an issue or PR, oldest first."""
    return _repo_get_pages(repo_full_name, f"/issues/{int(number)}/comments", max_pages=max_pages)


def list_pull_reviews(repo_full_name: str, number: int, *, max_pages: int = 3) -> list[dict]:
    """Reviews on a PR, oldest first: each with ``state``, ``user`` and
    ``commit_id``."""
    return _repo_get_pages(repo_full_name, f"/pulls/{int(number)}/reviews", max_pages=max_pages)


def list_pull_review_comments(repo_full_name: str, number: int, *, max_pages: int = 3) -> list[dict]:
    """Line comments on a PR's diff, oldest first."""
    return _repo_get_pages(repo_full_name, f"/pulls/{int(number)}/comments", max_pages=max_pages)


def list_check_runs(repo_full_name: str, ref: str, *, max_pages: int = 3) -> list[dict]:
    """Every check run on ``ref`` (a sha, branch or tag), latest attempt
    per check name. Needs the ``checks: read`` grant."""
    return _repo_get_pages(repo_full_name, f"/commits/{ref}/check-runs",
                           params={"filter": "latest"}, max_pages=max_pages, key="check_runs")


def get_combined_status(repo_full_name: str, ref: str) -> dict:
    """The legacy commit-status rollup for ``ref``: ``state`` is one of
    success / failure / pending, ``statuses`` lists each context."""
    if not _configured():
        raise RuntimeError("GitHub App not configured — cannot get_combined_status")
    r = requests.get(
        f"https://api.github.com/repos/{repo_full_name}/commits/{ref}/status",
        headers=_auth_headers(repo_full_name),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected status answer for {repo_full_name}@{ref}")
    return data


def list_recent_pulls(repo_full_name: str, *, state: str = "all", max_pages: int = 1) -> list[dict]:
    """PRs in any state, most recently updated first."""
    return _repo_get_pages(repo_full_name, "/pulls",
                           params={"state": state, "sort": "updated", "direction": "desc"},
                           max_pages=max_pages)


def list_recent_issues(repo_full_name: str, *, state: str = "all", since: str | None = None,
                       max_pages: int = 1) -> list[dict]:
    """Issues in any state, most recently updated first. GitHub returns PRs
    here too; those carry a ``pull_request`` key and are dropped."""
    params = {"state": state, "sort": "updated", "direction": "desc"}
    if since:
        params["since"] = since
    rows = _repo_get_pages(repo_full_name, "/issues", params=params, max_pages=max_pages)
    return [i for i in rows if "pull_request" not in i]


def list_contributors(repo_full_name: str, *, max_pages: int = 2) -> list[dict]:
    """Commit authors on the default branch, most commits first: each with
    ``login``, ``type`` and ``contributions`` (the commit count)."""
    return _repo_get_pages(repo_full_name, "/contributors", max_pages=max_pages)


def post_comment(repo_full_name: str, issue_or_pr_number: int, body: str) -> bool:
    """Post a comment on an issue or PR. Returns True on success."""
    if not _configured():
        log.warning("GitHub App not configured — skipping comment")
        return False
    try:
        headers = _auth_headers(repo_full_name)
        r = requests.post(
            f"https://api.github.com/repos/{repo_full_name}/issues/{issue_or_pr_number}/comments",
            headers=headers,
            json={"body": body},
            timeout=10,
        )
        r.raise_for_status()
        log.info("posted comment on %s#%s", repo_full_name, issue_or_pr_number)
        return True
    except Exception as e:
        log.error("post_comment failed on %s#%s: %s", repo_full_name, issue_or_pr_number, e)
        return False
