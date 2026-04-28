"""
bot.py — Willow GitHub bot entry point.
b17: WBBT1  ΔΣ=42

Lightweight FastAPI webhook receiver. Runs on Sean's machine.
GitHub App sends events here via cloudflared tunnel.

Run: uvicorn bot:app --host 0.0.0.0 --port 9000
"""
import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request

import github_app
import quips
import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("willow-bot")

app = FastAPI(title="willow-bot", docs_url=None, redoc_url=None)

_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()


def _verify_signature(body: bytes, sig_header: str) -> bool:
    if not _SECRET:
        log.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(_SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(...),
    x_hub_signature_256: str = Header(default=""),
):
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    log.info("event: %s action: %s", x_github_event, payload.get("action", "—"))

    router.route(x_github_event, payload, github_app.post_comment)

    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok", "frank_mode": bool(os.getenv("FRANK_MODE"))}
