---
b17: WBOT1
title: Security Audit — willow-bot
date: 2026-05-06
auditor: Hanuman (Claude Code, Sonnet 4.6)
status: updated
---

# Security Audit — willow-bot

Part of Level 2 full-fleet security audit. willow-bot — FastAPI GitHub App webhook receiver. Listens on localhost port 9000 behind an operator-managed ingress such as Pangolin. Processes pull_request, push, check_run, create, and issues events; posts comments/reactions via GitHub App JWT auth.

## Rubric Results

| # | Check | Status | Notes |
|---|---|---|---|
| R1 | SQL injection | N/A | No database queries |
| R2 | Shell injection | ✅ PASS | No subprocess/os.system in event handlers; no tunnel subprocess is spawned by the bot |
| R3 | Path traversal | ✅ PASS | No user-controlled path operations; private key path from env var with home-relative default |
| R4 | Hardcoded credentials | ✅ PASS | All secrets via env vars (GITHUB_WEBHOOK_SECRET, GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH) |
| R5 | CORS wildcard | ✅ PASS | FastAPI default — no CORS middleware configured; internal webhook receiver only |
| R6 | XSS | N/A | API only, no HTML rendering |
| R7 | Unsigned code execution | ✅ PASS | No eval(), exec(), or dynamic imports |
| R8 | Missing auth on APIs | ✅ PASS | Startup fails closed when GITHUB_WEBHOOK_SECRET is unset |
| R9 | Bare except swallowing errors | ⚠️ WARN | `router.py` silently drops unhandled event types (`log.debug` only) |
| R10 | Predictable temp paths | ✅ PASS | No temp files; ingress lifecycle is operator-managed outside the bot |
| R11 | Race conditions | ✅ PASS | Single asyncio event loop; installation token cache is dict (not thread-safe, but single-threaded) |
| R12 | safe_integration.py status() | ❌ MISSING | No safe_integration.py present |
| R13 | Entry point importable | ✅ PASS | `uvicorn bot:app` pattern; imports clean |
| R14 | requirements.txt pinned | ✅ PASS | `requirements.txt` pins runtime dependencies |
| R15 | No hardcoded dev paths | ✅ PASS | All paths use env vars or `Path.home()` |

## Findings

### P1: WB-SIG-01 — Webhook signature verification must fail closed

**Severity:** P1
**Status:** Fixed
**File:** `bot.py`, startup secret check and `_verify_signature()`

```python
_SECRET_RAW = os.getenv("GITHUB_WEBHOOK_SECRET", "")
if not _SECRET_RAW:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET must be set — refusing to start without signature verification")
_SECRET = _SECRET_RAW.encode()
```

When `GITHUB_WEBHOOK_SECRET` is not set, the bot now refuses to start. Any request that reaches the local service must still carry a valid GitHub HMAC signature.

This preserves fail-closed behavior even when Pangolin or another ingress is misconfigured.

**Resolution:** fail at startup, not at request time. A missing secret is a hard configuration error.

---

### P2: WB-DEP-01 — Runtime dependencies must be pinned

**Severity:** P2
**Status:** Fixed

`requirements.txt` pins FastAPI, Uvicorn, PyJWT, and Requests.

---

### P2: WB-CFG-01 — Tunnel configuration should be operator-managed

**Severity:** P2
**Status:** Fixed
**File:** `tunnel.py`

`tunnel.py` no longer writes provider config or launches a tunnel subprocess. It reports local listener and public webhook configuration so Pangolin or another ingress can own the route lifecycle.

---

## Strengths

- **HMAC comparison uses `hmac.compare_digest`.** Constant-time comparison prevents timing attacks.
- **GitHub App JWT pattern is correct.** 60-second clock skew buffer, 10-minute expiry, installation token caching with 60-second safety margin.
- **Route dispatch is explicit.** Only known event types are handled; others are logged and dropped.
- **No shell=True** in subprocess calls.
