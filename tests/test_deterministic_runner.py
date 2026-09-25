"""Deterministic growth runner — mock Ollama, no network."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.runner import growth_prompt, run_growth_fixtures


class _OllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: ANN002
        return

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        content = f"mock:{body.get('model')}"
        out = {"message": {"role": "assistant", "content": content}}
        payload = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_growth_prompt_includes_excerpt_ids():
    fixture = {
        "brief": "Name the seat.",
        "excerpts": [{"id": "ex-1", "text": "to_app loki"}],
    }
    text = growth_prompt(fixture)
    assert "ex-1" in text
    assert "loki" in text


def test_run_growth_fixtures_writes_jsonl(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "S-growth-01.json").write_text(
        json.dumps(
            {
                "id": "S-growth-01",
                "class": "G1",
                "brief": "Say OK",
                "excerpts": [{"id": "ex-a", "text": "hint"}],
            }
        ),
        encoding="utf-8",
    )
    server = HTTPServer(("127.0.0.1", 0), _OllamaHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        policy = Policy(
            socket_path=tmp_path / "sock",
            ollama_base=f"http://127.0.0.1:{port}",
            runs_dir=tmp_path / "runs",
            default_model="test-model",
            chain_tiers=("test-model",),
        )
        out = tmp_path / "runs" / "t1.jsonl"
        summary = run_growth_fixtures(
            policy,
            fixtures_dir=fixtures,
            model="llama3.2:3b",
            out_path=out,
        )
        assert summary["count"] == 1
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        row = json.loads(lines[0])
        assert row["fixture_id"] == "S-growth-01"
        assert row["raw_reply"].startswith("mock:")
        assert row["error"] is None
    finally:
        server.shutdown()
