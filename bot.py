"""
bot.py — Willow GitHub bot entry point.
b17: WBBT1  ΔΣ=42

Lightweight FastAPI webhook receiver. Runs on USER's machine.
GitHub App sends events here through Pangolin or another external tunnel.

Secrets resolve from the operator data vault (see credentials.py), not from
a checkout-local .env of record.

Run: uvicorn bot:app --host 127.0.0.1 --port 9000
"""
import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request

import credentials
import github_app
import quips
import router
from willow_bot import delivery_dedup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("willow-bot")

app = FastAPI(title="willow-bot", docs_url=None, redoc_url=None)


# Credentials resolve lazily on the first webhook, not at module import.
# Eager resolve at load meant every test importing bot.py had to prime the
# full vault chain (PEM, app_id, webhook secret) even to exercise a
# handler that never verified a signature — and a systemd-style import
# under a rotated PEM could crash the whole worker before uvicorn came up.
# Cache once after the first success so subsequent requests do not re-read
# disk. github_app._cred() uses the same shape; a rename to a shared
# helper is possible when a third caller lands.
_cred_cache: credentials.BotCredentials | None = None


def _cred() -> credentials.BotCredentials:
    global _cred_cache
    if _cred_cache is None:
        _cred_cache = credentials.resolve(require_complete=True)
        log.info("credentials loaded (%s)", _cred_cache.source)
    return _cred_cache


def reset_credential_cache() -> None:
    """Test hook — drop the process-wide credential cache.

    Mirrors ``github_app.reset_credential_cache`` so a test rotating the
    vault mid-suite can force the next request to re-resolve.
    """
    global _cred_cache
    _cred_cache = None


def _verify_signature(body: bytes, sig_header: str) -> bool:
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    secret = _cred().webhook_secret.encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(...),
    x_hub_signature_256: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
):
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    # Gap acfd27ae3259 (webhook idempotency): a redelivery of the same
    # X-GitHub-Delivery id short-circuits before dispatch, so router.route
    # and fleet_bridge.handle don't double-write journals or double-count
    # a merge. The inbox and gitsync paths are semantic-idempotent already,
    # but ci_outcomes.jsonl and event-log.jsonl are pure append and would
    # take a duplicate row without this guard. An empty header (a webhook
    # that is not GitHub's) is accepted with 200 as a dispatch-time no-op
    # rather than a dedup — the signature check above is what rejects the
    # non-GitHub caller. See willow_bot/delivery_dedup.py.
    if not delivery_dedup.mark_seen(x_github_delivery):
        log.info("event: %s action: %s delivery: %s (redelivery — skipped)",
                 x_github_event, payload.get("action", "—"), x_github_delivery)
        return {"ok": True, "dedup": "delivery_seen", "delivery": x_github_delivery}

    log.info("event: %s action: %s delivery: %s",
             x_github_event, payload.get("action", "—"), x_github_delivery or "—")

    router.route(x_github_event, payload, github_app.post_comment)

    return {"ok": True, "delivery": x_github_delivery or None}


@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok", "frank_mode": bool(os.getenv("FRANK_MODE"))}
