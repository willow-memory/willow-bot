"""
bot.py — Willow GitHub bot entry point.
b17: WBBT1  ΔΣ=42

Lightweight FastAPI webhook receiver. Runs on USER's machine.
GitHub App sends events here through Pangolin or another external tunnel.

Run: uvicorn bot:app --host 127.0.0.1 --port 9000
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

_SECRET_RAW = os.getenv("GITHUB_WEBHOOK_SECRET", "")
if not _SECRET_RAW:
    raise RuntimeError("GITHUB_WEBHOOK_SECRET must be set — refusing to start without signature verification")
_SECRET = _SECRET_RAW.encode()


def _verify_signature(body: bytes, sig_header: str) -> bool:
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


@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok", "frank_mode": bool(os.getenv("FRANK_MODE"))}
