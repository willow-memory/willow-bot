"""The socket's ``chat`` and ``unload`` ops: local models, loopback Ollama only.

``chat`` is one system+user exchange with a local model. ``unload`` asks Ollama
to drop a model from memory now, so a caller walking a ladder of models holds
one in memory at a time. The model must be in the policy's allow-list. No cloud
route, no escalation, no retry: one request, one answer or one refusal.

Errors are ``{"ok": False, "error": "<CODE>: <detail>"}`` with codes
``EBADREQ``, ``ESIZE``, ``EMODEL``, ``ELOOPBACK``, ``ECHAT``.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from willow_bot.deterministic.policy import Policy

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_TOKENS_CAP = 32768
# keep_alive: how long Ollama holds the model after the reply. Bounded to a day
# and never negative: a negative keep_alive pins a model in memory forever.
_KEEP_ALIVE_MAX_S = 86400
_KEEP_ALIVE_RE = re.compile(r"^(\d+)(s|m|h)$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}


def _err(code: str, detail: str) -> dict:
    return {"ok": False, "error": f"{code}: {detail}"}


def is_loopback_base(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and host in LOOPBACK_HOSTS


def _keep_alive_ok(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= _KEEP_ALIVE_MAX_S
    if isinstance(value, str):
        m = _KEEP_ALIVE_RE.match(value)
        return bool(m) and int(m.group(1)) * _UNIT_S[m.group(2)] <= _KEEP_ALIVE_MAX_S
    return False


def _model_refusal(policy: Policy, model) -> dict | None:
    if not isinstance(model, str) or not model:
        return _err("EBADREQ", "model must be a non-empty string")
    if model not in policy.chat_models:
        return _err("EMODEL", f"{model!r} is not in the local-model allow-list")
    if not is_loopback_base(policy.ollama_base):
        return _err("ELOOPBACK", "ollama_base is not a loopback host")
    return None


def _post(policy: Policy, path: str, payload: dict) -> dict:
    http_req = urllib.request.Request(
        f"{policy.ollama_base.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(http_req, timeout=policy.chat_timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
    # format: null, "json", or a JSON schema object (Ollama's structured output).
    fmt = req.get("format")
    if fmt is not None and fmt != "json" and not isinstance(fmt, dict):
        return _err("EBADREQ", "format must be 'json', a JSON schema object, or null")
    max_tokens = req.get("max_tokens")
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            return _err("EBADREQ", "max_tokens must be an integer")
        if not 0 < max_tokens <= _MAX_TOKENS_CAP:
            return _err("EBADREQ", f"max_tokens must be within 1..{_MAX_TOKENS_CAP}")
    keep_alive = req.get("keep_alive")
    if keep_alive is not None and not _keep_alive_ok(keep_alive):
        return _err(
            "EBADREQ",
            f"keep_alive must be seconds (int) or '<n>s|m|h', within 0..{_KEEP_ALIVE_MAX_S}s",
        )
    # think: the caller's own choice, passed through when given. Absent, a qwen3
    # model still gets thinking off (the op's standing default); others get no field.
    think = req.get("think")
    if think is not None and not isinstance(think, bool):
        return _err("EBADREQ", "think must be true, false or null")
    size = len(system.encode("utf-8")) + len(user.encode("utf-8"))
    if isinstance(fmt, dict):
        size += len(json.dumps(fmt).encode("utf-8"))
    if size > policy.chat_max_request_bytes:
        return _err("ESIZE", f"{size} bytes exceeds {policy.chat_max_request_bytes}")
    refusal = _model_refusal(policy, model)
    if refusal is not None:
        return refusal

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
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if think is not None:
        payload["think"] = think
    elif "qwen3" in model.lower():
        payload["think"] = False
    t0 = time.monotonic()
    try:
        body = _post(policy, "/api/chat", payload)
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
        # Ollama's own: "stop", or "length" when the reply hit num_predict
        # (max_tokens). None when the server sends none. A caller scoring the
        # reply needs it to tell a cut-off answer from a wrong one.
        "done_reason": body.get("done_reason"),
    }


def run_unload(policy: Policy, req: dict) -> dict:
    """Drop ``model`` from Ollama's memory now (``/api/generate``, ``keep_alive: 0``)."""
    model = req.get("model")
    refusal = _model_refusal(policy, model)
    if refusal is not None:
        return refusal
    try:
        body = _post(policy, "/api/generate", {"model": model, "keep_alive": 0})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _err("ECHAT", str(exc))
    if not isinstance(body, dict):
        return _err("ECHAT", "unexpected reply shape")
    return {"ok": True, "model": model, "done_reason": body.get("done_reason")}
