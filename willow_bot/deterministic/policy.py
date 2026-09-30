"""Read ``deterministic-policy.json`` under ``$WILLOW_HOME/willow-bot/``."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from willow_bot.paths import bot_dir, willow_home


@dataclass(frozen=True)
class Policy:
    socket_path: Path
    ollama_base: str
    runs_dir: Path
    default_model: str
    chain_tiers: tuple[str, ...]
    ollama_chat_timeout_s: float = 600.0
    # Socket ``chat`` op: the local models it may call (empty = chain_tiers plus
    # default_model), its request-size ceiling and its timeout.
    chat_allowed_models: tuple[str, ...] = ()
    chat_max_request_bytes: int = 262144
    chat_timeout_s: float = 600.0

    @property
    def chat_models(self) -> tuple[str, ...]:
        if self.chat_allowed_models:
            return self.chat_allowed_models
        return tuple(dict.fromkeys((self.default_model, *self.chain_tiers)))


def _default_policy() -> Policy:
    home = willow_home()
    bdir = bot_dir()
    return Policy(
        socket_path=bdir / "deterministic.sock",
        ollama_base=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        runs_dir=bdir / "runs",
        default_model="llama3.2:3b",
        chain_tiers=(
            "llama3.2:3b",
            "willow-lane4-3b",
            "qwen3:4b",
            "gemma3:4b",
            "llama3.1:8b",
        ),
    )


def load_policy() -> Policy:
    path = bot_dir() / "deterministic-policy.json"
    base = _default_policy()
    if not path.is_file():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(raw, dict):
        return base
    home = str(willow_home())

    def _path(val: str | Path) -> Path:
        text = str(val).replace("$WILLOW_HOME", home)
        return Path(text).expanduser()

    socket = raw.get("socket_path") or str(base.socket_path)
    ollama = raw.get("ollama_base") or base.ollama_base
    runs = raw.get("runs_dir") or str(base.runs_dir)
    model = raw.get("default_model") or base.default_model
    tiers_raw = raw.get("chain_tiers")
    if isinstance(tiers_raw, list) and tiers_raw:
        tiers = tuple(str(m) for m in tiers_raw)
    else:
        tiers = base.chain_tiers
    raw_timeout = raw.get("ollama_chat_timeout_s")
    if isinstance(raw_timeout, (int, float)) and float(raw_timeout) > 0:
        chat_timeout = float(raw_timeout)
    else:
        chat_timeout = base.ollama_chat_timeout_s
    allowed_raw = raw.get("chat_allowed_models")
    if isinstance(allowed_raw, list) and allowed_raw:
        allowed = tuple(str(m) for m in allowed_raw)
    else:
        allowed = base.chat_allowed_models
    raw_max = raw.get("chat_max_request_bytes")
    max_bytes = (
        int(raw_max)
        if isinstance(raw_max, int) and not isinstance(raw_max, bool) and raw_max > 0
        else base.chat_max_request_bytes
    )
    raw_chat_t = raw.get("chat_timeout_s")
    if isinstance(raw_chat_t, (int, float)) and float(raw_chat_t) > 0:
        chat_t = float(raw_chat_t)
    else:
        chat_t = chat_timeout
    return Policy(
        chat_allowed_models=allowed,
        chat_max_request_bytes=max_bytes,
        chat_timeout_s=chat_t,
        socket_path=_path(socket),
        ollama_base=str(ollama),
        runs_dir=_path(runs),
        default_model=str(model),
        chain_tiers=tiers,
        ollama_chat_timeout_s=chat_timeout,
    )
