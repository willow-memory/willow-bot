"""Socket ``chat`` op — fake loopback Ollama, no real model."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.socket_server import _dispatch


class _FakeOllama(BaseHTTPRequestHandler):
    seen: list = []
    delay_s = 0.0
    chat_done_reason: str | None = "stop"

    def log_message(self, *_a):  # noqa: ANN002
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        type(self).seen.append((self.path, body))
        if type(self).delay_s:
            time.sleep(type(self).delay_s)
        if self.path == "/api/generate":
            reply = {"model": body.get("model"), "done": True, "done_reason": "unload"}
        else:
            reply = {
                "message": {"role": "assistant", "content": '{"a": 1}'},
                "prompt_eval_count": 11,
                "eval_count": 7,
            }
            if type(self).chat_done_reason is not None:
                reply["done_reason"] = type(self).chat_done_reason
        payload = json.dumps(reply).encode()
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
    _FakeOllama.chat_done_reason = "stop"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _clean_log_state():
    from willow_bot.deterministic import chat_op

    chat_op._last_write_error = None
    yield
    chat_op._last_write_error = None


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
    assert out["done_reason"] == "stop"
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
    [
        {"format": "yaml"},
        {"format": ["answer"]},
        {"temperature": "hot"},
        {"max_tokens": 0},
        {"system": 3},
        {"keep_alive": -1},
        {"keep_alive": True},
        {"keep_alive": 86401},
        {"keep_alive": "25h"},
        {"keep_alive": "forever"},
        {"keep_alive": "-5m"},
        {"keep_alive": 1.5},
        {"think": "no"},
        {"think": 0},
    ],
)
def test_chat_bad_request_refused(tmp_path, fake_ollama, bad):
    out = _dispatch(_req(**bad), _policy(tmp_path, fake_ollama))
    assert out["ok"] is False
    assert out["error"].startswith("EBADREQ")
    assert _FakeOllama.seen == []


def _gemma_policy(tmp_path, base):
    return _policy(tmp_path, base, chat_allowed_models=("gemma4:e2b", "qwen3:4b", "llama3.2:3b"))


def test_chat_passes_the_callers_think_false_through(tmp_path, fake_ollama):
    out = _dispatch(_req(model="gemma4:e2b", think=False), _gemma_policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    (_path, body), = _FakeOllama.seen
    assert body["think"] is False


def test_chat_passes_the_callers_think_true_through_even_for_qwen3(tmp_path, fake_ollama):
    _dispatch(_req(model="qwen3:4b", think=True), _gemma_policy(tmp_path, fake_ollama))
    (_path, body), = _FakeOllama.seen
    assert body["think"] is True


@pytest.mark.parametrize(("model", "expected"), [("qwen3:4b", False), ("gemma4:e2b", None), ("llama3.2:3b", None)])
def test_chat_without_think_keeps_the_standing_default(tmp_path, fake_ollama, model, expected):
    _dispatch(_req(model=model), _gemma_policy(tmp_path, fake_ollama))
    (_path, body), = _FakeOllama.seen
    assert body.get("think", None) is expected


def test_chat_reports_a_length_stop(tmp_path, fake_ollama):
    """A reply cut off at num_predict says so; the caller scores it knowing."""
    _FakeOllama.chat_done_reason = "length"
    out = _dispatch(_req(max_tokens=8), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    assert out["done_reason"] == "length"


def test_chat_without_a_done_reason_reports_none(tmp_path, fake_ollama):
    _FakeOllama.chat_done_reason = None
    out = _dispatch(_req(), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    assert out["done_reason"] is None


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["answer", "confidence"],
}


def test_chat_json_schema_format_passes_through(tmp_path, fake_ollama):
    out = _dispatch(_req(format=SCHEMA), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    (_path, body), = _FakeOllama.seen
    assert body["format"] == SCHEMA


def test_chat_schema_counts_toward_request_size(tmp_path, fake_ollama):
    # system+user alone fit; the schema pushes the request over the ceiling.
    pol = _policy(tmp_path, fake_ollama, chat_max_request_bytes=40)
    assert _dispatch(_req(), pol)["ok"] is True
    _FakeOllama.seen = []
    out = _dispatch(_req(format=SCHEMA), pol)
    assert out["error"].startswith("ESIZE")
    assert _FakeOllama.seen == []


@pytest.mark.parametrize("keep_alive", [0, 600, 86400, "10m", "0s", "24h"])
def test_chat_keep_alive_passes_through(tmp_path, fake_ollama, keep_alive):
    out = _dispatch(_req(keep_alive=keep_alive), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    (_path, body), = _FakeOllama.seen
    assert body["keep_alive"] == keep_alive


def test_chat_without_keep_alive_sends_none(tmp_path, fake_ollama):
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    (_path, body), = _FakeOllama.seen
    assert "keep_alive" not in body


def test_unload_happy_path(tmp_path, fake_ollama):
    out = _dispatch({"op": "unload", "model": "qwen3:4b"}, _policy(tmp_path, fake_ollama))
    assert out == {"ok": True, "model": "qwen3:4b", "done_reason": "unload"}
    (path, body), = _FakeOllama.seen
    assert path == "/api/generate"
    assert body == {"model": "qwen3:4b", "keep_alive": 0}


@pytest.mark.parametrize(
    ("req", "base", "code"),
    [
        ({"op": "unload", "model": "gpt-5"}, None, "EMODEL"),
        ({"op": "unload"}, None, "EBADREQ"),
        ({"op": "unload", "model": "llama3.2:3b"}, "http://192.0.2.7:11434", "ELOOPBACK"),
    ],
)
def test_unload_refusals_never_reach_ollama(tmp_path, fake_ollama, req, base, code):
    out = _dispatch(req, _policy(tmp_path, base or fake_ollama))
    assert out["ok"] is False
    assert out["error"].startswith(code)
    assert _FakeOllama.seen == []


def test_existing_ops_unchanged(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama)
    pol.runs_dir.mkdir()
    assert _dispatch({"op": "nope"}, pol) == {"ok": False, "error": "unknown op: 'nope'"}
    assert _dispatch({"op": "runs_summary"}, pol)["ok"] is True


# ── call log, content off (IDEAS 66) ──────────────────────────────────────


def _log(tmp_path):
    """Where the log lives: bot_dir (WILLOW_HOME is tmp_path), not beside the socket."""
    return tmp_path / "willow-bot" / "chat-calls.jsonl"


def _rows(tmp_path) -> list[dict]:
    path = _log(tmp_path)
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def test_call_log_records_metadata_and_never_content(tmp_path, fake_ollama):
    secret_prompt, secret_system = "TOP-SECRET-USER-TEXT", "TOP-SECRET-SYSTEM-TEXT"
    out = _dispatch(_req(user=secret_prompt, system=secret_system), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True
    [row] = _rows(tmp_path)
    assert row["model"] == "llama3.2:3b" and row["ok"] is True and row["outcome"] == "ok"
    assert (row["tokens_in"], row["tokens_out"]) == (11, 7)
    assert row["done_reason"] == "stop" and isinstance(row["latency_ms"], int)
    assert row["bytes_in"] == len(secret_prompt) + len(secret_system)
    assert row["bytes_out"] == len(out["text"])
    assert row["at"].endswith("Z")
    raw = _log(tmp_path).read_text()
    assert "TOP-SECRET" not in raw and out["text"] not in raw
    assert set(row) == {
        "at", "op", "model", "ok", "outcome", "done_reason", "tokens_in",
        "tokens_out", "latency_ms", "bytes_in", "bytes_out",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("kw", "code"),
    [({"model": "gpt-5"}, "EMODEL"), ({"user": "x" * 50}, "ESIZE"), ({"temperature": "hot"}, "EBADREQ")],
)
def test_call_log_records_refusals_by_code(tmp_path, fake_ollama, kw, code):
    extra = {"chat_max_request_bytes": 10} if code == "ESIZE" else {}
    out = _dispatch(_req(**kw), _policy(tmp_path, fake_ollama, **extra))
    assert out["ok"] is False
    [row] = _rows(tmp_path)
    assert row["ok"] is False and row["outcome"] == code
    assert row["tokens_in"] is None and row["bytes_out"] is None


def test_call_log_records_a_failed_call(tmp_path, fake_ollama):
    _FakeOllama.delay_s = 1.5
    _dispatch(_req(), _policy(tmp_path, fake_ollama, chat_timeout_s=0.3))
    [row] = _rows(tmp_path)
    assert row["outcome"] == "ECHAT" and row["ok"] is False


def test_call_log_appends_one_row_per_call(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama)
    for _ in range(3):
        _dispatch(_req(), pol)
    assert len(_rows(tmp_path)) == 3


def test_call_log_can_be_turned_off(tmp_path, fake_ollama):
    out = _dispatch(_req(), _policy(tmp_path, fake_ollama, chat_call_log=False))
    assert out["ok"] is True
    assert not _log(tmp_path).exists()


def test_call_log_is_bounded_with_one_rotated_backup(tmp_path, fake_ollama, monkeypatch):
    from willow_bot.deterministic import chat_op

    monkeypatch.setattr(chat_op, "CALL_LOG_MAX_BYTES", 200)
    pol = _policy(tmp_path, fake_ollama)
    for _ in range(12):
        _dispatch(_req(), pol)
    live = _log(tmp_path)
    backup = live.with_name("chat-calls.jsonl.1")
    assert backup.exists() and live.stat().st_size < 600
    assert not live.with_name("chat-calls.jsonl.2").exists()


def test_a_log_that_cannot_be_written_never_fails_the_call(tmp_path, fake_ollama):
    _log(tmp_path).mkdir(parents=True)  # a directory where the file goes
    out = _dispatch(_req(), _policy(tmp_path, fake_ollama))
    assert out["ok"] is True and out["text"] == '{"a": 1}'


def test_policy_file_can_turn_the_log_off(tmp_path):
    from willow_bot import paths
    from willow_bot.deterministic.policy import load_policy

    assert load_policy().chat_call_log is True
    (paths.bot_dir()).mkdir(parents=True, exist_ok=True)
    (paths.bot_dir() / "deterministic-policy.json").write_text('{"chat_call_log": false}')
    assert load_policy().chat_call_log is False
    (paths.bot_dir() / "deterministic-policy.json").write_text('{"chat_call_log": "no"}')
    assert load_policy().chat_call_log is True  # not a bool: the default stands


# ── call log: 0600, pinned to bot_dir, three-state status (IDEAS 66) ─────────


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def umask_022():
    """The umask that would hand a plain open() 0644; restored after."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_call_log_is_created_owner_only(tmp_path, fake_ollama, umask_022):
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    assert _mode(_log(tmp_path)) == 0o600


