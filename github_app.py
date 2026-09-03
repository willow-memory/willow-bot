"""
github_app.py — GitHub App JWT auth and API calls.
b17: WBGA1  ΔΣ=42

Handles installation access tokens and posting comments/reactions.
Private key lives on disk (never in env). Rotate by swapping the PEM file.
"""
import logging
import os
import time
from pathlib import Path

import jwt
import requests

log = logging.getLogger("willow-bot.github_app")

_APP_ID = os.getenv("GITHUB_APP_ID", "")
_KEY_PATH = Path(os.getenv("GITHUB_APP_PRIVATE_KEY_PATH",
                            Path.home() / ".willow" / "secrets" / "willow-bot.pem"))
# No GITHUB_BOT_LOGIN. The bot has been renamed twice and a login string was
# wrong on both sides of each rename; nothing here ever read the value. Where
# a bot must be recognised, match on `user.type == "Bot"` (BOT-INVENTORY.md).

_installation_token_cache: dict[int, tuple[str, float]] = {}


def _load_private_key() -> str:
    return _KEY_PATH.read_text().strip()


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": _APP_ID,
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
    # GitHub installation tokens expire after 1 hour
    _installation_token_cache[installation_id] = (access_token, time.time() + 3600)
    return access_token


def _auth_headers(repo_full_name: str) -> dict:
    installation_id = _get_installation_id(repo_full_name)
    token = _get_installation_token(installation_id)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def post_comment(repo_full_name: str, issue_or_pr_number: int, body: str) -> bool:
    """Post a comment on an issue or PR. Returns True on success."""
    if not _APP_ID or not _KEY_PATH.exists():
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
