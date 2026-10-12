"""Loopback Ollama probes — host ``serve`` only; Kart calls via Unix socket."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from willow_bot.deterministic.ollama import chat
from willow_bot.deterministic.policy import Policy


def _fetch_json(url: str, *, timeout_s: float = 5.0) -> tuple[dict | list | None, str | None]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _tag(name: str) -> str:
    """Ollama's own spelling: a bare name means ``:latest``."""
    return name if ":" in name else f"{name}:latest"


def ollama_tags(base_url: str, *, timeout_s: float = 5.0, policy: Policy | None = None) -> dict:
    """What is on disk. ``models`` stays the list of names; ``inventory`` adds
    ``size`` and ``digest`` per model (Ollama's own, as read, nothing pulled or
    pinned). Three states, never collapsed: ``state`` is ``unreachable`` (the
    read failed or the reply was not a model list), ``empty`` (reachable, no
    models) or ``populated``.

    With ``policy`` each model is marked against it: ``allowed`` (in the chat
    allow-list or ``chain_tiers``) or ``unlisted`` (on disk, not listed), and
    ``missing`` names the listed models that are not on disk. On an unreachable
    read ``missing`` is ``None``: nothing can be said about what is absent."""
    data, err = _fetch_json(f"{base_url.rstrip('/')}/api/tags", timeout_s=timeout_s)
    if err is None and not (isinstance(data, dict) and isinstance(data.get("models"), list)):
        err = "unexpected reply shape (no model list)"
    if err:
        out: dict = {"ok": False, "state": "unreachable", "error": err, "models": [], "inventory": []}
        if policy is not None:
            out["missing"] = None
        return out
    inventory = []
    for row in data["models"]:
        if isinstance(row, dict) and row.get("name"):
            size = row.get("size")
            digest = row.get("digest")
            inventory.append(
                {
                    "name": str(row["name"]),
                    "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                    "digest": str(digest) if digest else None,
                }
            )
    out = {
        "ok": True,
        "state": "populated" if inventory else "empty",
        "models": [m["name"] for m in inventory],
        "count": len(inventory),
        "inventory": inventory,
    }
    if policy is not None:
        listed = {_tag(m) for m in (*policy.chat_models, *policy.chain_tiers)}
        for m in inventory:
            m["policy"] = "allowed" if _tag(m["name"]) in listed else "unlisted"
        on_disk = {_tag(m["name"]) for m in inventory}
        out["missing"] = sorted(m for m in dict.fromkeys((*policy.chat_models, *policy.chain_tiers)) if _tag(m) not in on_disk)
    return out


def ollama_ps(base_url: str, *, timeout_s: float = 5.0) -> dict:
    """What is loaded now. ``running`` is Ollama's own rows (each carries its
    ``digest``). ``state`` is ``unreachable`` / ``empty`` / ``populated``."""
    data, err = _fetch_json(f"{base_url.rstrip('/')}/api/ps", timeout_s=timeout_s)
    if err is None and not (isinstance(data, dict) and isinstance(data.get("models") or [], list)):
        err = "unexpected reply shape (no model list)"
    if err:
        return {"ok": False, "state": "unreachable", "error": err, "running": []}
    running = data.get("models") or []
    return {"ok": True, "state": "populated" if running else "empty", "running": running}


def health(base_url: str, *, timeout_s: float = 5.0, policy: Policy | None = None) -> dict:
    tags = ollama_tags(base_url, timeout_s=timeout_s, policy=policy)
    ps = ollama_ps(base_url, timeout_s=timeout_s)
    ok = bool(tags.get("ok")) and bool(ps.get("ok"))
    return {
        "ok": ok,
        "ollama_base": base_url,
        "tags": tags,
        "ps": ps,
    }


def probe_model(
    base_url: str,
    model: str,
    *,
    timeout_s: float = 30.0,
) -> dict:
    reply, latency_ms, err = chat(
        base_url=base_url,
        model=model,
        prompt="Reply with exactly: OK",
        timeout_s=timeout_s,
    )
    return {
        "ok": err is None,
        "model": model,
        "latency_ms": latency_ms,
        "reply_prefix": (reply or "")[:200],
        "error": err,
    }


def runs_summary(runs_dir: Path, *, limit: int = 15) -> dict:
    runs_dir = runs_dir.resolve()
    if not runs_dir.is_dir():
        return {"ok": False, "error": f"runs_dir missing: {runs_dir}", "files": []}
    rows: list[dict] = []
    paths = sorted(
        runs_dir.glob("layer-b-growth-*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    for path in paths:
        st = path.stat()
        lines = 0
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        lines += 1
        except OSError:
            lines = -1
        rows.append(
            {
                "name": path.name,
                "bytes": st.st_size,
                "lines": lines,
                "mtime_utc": int(st.st_mtime),
            }
        )
    return {"ok": True, "runs_dir": str(runs_dir), "files": rows}
