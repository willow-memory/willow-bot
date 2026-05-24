---
b17: WBOT1
title: Security Audit — willow-bot
date: 2026-05-06
auditor: Hanuman (Claude Code, Sonnet 4.6)
status: open
---

# Security Audit — willow-bot

Part of Level 2 full-fleet security audit. willow-bot — FastAPI GitHub App webhook receiver. Listens on port 9000 via cloudflared tunnel. Processes pull_request, push, check_run, create, and issues events; posts comments/reactions via GitHub App JWT auth.

## Rubric Results

| # | Check | Status | Notes |
|---|---|---|---|
| R1 | SQL injection | N/A | No database queries |
| R2 | Shell injection | ✅ PASS | No subprocess/os.system in event handlers; cloudflared spawned with list args |
| R3 | Path traversal | ✅ PASS | No user-controlled path operations; private key path from env var with home-relative default |
| R4 | Hardcoded credentials | ✅ PASS | All secrets via env vars (GITHUB_WEBHOOK_SECRET, GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH) |
| R5 | CORS wildcard | ✅ PASS | FastAPI default — no CORS middleware configured; internal webhook receiver only |
| R6 | XSS | N/A | API only, no HTML rendering |
| R7 | Unsigned code execution | ✅ PASS | No eval(), exec(), or dynamic imports |
| R8 | Missing auth on APIs | ❌ FAIL | See P1: signature verification silently bypassed when env var unset |
| R9 | Bare except swallowing errors | ⚠️ WARN | `router.py` silently drops unhandled event types (`log.debug` only) |
| R10 | Predictable temp paths | ✅ PASS | No temp files; cloudflared config at `~/.cloudflared/config.yml` (home-relative) |
| R11 | Race conditions | ✅ PASS | Single asyncio event loop; installation token cache is dict (not thread-safe, but single-threaded) |
| R12 | safe_integration.py status() | ❌ MISSING | No safe_integration.py present |
| R13 | Entry point importable | ✅ PASS | `uvicorn bot:app` pattern; imports clean |
| R14 | requirements.txt pinned | ⚠️ WARN | No requirements.txt found; dependencies undefined/unpinned |
| R15 | No hardcoded dev paths | ✅ PASS | All paths use env vars or `Path.home()` |

## Findings

### P1: WB-SIG-01 — Webhook signature verification silently bypassed when GITHUB_WEBHOOK_SECRET unset

**Severity:** P1
**Status:** Open
**File:** `bot.py`, line ~20 and `_verify_signature()`

```python
_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()

def _verify_signature(body: bytes, sig_header: str) -> bool:
    if not _SECRET:
        log.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True   # ← any request accepted
```

When `GITHUB_WEBHOOK_SECRET` is not set, all webhook requests are accepted regardless of signature. An attacker who can reach port 9000 (via the cloudflared tunnel URL) can forge arbitrary GitHub events — fake PRs opened, branches created, issues filed — and the bot will act on them.

This is the same pattern as the SAP fingerprint issue in openclaw-sap-gate: the security check is conditional on the env var being set, silently degrading to no-auth.

**Recommended fix:**
```python
if not _SECRET:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET must be set — refusing to start without signature verification")
```

Fail at startup, not at request time. A warning at request time is easy to miss; a startup crash forces the issue.

---

### P2: WB-DEP-01 — No requirements.txt or dependency pinning

**Severity:** P2
**Status:** Open

No `requirements.txt` found. Runtime deps (fastapi, uvicorn, PyJWT, requests) are undeclared. Unpinned installs can pull in breaking changes or security-patched versions without notice.

---

### P2: WB-CFG-01 — cloudflared config.yml written with unsanitized env var

**Severity:** P2
**Status:** Open
**File:** `tunnel.py`, `_write_config()`

`_TUNNEL_NAME` from `CLOUDFLARE_TUNNEL_NAME` env var is written directly into YAML config without sanitization. A malicious value with YAML special characters could corrupt the config. Blast radius is limited (localhost config file only), but worth sanitizing.

---

## Strengths

- **HMAC comparison uses `hmac.compare_digest`.** Constant-time comparison prevents timing attacks.
- **GitHub App JWT pattern is correct.** 60-second clock skew buffer, 10-minute expiry, installation token caching with 60-second safety margin.
- **Route dispatch is explicit.** Only known event types are handled; others are logged and dropped.
- **No shell=True** in subprocess calls.
