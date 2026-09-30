"""The socket's ``chat`` op: one system+user exchange with a local model.

Loopback Ollama only. The model must be in the policy's allow-list. No cloud
route, no escalation, no retry: one request, one answer or one refusal.

Errors are ``{"ok": False, "error": "<CODE>: <detail>"}`` with codes
``EBADREQ``, ``ESIZE``, ``EMODEL``, ``ELOOPBACK``, ``ECHAT``.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from willow_bot.deterministic.policy import Policy

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_TOKENS_CAP = 32768


def _err(code: str, detail: str) -> dict:
    return {"ok": False, "error": f"{code}: {detail}"}


def is_loopback_base(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and host in LOOPBACK_HOSTS


def run_chat(policy: Policy, req: dict) -> dict:
    model = req.get("model")
    system = req.get("system")
    user = req.get("user")
    if not isinstance(model, str) or not model:
        return _err("EBADREQ", "model must be a non-empty string")
    if not isinstance(system, str) or not isinstance(user, str):
        return _err("EBADREQ", "system and user must be strings")
    temperature = req.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return _err("EBADREQ", "temperature must be a number")
    if not 0 <= float(temperature) <= 2:
        return _err("EBADREQ", "temperature must be within 0..2")
    fmt = req.get("format")
    if fmt not in (None, "json"):
        return _err("EBADREQ", "format must be 'json' or null")
    max_tokens = req.get("max_tokens")
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            return _err("EBADREQ", "max_tokens must be an integer")
        if not 0 < max_tokens <= _MAX_TOKENS_CAP:
            return _err("EBADREQ", f"max_tokens must be within 1..{_MAX_TOKENS_CAP}")
    size = len(system.encode("utf-8")) + len(user.encode("utf-8"))
    if size > policy.chat_max_request_bytes:
        return _err("ESIZE", f"{size} bytes exceeds {policy.chat_max_request_bytes}")
    if model not in policy.chat_models:
        return _err("EMODEL", f"{model!r} is not in the local-model allow-list")
    if not is_loopback_base(policy.ollama_base):
        return _err("ELOOPBACK", "ollama_base is not a loopback host")

    options: dict = {"temperature": float(temperature)}
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": options,
    }
    if fmt is not None:
        payload["format"] = fmt
    if "qwen3" in model.lower():
        payload["think"] = False
    http_req = urllib.request.Request(
        f"{policy.ollama_base.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(http_req, timeout=policy.chat_timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _err("ECHAT", str(exc))
    latency_ms = int((time.monotonic() - t0) * 1000)
    if not isinstance(body, dict):
        return _err("ECHAT", "unexpected reply shape")
    msg = body.get("message")
    text = msg.get("content") if isinstance(msg, dict) else ""
    if not isinstance(text, str):
        text = str(text)
    return {
        "ok": True,
        "text": text,
        "tokens_in": int(body.get("prompt_eval_count") or 0),
        "tokens_out": int(body.get("eval_count") or 0),
        "latency_ms": latency_ms,
        "model": model,
    }
