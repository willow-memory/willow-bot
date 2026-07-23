#!/usr/bin/env python3
"""List GitHub App installations and repository access for willow-bot."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import jwt
import requests

APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
KEY_PATH = Path(
    os.getenv(
        "GITHUB_APP_PRIVATE_KEY_PATH",
        str(Path.home() / ".willow" / "secrets/willow-bot.pem"),
    )
)

API = "https://api.github.com"
API_VERSION = "2022-11-28"


def _app_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _installation_token(app_token: str, installation_id: int) -> str | None:
    r = requests.post(
        f"{API}/app/installations/{installation_id}/access_tokens",
        headers=_app_headers(app_token),
        json={},
        timeout=20,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["token"]


def _list_repos_jwt(app_token: str, installation_id: int) -> tuple[list[dict], str | None]:
    repos: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{API}/app/installations/{installation_id}/repositories",
            headers=_app_headers(app_token),
            params={"per_page": 100, "page": page},
            timeout=20,
        )
        if r.status_code == 404:
            return [], "jwt_repos_404"
        r.raise_for_status()
        batch = r.json().get("repositories", [])
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos, None


def _list_repos_installation_token(inst_token: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    headers = {
        "Authorization": f"Bearer {inst_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    while True:
        r = requests.get(
            f"{API}/installation/repositories",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=20,
        )
        r.raise_for_status()
        batch = r.json().get("repositories", [])
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def _repo_record(repo: dict, account: str) -> dict:
    perms = {k: v for k, v in (repo.get("permissions") or {}).items() if v}
    return {
        "full_name": repo["full_name"],
        "private": repo["private"],
        "account": account,
        "permissions": perms,
        "default_branch": repo.get("default_branch"),
    }


def main() -> int:
    if not APP_ID or not KEY_PATH.is_file():
        print("[FAIL] GITHUB_APP_ID / PEM not configured", file=sys.stderr)
        return 1

    now = int(time.time())
    app_token = jwt.encode(
        {"iat": now - 60, "exp": now + 600, "iss": int(APP_ID)},
        KEY_PATH.read_text().strip(),
        algorithm="RS256",
    )

    app_r = requests.get(f"{API}/app", headers=_app_headers(app_token), timeout=20)
    app_r.raise_for_status()
    app = app_r.json()

    installs: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{API}/app/installations",
            headers=_app_headers(app_token),
            params={"per_page": 100, "page": page},
            timeout=20,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        installs.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    repos: list[dict] = []
    warnings: list[str] = []
    install_meta: list[dict] = []

    for inst in installs:
        iid = inst["id"]
        account = (inst.get("account") or {}).get("login", "?")
        install_meta.append(
            {
                "id": iid,
                "account": account,
                "target_type": inst.get("target_type"),
                "repository_selection": inst.get("repository_selection"),
                "suspended_at": inst.get("suspended_at"),
            }
        )

        if inst.get("suspended_at"):
            warnings.append(f"installation {iid} ({account}) is suspended — skipped")
            continue

        raw, err = _list_repos_jwt(app_token, iid)
        if err == "jwt_repos_404":
            inst_token = _installation_token(app_token, iid)
            if not inst_token:
                warnings.append(
                    f"installation {iid} ({account}) returned 404 for repos and "
                    "access_token — may be stale; reinstall the App on this account"
                )
                continue
            raw = _list_repos_installation_token(inst_token)
            warnings.append(
                f"installation {iid} ({account}): used installation-token fallback "
                f"({len(raw)} repos)"
            )

        for repo in raw:
            repos.append(_repo_record(repo, account))

    # De-dupe if multiple installs overlap (shouldn't, but safe).
    seen: set[str] = set()
    unique: list[dict] = []
    for repo in sorted(repos, key=lambda r: r["full_name"]):
        if repo["full_name"] in seen:
            continue
        seen.add(repo["full_name"])
        unique.append(repo)

    out = {
        "app": {
            "id": app.get("id"),
            "name": app.get("name"),
            "slug": app.get("slug"),
            "owner": (app.get("owner") or {}).get("login"),
        },
        "installations": install_meta,
        "installation_count": len(installs),
        "warnings": warnings,
        "repositories": unique,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
