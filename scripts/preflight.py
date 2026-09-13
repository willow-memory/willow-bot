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

    # Prefer vault-resolved credentials over checkout .env.
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    import credentials

    # Non-secret settings may still live in secrets/willow-bot.env
    for k, v in credentials._parse_env_file(credentials.vault_env_path()).items():
        if k not in ("GITHUB_WEBHOOK_SECRET",) and k not in os.environ:
            os.environ.setdefault(k, v)

    try:
        cred = credentials.resolve(require_complete=False)
    except Exception as exc:
        _fail("vault credentials", str(exc))
        return 1

    if cred.app_id:
        _ok("GITHUB_APP_ID", cred.app_id)
    else:
        _fail(
            "GITHUB_APP_ID",
            f"set in {credentials.vault_env_path()} or Fernet key willow-bot/app_id",
        )
        failures += 1

    if cred.webhook_secret:
        _ok("GITHUB_WEBHOOK_SECRET", "set")
    else:
        _fail(
            "GITHUB_WEBHOOK_SECRET",
            f"set in {credentials.vault_env_path()} or Fernet key willow-bot/webhook_secret",
        )
        failures += 1

    webhook_secret = cred.webhook_secret

    # Roundtrip HMAC check against the local listener — catches secret mismatches
    # before GitHub's delivery would produce a 401.
    if webhook_secret:
        import hashlib
        import hmac as _hmac
        import urllib.request
        import json as _json

        _tunnel = None
        try:
            import tunnel as _tunnel_mod
            _tunnel = _tunnel_mod.status()
        except Exception:
            pass

        local_url = (_tunnel or {}).get("local_url") or "http://127.0.0.1:9000"
        probe_body = _json.dumps({"action": "preflight-probe"}).encode()
        sig = "sha256=" + _hmac.new(
            webhook_secret.encode(), probe_body, hashlib.sha256
        ).hexdigest()
        try:
            req = urllib.request.Request(
                f"{local_url}/webhook",
                data=probe_body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "ping",
                    "X-Hub-Signature-256": sig,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    _ok("webhook HMAC roundtrip", "local signature accepted")
                else:
                    _warn("webhook HMAC roundtrip", f"unexpected status {resp.status}")
        except Exception as exc:
            code = getattr(getattr(exc, "code", None), "__str__", lambda: str(exc))()
            if "401" in str(exc):
                _fail("webhook HMAC roundtrip", "401 — secret does not match running service; restart willow-bot")
                failures += 1
            else:
                _warn("webhook HMAC roundtrip", f"could not reach local listener: {exc}")

    if cred.private_key_pem:
        key_path = cred.private_key_path or credentials.default_pem_path()
        if cred.private_key_path and cred.private_key_path.is_file():
            mode = cred.private_key_path.stat().st_mode & 0o777
            if mode & 0o077:
                _warn("GitHub App PEM permissions", f"{cred.private_key_path} is {mode:o}; prefer 600")
            else:
                _ok("GitHub App PEM", str(cred.private_key_path))
        else:
            _ok("GitHub App PEM", cred.source)
    else:
        _fail("GitHub App PEM", f"missing: {credentials.default_pem_path()}")
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
