#!/usr/bin/env python3
"""audit_app_config.py — check willow-bot GitHub App vs fleet expectations.

Run from willow-bot with .env loaded:

    set -a && source .env && set +a
    python scripts/audit_app_config.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Repos the fleet actually uses (not every ~/github clone).
# Orgs as of BOT-INVENTORY 2026-08-29; refresh when installs change.
FLEET_CORE = [
    "willow-memory/willow-mcp",
    "willow-memory/kartikeya",
    "willow-memory/willows-grove",
    "willow-memory/willow-gate",
    "willow-memory/ratatosk",
    "willow-memory/corpus-lens",
    "Die-Namic-Systems/Nestor",
    "hornbook-knowledge/Jeles",
    "forge-play/Forge",
    "rudi193-cmd/willow-bot",
]


def _installed_lower(installed: set[str]) -> set[str]:
    return {name.lower() for name in installed}


def _has_repo(installed: set[str], full_name: str) -> bool:
    return full_name.lower() in _installed_lower(installed)

UPSTREAM_ACTIVE = [
  # upstream_watcher defaults + OPEN_WORK warm lanes (2026-07-22)
    "NousResearch/hermes-agent",
    "moazbuilds/claudeclaw",
    "alash3al/stash",
    "basicmachines-co/basic-memory",
    "Redential/redential-cli",
    "DeusData/codebase-memory-mcp",
    "Filippo-Venturini/ctxvault",
    "zeroc00I/DontFeedTheAI",
    "liatrio-labs/claude-deep-review",
    "doobidoo/mcp-memory-service",
]

# Your forks count — app on rudi193-cmd covers fork webhooks, not upstream org events.
UPSTREAM_FORK_OK = True  # document-only

REQUIRED_PERMISSIONS = {
    "metadata": "read",
    "issues": "write",
    "pull_requests": "write",
    "checks": "read",
    "contents": "read",
}

# GitHub sends installation + installation_repositories to every App by default;
# they are not in the Subscribe-to-events UI and do not appear in GET /app events.
IMPLICIT_EVENTS = {"installation", "installation_repositories"}

REQUIRED_EVENTS = {
    "pull_request",
    "push",
    "issues",
    "check_run",
    "create",
    "issue_comment",
}

OPTIONAL_EVENTS = {"pull_request_review", "workflow_run", "status"}

DO_NOT_NEED = {
    "contents": "write",  # bot comments only; commits use Kart/PAT
    "actions": "write",
    "administration": "write",
}


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _ok(msg: str) -> None:
    print(f"  OK    {msg}")


def _warn(msg: str) -> None:
    print(f"  WARN  {msg}")


def main() -> int:
    env = os.environ.copy()
    for line in (_ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v

    proc = subprocess.run(
        [str(_ROOT / ".venv/bin/python"), str(_ROOT / "scripts/list_installations.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 1

    data = json.loads(proc.stdout)
    installed = {r["full_name"] for r in data["repositories"]}
    for w in data.get("warnings") or []:
        _warn(w)
    print(f"Installations: {json.dumps(data.get('installations', []), indent=2)}\n")

    import jwt
    import requests

    app_id = env["GITHUB_APP_ID"]
    key_path = Path(
        env.get(
            "GITHUB_APP_PRIVATE_KEY_PATH",
            str(Path.home() / ".willow/secrets/willow-bot.pem"),
        )
    )
    token = jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + 600, "iss": app_id},
        key_path.read_text().strip(),
        algorithm="RS256",
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    app = requests.get("https://api.github.com/app", headers=headers, timeout=20).json()
    hook = requests.get("https://api.github.com/app/hook/config", headers=headers, timeout=20).json()
    deliveries = requests.get(
        "https://api.github.com/app/hook/deliveries?per_page=5",
        headers=headers,
        timeout=20,
    ).json()

    print("=== willow-bot GitHub App audit ===\n")
    print(f"App: {app.get('name')} ({app.get('html_url')})")
    print(f"Repositories with access: {len(installed)}\n")

    print("-- Webhook --")
    expected_url = env.get("WEBHOOK_PUBLIC_URL", "").rstrip("/")
    if expected_url and not expected_url.endswith("/webhook"):
        expected_url += "/webhook"
    hook_url = hook.get("url", "")
    if hook_url == expected_url:
        _ok(f"hook URL matches .env ({hook_url})")
    else:
        _fail(f"hook URL {hook_url!r} != .env {expected_url!r}")

    recent = [
        f"{d.get('event')} → {d.get('status_code')}" for d in deliveries[:5]
    ]
    if recent:
        ok = all("200" in s for s in recent)
        ( _ok if ok else _warn )(f"recent deliveries: {', '.join(recent)}")
    else:
        _warn("no recent webhook deliveries recorded")

    print("\n-- Permissions (app-level) --")
    perms = app.get("permissions") or {}
    for key, level in REQUIRED_PERMISSIONS.items():
        actual = perms.get(key)
        if actual == level or (key == "metadata" and actual == "read"):
            _ok(f"{key}: {actual}")
        else:
            _fail(f"{key}: need {level}, have {actual!r}")

    for key, level in DO_NOT_NEED.items():
        if perms.get(key) in ("write", "admin"):
            _warn(f"{key}: {perms[key]} — broader than needed")

    print("\n-- Webhook events subscribed --")
    events = set(app.get("events") or [])
    _ok(
        "implicit (always delivered, not in UI): "
        + ", ".join(sorted(IMPLICIT_EVENTS))
    )
    missing = REQUIRED_EVENTS - events
    if missing:
        _fail(f"missing events: {', '.join(sorted(missing))}")
    else:
        _ok("all required events subscribed")
    extra = events - REQUIRED_EVENTS - OPTIONAL_EVENTS - IMPLICIT_EVENTS
    if extra:
        _warn(f"extra events (noise ok): {', '.join(sorted(extra))}")

    print("\n-- Fleet core repos --")
    for repo in FLEET_CORE:
        if _has_repo(installed, repo):
            _ok(repo)
        else:
            _fail(f"{repo} — not on app (gitsync triggers + fleet PR events won't fire)")

    print("\n-- Upstream active lanes --")
    for repo in UPSTREAM_ACTIVE:
        if repo in installed:
            _ok(repo)
        else:
            _warn(
                f"{repo} — not on app. "
                "OK if you only watch via fork under rudi193-cmd/* or upstream_watcher poll."
            )

    print("\n-- Scope sanity --")
    if len(installed) > 80:
        _warn(
            f"{len(installed)} repos — likely over-broad. "
            "Prefer: fleet core + active upstream forks + open-PR repos only."
        )
    elif len(installed) < 5:
        _warn(f"only {len(installed)} repos — may be under-scoped for consolidation")

  # sensitive patterns
    sensitive = [r for r in installed if "data-vault" in r.lower() or "secrets" in r.lower()]
    if sensitive:
        _warn(f"sensitive repos on app (review): {', '.join(sorted(sensitive))}")

    print("\n-- Local clone overlap --")
    proc2 = subprocess.run(
        [str(_ROOT / ".venv/bin/python"), str(_ROOT / "scripts/repo_map.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
    )
    if proc2.returncode == 0:
        mp = json.loads(proc2.stdout)
        print(f"  overlap (app + ~/github): {len(mp['overlap'])}")
        print(f"  app_only (no local clone): {len(mp['app_only'])}")
        print(f"  local_only (no app): {len(mp['local_only'])}")
        if mp["app_only"]:
            _warn(
                "app_only repos won't trigger gitsync — fine for upstream-only watch"
            )

    print("\nDone. Fix FAIL lines in GitHub → App settings → Permissions / Events / Repository access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
