"""
github_app.py — GitHub App JWT auth and API calls.
b17: WBGA1  ΔΣ=42

Handles installation access tokens and posting comments/reactions.
Private key lives in the operator data vault (never committed). Env may
override for tests.
"""
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
