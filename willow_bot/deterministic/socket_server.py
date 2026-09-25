"""Unix socket delegate — Ollama stays on the host."""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from willow_bot.deterministic.host_probe import (
    health,
    ollama_ps,
    ollama_tags,
    probe_model,
    runs_summary,
)
from willow_bot.deterministic.policy import Policy, load_policy
from willow_bot.deterministic.runner import run_growth_fixtures
from willow_bot.deterministic.socket_client import client_op


def _dispatch(req: dict, policy: Policy) -> dict:
    op = req.get("op")
    if op == "run_growth":
        fixtures_dir = Path(str(req.get("fixtures_dir", ""))).expanduser()
        model = str(req.get("model") or policy.default_model)
        run_id = str(req.get("run_id") or "kart-delegated")
        limit = req.get("limit")
        lim = int(limit) if limit is not None else None
        out_path = policy.runs_dir / f"{run_id}.jsonl"
        summary = run_growth_fixtures(
            policy,
            fixtures_dir=fixtures_dir,
            model=model,
            out_path=out_path,
            limit=lim,
        )
        return {"ok": True, **summary}
    if op == "health":
        return health(policy.ollama_base)
    if op == "ollama_tags":
        return ollama_tags(policy.ollama_base)
    if op == "ollama_ps":
        return ollama_ps(policy.ollama_base)
    if op == "probe_model":
        model = str(req.get("model") or policy.default_model)
        timeout = float(req.get("timeout_s") or 30.0)
        return probe_model(policy.ollama_base, model, timeout_s=timeout)
    if op == "runs_summary":
        limit = int(req.get("limit") or 15)
        return runs_summary(policy.runs_dir, limit=limit)
    if op == "status":
        import willow_bot.deterministic.ollama as ollama_mod

        body = {
            "ok": True,
            "health": health(policy.ollama_base),
            "runs": runs_summary(policy.runs_dir),
            "runner": {
                "ollama_chat_timeout_s": policy.ollama_chat_timeout_s,
                "ollama_module": str(getattr(ollama_mod, "__file__", "")),
            },
        }
        fix = req.get("fixtures_dir")
        if fix:
            body["fixtures_dir"] = str(Path(str(fix)).expanduser())
        overlay = policy.runs_dir / "flowering-kart-overlay.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
        body["overlay_path"] = str(overlay)
        return body
    return {"ok": False, "error": f"unknown op: {op!r}"}


def _handle_conn(conn: socket.socket, policy: Policy) -> None:
    try:
        data = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        line = data.split(b"\n", 1)[0].decode("utf-8").strip()
        if not line:
            conn.sendall(b'{"ok":false,"error":"empty request"}\n')
            return
        req = json.loads(line)
        result = _dispatch(req, policy)
        conn.sendall(json.dumps(result, sort_keys=True).encode() + b"\n")
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        conn.sendall(json.dumps({"ok": False, "error": str(exc)}).encode() + b"\n")
    finally:
        conn.close()


def serve_forever(policy: Policy | None = None) -> None:
    pol = policy or load_policy()
    sock_path = pol.socket_path
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(8)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(
                target=_handle_conn, args=(conn, pol), daemon=True
            ).start()
    finally:
        server.close()
        if sock_path.exists():
            sock_path.unlink()


def client_run(
    policy: Policy,
    *,
    fixtures_dir: Path,
    model: str,
    run_id: str,
    limit: int | None = None,
) -> dict:
    """Submit one growth job to the host ``serve`` socket (callable from Kart)."""
    req: dict = {
        "op": "run_growth",
        "fixtures_dir": str(fixtures_dir.resolve()),
        "model": model,
        "run_id": run_id,
    }
    if limit is not None:
        req["limit"] = limit
    # Full 12-fixture runs on slow tiers (qwen 4b, llama 8b) can exceed 10 minutes.
    return client_op(policy, req, timeout_s=3600.0)
