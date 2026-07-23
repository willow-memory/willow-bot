#!/usr/bin/env python3
"""sync_webhook_secret.py — push .env webhook secret + URL to the GitHub App.

Uses the App JWT (same auth as github_app.py) to PATCH /app/hook/config so
local GITHUB_WEBHOOK_SECRET and WEBHOOK_PUBLIC_URL match GitHub's delivery.

Usage:
    cd ~/github/willow-bot
    set -a && source .env && set +a
    python scripts/sync_webhook_secret.py

Then restart (if the service was already running with a stale secret):
    systemctl --user restart willow-bot
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import jwt
import requests

APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "").strip().rstrip("/")
KEY_PATH = Path(
    os.getenv(
        "GITHUB_APP_PRIVATE_KEY_PATH",
        str(Path.home() / ".willow" / "secrets" / "willow-bot.pem"),
    )
)


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _webhook_url() -> str:
    if not PUBLIC_URL:
        _fail("WEBHOOK_PUBLIC_URL is not set")
    if PUBLIC_URL.endswith("/webhook"):
        return PUBLIC_URL
    return f"{PUBLIC_URL}/webhook"


def _jwt() -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 600, "iss": APP_ID},
        KEY_PATH.read_text().strip(),
        algorithm="RS256",
    )


def main() -> int:
    if not APP_ID:
        _fail("GITHUB_APP_ID is not set")
    if not SECRET:
        _fail("GITHUB_WEBHOOK_SECRET is not set")
    if not KEY_PATH.is_file():
        _fail(f"GitHub App PEM missing: {KEY_PATH}")

    url = _webhook_url()
    headers = {
        "Authorization": f"Bearer {_jwt()}",
        "Accept": "application/vnd.github+json",
    }

    get_r = requests.get(
        "https://api.github.com/app/hook/config", headers=headers, timeout=20
    )
    if get_r.status_code != 200:
        _fail(f"GET /app/hook/config → {get_r.status_code}: {get_r.text[:300]}")

    before = get_r.json()
    print("[INFO] current hook URL:", before.get("url", "(unset)"))

    patch_body = {
        "url": url,
        "content_type": "json",
        "secret": SECRET,
        "insecure_ssl": "0",
    }
    patch_r = requests.patch(
        "https://api.github.com/app/hook/config",
        headers=headers,
        json=patch_body,
        timeout=20,
    )
    if patch_r.status_code != 200:
        _fail(f"PATCH /app/hook/config → {patch_r.status_code}: {patch_r.text[:300]}")

    after = patch_r.json()
    print("[OK]   synced hook URL:", after.get("url"))
    print("[OK]   secret pushed (len=%d)" % len(SECRET))
    if before.get("url") and before.get("url") != url:
        print("[WARN] URL changed from", before.get("url"))
    print()
    print("Restart the listener if it was running:")
    print("    systemctl --user restart willow-bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
