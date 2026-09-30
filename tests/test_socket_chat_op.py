"""Socket ``chat`` op — fake loopback Ollama, no real model."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.socket_server import _dispatch


class _FakeOllama(BaseHTTPRequestHandler):
    seen: list = []
    delay_s = 0.0

    def log_message(self, *_a):  # noqa: ANN002
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        type(self).seen.append((self.path, body))
        if type(self).delay_s:
            time.sleep(type(self).delay_s)
        payload = json.dumps(
            {
                "message": {"role": "assistant", "content": '{"a": 1}'},
                "prompt_eval_count": 11,
                "eval_count": 7,
            }
        ).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass


@pytest.fixture
def fake_ollama():
    _FakeOllama.seen = []
    _FakeOllama.delay_s = 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _policy(tmp_path, base: str, **kw) -> Policy:
    return Policy(
        socket_path=tmp_path / "s.sock",
        ollama_base=base,
        runs_dir=tmp_path / "runs",
        default_model="llama3.2:3b",
        chain_tiers=("llama3.2:3b", "qwen3:4b"),
        **kw,
    )


def _req(**over) -> dict:
    req = {"op": "chat", "model": "llama3.2:3b", "system": "be brief", "user": "hi"}
    req.update(over)
    return req


def test_chat_happy_path(tmp_path, fake_ollama):
    out = _dispatch(_req(format="json", max_tokens=50), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    assert out["text"] == '{"a": 1}'
    assert out["tokens_in"] == 11
    assert out["tokens_out"] == 7
    assert out["model"] == "llama3.2:3b"
    assert isinstance(out["latency_ms"], int)
    (path, body), = _FakeOllama.seen
    assert path == "/api/chat"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "num_predict": 50}


def test_chat_non_loopback_host_refused(tmp_path, fake_ollama):
    out = _dispatch(_req(), _policy(tmp_path, "http://192.0.2.7:11434"))
    assert out["ok"] is False
    assert out["error"].startswith("ELOOPBACK")
    assert _FakeOllama.seen == []


def test_chat_model_outside_allow_list_refused(tmp_path, fake_ollama):
    out = _dispatch(_req(model="gpt-5"), _policy(tmp_path, fake_ollama))
    assert out["ok"] is False
    assert out["error"].startswith("EMODEL")
    assert _FakeOllama.seen == []


def test_chat_explicit_allow_list_overrides_tiers(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama, chat_allowed_models=("only:1b",))
    assert _dispatch(_req(), pol)["error"].startswith("EMODEL")


def test_chat_oversize_request_refused(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama, chat_max_request_bytes=10)
    out = _dispatch(_req(user="x" * 50), pol)
    assert out["ok"] is False
    assert out["error"].startswith("ESIZE")
    assert _FakeOllama.seen == []


def test_chat_timeout_is_not_ok_and_not_retried(tmp_path, fake_ollama):
    _FakeOllama.delay_s = 1.5
    pol = _policy(tmp_path, fake_ollama, chat_timeout_s=0.3)
    out = _dispatch(_req(), pol)
    assert out["ok"] is False
    assert out["error"].startswith("ECHAT")
    time.sleep(0.2)
    assert len(_FakeOllama.seen) == 1


@pytest.mark.parametrize(
    "bad",
    [{"format": "yaml"}, {"temperature": "hot"}, {"max_tokens": 0}, {"system": 3}],
)
def test_chat_bad_request_refused(tmp_path, fake_ollama, bad):
    out = _dispatch(_req(**bad), _policy(tmp_path, fake_ollama))
    assert out["ok"] is False
    assert out["error"].startswith("EBADREQ")


def test_existing_ops_unchanged(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama)
    pol.runs_dir.mkdir()
    assert _dispatch({"op": "nope"}, pol) == {"ok": False, "error": "unknown op: 'nope'"}
    assert _dispatch({"op": "runs_summary"}, pol)["ok"] is True
