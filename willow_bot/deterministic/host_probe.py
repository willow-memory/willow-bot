"""Loopback Ollama probes — host ``serve`` only; Kart calls via Unix socket."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from willow_bot.deterministic.ollama import chat


def _fetch_json(url: str, *, timeout_s: float = 5.0) -> tuple[dict | list | None, str | None]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, str(exc)


def ollama_tags(base_url: str, *, timeout_s: float = 5.0) -> dict:
    data, err = _fetch_json(f"{base_url.rstrip('/')}/api/tags", timeout_s=timeout_s)
    if err:
        return {"ok": False, "error": err, "models": []}
    models = []
    if isinstance(data, dict):
        for row in data.get("models") or []:
            if isinstance(row, dict) and row.get("name"):
                models.append(str(row["name"]))
    return {"ok": True, "models": models, "count": len(models)}


def ollama_ps(base_url: str, *, timeout_s: float = 5.0) -> dict:
    data, err = _fetch_json(f"{base_url.rstrip('/')}/api/ps", timeout_s=timeout_s)
    if err:
        return {"ok": False, "error": err, "running": []}
    running = data if isinstance(data, dict) else {}
    return {"ok": True, "running": running.get("models") or []}


def health(base_url: str, *, timeout_s: float = 5.0) -> dict:
    tags = ollama_tags(base_url, timeout_s=timeout_s)
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
