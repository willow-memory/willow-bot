#!/usr/bin/env python3
"""rotate_webhook_secret.py — generate a new webhook secret and update .env.

Usage:
    python scripts/rotate_webhook_secret.py

Prints the new secret so you can paste it into:
  GitHub → App settings → Webhooks → Secret field

Then restart:
    systemctl --user restart willow-bot
"""
import os
import re
import secrets
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ENV = _ROOT / ".env"


def main() -> None:
    new_secret = secrets.token_hex(32)

    if not _ENV.is_file():
        print(f"[FAIL] .env not found at {_ENV}")
        raise SystemExit(1)

    text = _ENV.read_text()
    if re.search(r"^GITHUB_WEBHOOK_SECRET=", text, re.MULTILINE):
        updated = re.sub(
            r"^(GITHUB_WEBHOOK_SECRET=).*$",
            rf"\g<1>{new_secret}",
            text,
            flags=re.MULTILINE,
        )
    else:
        updated = text.rstrip() + f"\nGITHUB_WEBHOOK_SECRET={new_secret}\n"

    _ENV.write_text(updated)
    print(f"[OK] .env updated with new GITHUB_WEBHOOK_SECRET")
    print()
    print("Paste this value into GitHub App settings → Webhooks → Secret:")
    print()
    print(f"    {new_secret}")
    print()
    print("Then restart the service:")
    print("    systemctl --user restart willow-bot")


if __name__ == "__main__":
    main()