def test_rotated_backup_is_owner_only_too(
    tmp_path, fake_ollama, monkeypatch, umask_022
):
    from willow_bot.deterministic import chat_op

    monkeypatch.setattr(chat_op, "CALL_LOG_MAX_BYTES", 200)
    pol = _policy(tmp_path, fake_ollama)
    for _ in range(6):
        _dispatch(_req(), pol)
    live = _log(tmp_path)
    backup = live.with_name("chat-calls.jsonl.1")
    assert backup.exists()
    assert _mode(live) == 0o600 and _mode(backup) == 0o600


def test_a_wider_existing_log_is_tightened_before_it_rotates(tmp_path, fake_ollama, monkeypatch):
    from willow_bot.deterministic import chat_op

    live = _log(tmp_path)
    live.parent.mkdir(parents=True)
    live.write_text("x" * 300 + "\n")
    live.chmod(0o666)
    monkeypatch.setattr(chat_op, "CALL_LOG_MAX_BYTES", 200)
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    assert _mode(live) == 0o600
    assert _mode(live.with_name("chat-calls.jsonl.1")) == 0o600  # the wide inode, now tight


def test_a_wider_existing_log_is_tightened_in_place(tmp_path, fake_ollama):
    live = _log(tmp_path)
    live.parent.mkdir(parents=True)
    live.write_text("")
    live.chmod(0o664)
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    assert _mode(live) == 0o600 and len(_rows(tmp_path)) == 1


