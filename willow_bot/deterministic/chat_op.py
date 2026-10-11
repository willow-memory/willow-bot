"""The socket's ``chat`` and ``unload`` ops: local models, loopback Ollama only.

``chat`` is one system+user exchange with a local model. ``unload`` asks Ollama
to drop a model from memory now, so a caller walking a ladder of models holds
one in memory at a time. The model must be in the policy's allow-list. No cloud
route, no escalation, no retry: one request, one answer or one refusal.

Every call, refused or answered, leaves one metadata-only row in
``chat-calls.jsonl`` in ``bot_dir()`` (``Policy.chat_call_log``; owner-only 0600,
wherever the socket lives): model, outcome code, tokens, latency, sizes. No
prompt or reply text, ever. A write failure never fails the call, but it is
kept (``chat_log_state``) and the socket ``status`` op and ``bot status`` report
it: populated / empty / unreachable, or ``off``.

Errors are ``{"ok": False, "error": "<CODE>: <detail>"}`` with codes
``EBADREQ``, ``ESIZE``, ``EMODEL``, ``ELOOPBACK``, ``ECHAT``.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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


# Call log: metadata only. One size cap with one rotated backup, the rule
# steward/heartbeat.py uses for its receipts file.
CALL_LOG_MAX_BYTES = 5 * 1024 * 1024
_MODEL_LOG_CAP = 128
_TAIL_BYTES = 64 * 1024

# The last write outcome, in this process (the one that writes the log): None
# after a good write, else (iso time, error text). ``status`` reports it so a
# failing log is never silent.
_last_write_error: tuple[str, str] | None = None


def _open_private(path: Path) -> int:
    """Open the log for append, owner-only. Created 0600; a file already wider
    is tightened (best effort); a symlink at the path is refused (O_NOFOLLOW)."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
    except OSError:
        os.close(fd)
        raise
    return fd


def _append_row(path: Path, line: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_private(path)
    try:
        if os.fstat(fd).st_size >= CALL_LOG_MAX_BYTES:
            # The inode we hold is already 0600, so the backup is too.
            os.replace(path, path.with_name(path.name + ".1"))
            os.close(fd)
            fd = _open_private(path)
        os.write(fd, line)
    finally:
        os.close(fd)


def _log_call(policy: Policy, req: dict, out: dict, started: str) -> None:
    """Append one row to ``policy.chat_call_log_path``: when, model, outcome
    (``ok`` or the error code), ``done_reason``, tokens, ``latency_ms`` and the
    request/reply sizes in bytes. Never the system prompt, the user prompt or the
    reply, nor anything derived from their text. A log that cannot be written
    never fails the call, but the failure is kept for ``chat_log_state``."""
    global _last_write_error
    if not policy.chat_call_log:
        return
    model = req.get("model")
    system, user = req.get("system"), req.get("user")
    bytes_in = (
        len(system.encode("utf-8")) + len(user.encode("utf-8"))
        if isinstance(system, str) and isinstance(user, str)
        else None
    )
    text = out.get("text")
    row = {
        "at": started,
        "op": "chat",
        "model": model[:_MODEL_LOG_CAP] if isinstance(model, str) else None,
        "ok": bool(out.get("ok")),
        "outcome": "ok" if out.get("ok") else str(out.get("error", "")).split(":", 1)[0],
        "done_reason": out.get("done_reason"),
        "tokens_in": out.get("tokens_in"),
        "tokens_out": out.get("tokens_out"),
        "latency_ms": out.get("latency_ms"),
        "bytes_in": bytes_in,
        "bytes_out": len(text.encode("utf-8")) if isinstance(text, str) else None,
    }
    try:
        _append_row(policy.chat_call_log_path, (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8"))
        _last_write_error = None
    except OSError as exc:
        _last_write_error = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), str(exc)[:200])


def _last_row_at(path: Path) -> str | None:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - _TAIL_BYTES))
        tail = fh.read()
    for raw in reversed(tail.split(b"\n")):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("at"), str):
            return rec["at"]
    return None


def chat_log_state(policy: Policy) -> dict:
    """The call log's state, three ways and never collapsed: ``populated``
    (rows exist: ``size``, ``last_at``), ``empty`` (log on, file absent or no
    rows), ``unreachable`` (the file cannot be stat'd or read, or the last write
    failed: ``detail`` names the error). ``off`` when ``chat_call_log`` is false.
    The write failure is this process's; a reader in another process sees the
    file only."""
    path = policy.chat_call_log_path
    if not policy.chat_call_log:
        return {"status": "off", "detail": "chat_call_log is false in the policy", "path": str(path)}
    out: dict = {"path": str(path)}
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{path} is not a regular file")
        if st.st_size == 0:
            out.update(status="empty", size=0, last_at=None, detail="no calls logged yet")
        else:
            last_at = _last_row_at(path)
            if last_at is None:
                raise OSError(f"{st.st_size} bytes but no readable row in the tail")
            out.update(status="populated", size=st.st_size, last_at=last_at)
    except FileNotFoundError:
        out.update(status="empty", size=0, last_at=None, detail="no calls logged yet")
    except OSError as exc:
        out.update(status="unreachable", detail=str(exc)[:200])
    err = _last_write_error
    if err is not None:
        out["status"] = "unreachable"
        out["detail"] = f"last write failed at {err[0]}: {err[1]}"
    return out


def run_chat(policy: Policy, req: dict) -> dict:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _run_chat(policy, req)
    _log_call(policy, req, out, started)
    return out


def _run_chat(policy: Policy, req: dict) -> dict:
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
