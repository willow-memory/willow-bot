"""Host probe helpers — mock Ollama HTTP."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from willow_bot.deterministic.host_probe import (
    health,
    ollama_ps,
    ollama_tags,
    probe_model,
)


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


# ── model inventory with digests (IDEAS 61) ───────────────────────────────


def _serve(tags_body, ps_body=None):
    """A loopback Ollama answering /api/tags and /api/ps with fixed JSON."""

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_GET(self) -> None:
            body = tags_body if self.path == "/api/tags" else ps_body
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _policy(**kw):
    from pathlib import Path

    from willow_bot.deterministic.policy import Policy

    return Policy(
        socket_path=Path("/nonexistent/s.sock"),
        ollama_base="http://127.0.0.1:1",
        runs_dir=Path("/nonexistent/runs"),
        default_model="llama3.2:3b",
        chain_tiers=("llama3.2:3b", "willow-lane4-3b"),
        **kw,
    )


def test_tags_keep_size_and_digest_and_mark_against_policy():
    server, base = _serve(
        {
            "models": [
                {"name": "llama3.2:3b", "size": 2019393189, "digest": "a" * 64},
                {"name": "willow-lane4-3b:latest", "size": 5, "digest": "b" * 64},
                {"name": "mistral:7b", "size": 4, "digest": "c" * 64},
            ]
        }
    )
    try:
        out = ollama_tags(base, policy=_policy())
    finally:
        server.shutdown()
    assert out["state"] == "populated" and out["count"] == 3
    assert out["models"] == ["llama3.2:3b", "willow-lane4-3b:latest", "mistral:7b"]
    by = {m["name"]: m for m in out["inventory"]}
    assert by["llama3.2:3b"]["digest"] == "a" * 64 and by["llama3.2:3b"]["size"] == 2019393189
    assert by["llama3.2:3b"]["policy"] == "allowed"
    assert by["willow-lane4-3b:latest"]["policy"] == "allowed"  # bare name means :latest
    assert by["mistral:7b"]["policy"] == "unlisted"
    assert out["missing"] == []


def test_tags_listed_but_missing_is_named():
    server, base = _serve({"models": [{"name": "llama3.2:3b", "digest": "a" * 64}]})
    try:
        out = ollama_tags(base, policy=_policy())
    finally:
        server.shutdown()
    assert out["missing"] == ["willow-lane4-3b"]
    assert out["inventory"][0]["size"] is None


def test_tags_without_policy_still_lists_names_and_digests():
    server, base = _serve({"models": [{"name": "x:1", "digest": "d" * 64}]})
    try:
        out = ollama_tags(base)
    finally:
        server.shutdown()
    assert out["models"] == ["x:1"] and "missing" not in out
    assert "policy" not in out["inventory"][0]


def test_tags_reachable_with_no_models_is_empty_and_everything_listed_is_missing():
    server, base = _serve({"models": []})
    try:
        out = ollama_tags(base, policy=_policy())
    finally:
        server.shutdown()
    assert out["ok"] is True and out["state"] == "empty" and out["inventory"] == []
    assert out["missing"] == ["llama3.2:3b", "willow-lane4-3b"]


def test_tags_unreachable_is_not_empty_and_claims_nothing_missing():
    server, base = _serve({"models": []})
    server.shutdown()
    server.server_close()  # nothing listens on the port now
    out = ollama_tags(base, timeout_s=1.0, policy=_policy())
    assert out["ok"] is False and out["state"] == "unreachable" and out["error"]
    assert out["missing"] is None


@pytest.mark.parametrize("body", [[], {"error": "boom"}, {"models": "none"}, "text"])
def test_tags_malformed_reply_is_unreachable_not_empty(body):
    server, base = _serve(body)
    try:
        out = ollama_tags(base, policy=_policy())
    finally:
        server.shutdown()
    assert out["ok"] is False and out["state"] == "unreachable"


def test_ps_three_states():
    server, base = _serve(None, {"models": [{"name": "llama3.2:3b", "digest": "a" * 64}]})
    try:
        assert ollama_ps(base)["state"] == "populated"
        assert ollama_ps(base)["running"][0]["digest"] == "a" * 64
    finally:
        server.shutdown()
    server, base = _serve(None, {"models": []})
    try:
        assert ollama_ps(base)["state"] == "empty"
    finally:
        server.shutdown()
    server.server_close()
    assert ollama_ps(base, timeout_s=1.0)["state"] == "unreachable"


def test_the_socket_tags_op_marks_against_the_policy():
    from willow_bot.deterministic.socket_server import _dispatch

    server, base = _serve({"models": [{"name": "zzz:1", "digest": "e" * 64}]}, {"models": []})
    try:
        pol = _policy()
        object.__setattr__(pol, "ollama_base", base)
        out = _dispatch({"op": "ollama_tags"}, pol)
        assert out["inventory"][0]["policy"] == "unlisted"
        assert _dispatch({"op": "health"}, pol)["tags"]["inventory"][0]["policy"] == "unlisted"
    finally:
        server.shutdown()