def test_a_symlink_at_the_log_path_is_refused(tmp_path, fake_ollama):
    from willow_bot.deterministic import chat_op

    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    live = _log(tmp_path)
    live.parent.mkdir(parents=True)
    live.symlink_to(target)
    pol = _policy(tmp_path, fake_ollama)
    out = _dispatch(_req(), pol)
    assert out["ok"] is True
    assert target.read_text() == "keep"
    assert chat_op.chat_log_state(pol)["status"] == "unreachable"


def test_a_custom_socket_path_does_not_move_the_log(tmp_path, fake_ollama):
    elsewhere = tmp_path / "pub"
    elsewhere.mkdir()
    pol = Policy(
        socket_path=elsewhere / "s.sock",
        ollama_base=fake_ollama,
        runs_dir=tmp_path / "runs",
        default_model="llama3.2:3b",
        chain_tiers=("llama3.2:3b",),
    )
    from willow_bot import paths

    assert pol.chat_call_log_path == paths.bot_dir() / "chat-calls.jsonl"
    _dispatch(_req(), pol)
    assert len(_rows(tmp_path)) == 1
    assert not list(elsewhere.iterdir())


def _state(tmp_path, base="http://127.0.0.1:1", **kw):
    from willow_bot.deterministic.chat_op import chat_log_state

    return chat_log_state(_policy(tmp_path, base, **kw))


