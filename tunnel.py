"""
tunnel.py — external tunnel preflight for willow-bot.
b17: WBTUN1  ΔΣ=42

willow-bot serves GitHub webhooks on a local HTTP port. The public route is
owned by the operator (Pangolin/Newt/Gerbil, reverse proxy, or another tunnel),
not by this process.
"""
from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse

log = logging.getLogger("willow-bot.tunnel")

_BOT_HOST = os.getenv("BOT_HOST", "127.0.0.1")
_BOT_PORT = int(os.getenv("BOT_PORT", "9000"))
_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "").rstrip("/")


def local_url() -> str:
    """Return the local URL that Pangolin or another ingress should target."""
    return f"http://{_BOT_HOST}:{_BOT_PORT}"


def webhook_url() -> str:
    """Return the configured public GitHub webhook URL, if known."""
    if not _PUBLIC_URL:
        return ""
    parsed = urlparse(_PUBLIC_URL)
    if parsed.path.endswith("/webhook"):
        return _PUBLIC_URL
    return f"{_PUBLIC_URL}/webhook"


def _local_port_listening() -> bool:
    try:
        with socket.create_connection((_BOT_HOST, _BOT_PORT), timeout=0.25):
            return True
    except OSError:
        return False


def start() -> bool:
    """Validate external tunnel configuration.

    The tunnel is intentionally managed outside willow-bot. For Pangolin, create
    a resource that forwards the public webhook hostname to `local_url()`.
    """
    if not _PUBLIC_URL:
        log.error("WEBHOOK_PUBLIC_URL is not set; configure Pangolin to forward to %s", local_url())
        return False
    log.info("external tunnel expected: %s -> %s", webhook_url(), local_url())
    return True


def stop() -> None:
    """No-op: external tunnel lifecycle is managed outside willow-bot."""
    log.info("external tunnel lifecycle is operator-managed; nothing to stop")


def status() -> dict:
    """Return webhook ingress configuration and local listener status."""
    return {
        "managed_by": "external",
        "local_url": local_url(),
        "webhook_url": webhook_url(),
        "configured": bool(_PUBLIC_URL),
        "local_listening": _local_port_listening(),
    }
