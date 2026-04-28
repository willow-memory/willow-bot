"""
cerebras.py — LLM call with SILENCE handling.
b17: LOKI3

Defaults to Cerebras (fast, cloud). Override with env vars for local model:
  LOKI_API_URL=http://localhost:11434/v1/chat/completions
  LOKI_MODEL=llama3.2:3b
  LOKI_API_KEY=ollama  (or omit — local models often need any non-empty value)
"""
import logging
import os
import sqlite3
from pathlib import Path

import httpx

log = logging.getLogger("loki.cerebras")

_VAULT_PATH = Path.home() / ".willow" / "vault.db"
_KEY_PATH = Path.home() / ".willow" / ".master.key"
_SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8").strip()

_DEFAULT_API_URL = "https://api.cerebras.ai/v1/chat/completions"
_DEFAULT_MODEL = "llama3.1-8b"

_API_URL = os.environ.get("LOKI_API_URL", _DEFAULT_API_URL)
_MODEL = os.environ.get("LOKI_MODEL", _DEFAULT_MODEL)
_EXPLICIT_KEY = os.environ.get("LOKI_API_KEY", "")


def _read_vault(name: str) -> str:
    from cryptography.fernet import Fernet
    f = Fernet(_KEY_PATH.read_bytes().strip())
    conn = sqlite3.connect(str(_VAULT_PATH))
    row = conn.execute(
        "SELECT value_enc FROM credentials WHERE name=?", (name,)
    ).fetchone()
    conn.close()
    if not row:
        raise KeyError(f"Key '{name}' not in vault at {_VAULT_PATH}")
    return f.decrypt(row[0]).decode()


def _api_key() -> str:
    if _EXPLICIT_KEY:
        return _EXPLICIT_KEY
    if "groq" in _API_URL:
        return _read_vault("GROQ_API_KEY")
    if "cerebras" in _API_URL:
        try:
            return _read_vault("CEREBRAS_API_KEY")
        except KeyError:
            # Fall back to Groq with llama-3.3-70b if Cerebras key not set up
            log.warning("CEREBRAS_API_KEY not in vault — falling back to GROQ_API_KEY")
            return _read_vault("GROQ_API_KEY")
    # Local model — try vault, fall back to placeholder
    try:
        return _read_vault("LOKI_API_KEY")
    except KeyError:
        return "local"


def call(context_packet: str) -> str | None:
    """Send context packet to LLM. Returns message string or None (SILENCE)."""
    api_key = _api_key()
    api_url = _API_URL
    model = _MODEL

    # If Cerebras key missing and we fell back to Groq, use Groq endpoint
    if "cerebras" in api_url and "groq" not in api_url:
        try:
            _read_vault("CEREBRAS_API_KEY")
        except KeyError:
            api_url = "https://api.groq.com/openai/v1/chat/completions"
            model = "llama-3.3-70b-versatile"

    response = httpx.post(
        api_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.7,
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": context_packet},
            ],
        },
        timeout=60.0,  # local models can be slower
    )
    if not response.is_success:
        log.error("LLM %s: %s — %s", response.status_code, api_url, response.text[:300])
        response.raise_for_status()

    text = response.json()["choices"][0]["message"]["content"].strip()
    if text == "SILENCE":
        log.info("LLM returned SILENCE — no post")
        return None
    return text
