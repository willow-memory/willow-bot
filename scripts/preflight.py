#!/usr/bin/env python3
"""Preflight checks for willow-bot local service setup."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"[OK]   {label}{suffix}")


def _warn(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"[WARN] {label}{suffix}")


def _fail(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"[FAIL] {label}{suffix}")


def main() -> int:
    failures = 0

    app_id = os.getenv("GITHUB_APP_ID", "").strip()
    if app_id:
        _ok("GITHUB_APP_ID", app_id)
    else:
        _fail("GITHUB_APP_ID", "set this from the GitHub App settings")
        failures += 1

    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if webhook_secret:
        _ok("GITHUB_WEBHOOK_SECRET", "set")
    else:
        _fail("GITHUB_WEBHOOK_SECRET", "must match the GitHub App webhook secret")
        failures += 1

    key_path = Path(
        os.getenv(
            "GITHUB_APP_PRIVATE_KEY_PATH",
            str(Path.home() / ".willow" / "secrets" / "willow-bot.pem"),
        )
    )
    if key_path.is_file():
        mode = key_path.stat().st_mode & 0o777
        if mode & 0o077:
            _warn("GitHub App PEM permissions", f"{key_path} is {mode:o}; prefer 600")
        else:
            _ok("GitHub App PEM", str(key_path))
    else:
        _fail("GitHub App PEM", f"missing: {key_path}")
        failures += 1

    import tunnel

    status = tunnel.status()
    if status["configured"]:
        _ok("WEBHOOK_PUBLIC_URL", status["webhook_url"])
    else:
        _fail("WEBHOOK_PUBLIC_URL", "set this to the public Pangolin URL")
        failures += 1

    if status["local_listening"]:
        _ok("local listener", status["local_url"])
    else:
        _warn("local listener", f"{status['local_url']} is not listening yet")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
