"""Host probe helpers — mock Ollama HTTP."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from willow_bot.deterministic.host_probe import health, ollama_tags, probe_model


class _TagsHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: ANN002
        return

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            payload = json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()
        elif self.path == "/api/ps":
            payload = json.dumps({"models": []}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = json.dumps({"message": {"content": "OK"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_health_and_probe_model():
    server = HTTPServer(("127.0.0.1", 0), _TagsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        h = health(base)
        assert h["ok"] is True
        tags = ollama_tags(base)
        assert tags["ok"] is True
        assert "llama3.2:3b" in tags["models"]
        probe = probe_model(base, "llama3.2:3b", timeout_s=5.0)
        assert probe["ok"] is True
    finally:
        server.shutdown()
