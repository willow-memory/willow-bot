"""Loopback Ollama chat — stdlib only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


def chat(
    *,
    base_url: str,
    model: str,
    prompt: str,
    timeout_s: float = 300.0,
) -> tuple[str, int, str | None]:
    """Return ``(reply_text, latency_ms, error)``. ``error`` is set on failure."""
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }
    # qwen3 defaults to thinking mode; long "thinking" phases hit common ~120s
    # Ollama/client limits while content stays empty (see flowering qwen3:4b rows).
    if "qwen3" in model.lower():
        payload["think"] = False
    body = json.dumps(payload).encode("utf-8")
    url = f"{base_url.rstrip('/')}/api/chat"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return "", ms, str(exc)
    ms = int((time.monotonic() - t0) * 1000)
    msg = payload.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else ""
    if not isinstance(content, str):
        content = str(content)
    return content, ms, None