def test_status_empty_when_log_absent_or_zero_rows(tmp_path):
    assert _state(tmp_path)["status"] == "empty"
    _log(tmp_path).parent.mkdir(parents=True)
    _log(tmp_path).write_text("")
    assert _state(tmp_path)["status"] == "empty"


def test_status_populated_names_size_and_last_row_time(tmp_path, fake_ollama):
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    st = _state(tmp_path)
    assert st["status"] == "populated"
    assert st["size"] == _log(tmp_path).stat().st_size > 0
    assert st["last_at"] == _rows(tmp_path)[-1]["at"]


def test_status_unreachable_when_the_file_cannot_be_read(tmp_path):
    _log(tmp_path).mkdir(parents=True)  # a directory where the file goes
    st = _state(tmp_path)
    assert st["status"] == "unreachable" and "not a regular file" in st["detail"]
    _log(tmp_path).rmdir()
    _log(tmp_path).write_text("not json at all\n")  # bytes, but no row
    assert _state(tmp_path)["status"] == "unreachable"


def test_status_off_when_the_log_is_off(tmp_path):
    assert _state(tmp_path, chat_call_log=False)["status"] == "off"


def test_a_write_failure_does_not_fail_the_call_and_shows_in_status(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama)
    _dispatch(_req(), pol)
    assert _state(tmp_path)["status"] == "populated"
    _log(tmp_path).chmod(0o400)
    _log(tmp_path).unlink()
    _log(tmp_path).mkdir()  # the write now fails
    out = _dispatch(_req(), pol)
    assert out["ok"] is True and out["text"] == '{"a": 1}'
    st = _state(tmp_path)
    assert st["status"] == "unreachable" and "last write failed" in st["detail"]
    sock = _dispatch({"op": "status"}, pol)
    assert sock["chat_log"]["status"] == "unreachable"
    assert "last write failed" in sock["chat_log"]["detail"]
    # A later good write clears it.
    _log(tmp_path).rmdir()
    _dispatch(_req(), pol)
    assert _state(tmp_path)["status"] == "populated"


def test_a_write_failure_is_not_reported_as_empty(tmp_path, fake_ollama):
    pol = _policy(tmp_path, fake_ollama)
    _log(tmp_path).mkdir(parents=True)
    _dispatch(_req(), pol)
    assert _state(tmp_path)["status"] != "empty"


def test_bot_status_reports_the_chat_log(tmp_path, fake_ollama):
    from willow_bot import status

    assert status.report()["chat_log"]["status"] == "empty"
    assert any(ln.startswith("  chat_log") for ln in status.brief().splitlines())
    _dispatch(_req(), _policy(tmp_path, fake_ollama))
    rep = status.report()["chat_log"]
    assert rep["status"] == "populated"
    assert "bytes, last call" in status.brief()
    (tmp_path / "willow-bot" / "deterministic-policy.json").write_text('{"chat_call_log": false}')
    assert status.report()["chat_log"]["status"] == "off"
    _log(tmp_path).unlink()
    (tmp_path / "willow-bot" / "deterministic-policy.json").write_text("{}")
    _log(tmp_path).mkdir()
    assert status.report()["chat_log"]["status"] == "unreachable"
