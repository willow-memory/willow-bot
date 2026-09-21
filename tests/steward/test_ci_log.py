"""willow_bot.steward.ci_log: job-log fetch + failure-block extraction,
implemented locally (stdlib + requests) so the steward works with the
willow-mcp broker down — the same rules willow-mcp's own pr_checks_read
is getting in a sibling packet, but no import between the two.
"""
from __future__ import annotations

from willow_bot.steward import ci_log


# ── strip_timestamps ─────────────────────────────────────────────────────────

def test_strip_timestamps_drops_the_leading_iso_stamp():
    raw = "2026-09-21T00:12:34.1234567Z hello\nno stamp here\n2026-09-21T00:12:35.0000000Z world\n"
    assert ci_log.strip_timestamps(raw) == "hello\nno stamp here\nworld\n"


# ── extract_failure_block: pytest ────────────────────────────────────────────

def test_extract_pytest_failures_block_to_the_summary_count_line():
    log = (
        "2026-09-21T00:00:00.0000000Z ============================= test session starts =\n"
        "2026-09-21T00:00:01.0000000Z collected 3 items\n"
        "2026-09-21T00:00:02.0000000Z\n"
        "2026-09-21T00:00:03.0000000Z =================================== FAILURES ====\n"
        "2026-09-21T00:00:04.0000000Z ___________________________ test_x ________________\n"
        "2026-09-21T00:00:05.0000000Z     def test_x():\n"
        "2026-09-21T00:00:06.0000000Z >       assert False\n"
        "2026-09-21T00:00:07.0000000Z E       assert False\n"
        "2026-09-21T00:00:08.0000000Z =========================== short test summary info ==\n"
        "2026-09-21T00:00:09.0000000Z FAILED tests/test_x.py::test_x - assert False\n"
        "2026-09-21T00:00:10.0000000Z ======================== 1 failed, 2 passed in 0.12s ==\n"
        "2026-09-21T00:00:11.0000000Z ##[endgroup]\n"
    )
    out = ci_log.extract_failure_block(log)
    assert out["source"] == "pytest"
    assert out["block"].startswith("=================================== FAILURES ====")
    assert out["block"].endswith("1 failed, 2 passed in 0.12s ==")
    assert "##[endgroup]" not in out["block"]
    assert out["trimmed"] is False


# ── extract_failure_block: ruff ──────────────────────────────────────────────

def test_extract_ruff_collects_file_line_col_rows():
    log = (
        "2026-09-21T00:00:00.0000000Z Run ruff check .\n"
        "2026-09-21T00:00:01.0000000Z src/foo.py:10:5: F401 'os' imported but unused\n"
        "2026-09-21T00:00:02.0000000Z src/bar.py:22:1: E501 line too long\n"
        "2026-09-21T00:00:03.0000000Z Found 2 errors.\n"
        "2026-09-21T00:00:04.0000000Z ##[error]Process completed with exit code 1.\n"
    )
    out = ci_log.extract_failure_block(log)
    assert out["source"] == "ruff"
    assert out["block"] == ("src/foo.py:10:5: F401 'os' imported but unused\n"
                            "src/bar.py:22:1: E501 line too long")


# ── extract_failure_block: generic ───────────────────────────────────────────

def test_extract_generic_windows_forty_lines_before_the_error_marker():
    body_lines = [f"line {i}" for i in range(60)]
    body_lines.append("##[error]Process completed with exit code 1.")
    log = "\n".join(body_lines) + "\n"
    out = ci_log.extract_failure_block(log)
    assert out["source"] == "generic"
    lines = out["block"].splitlines()
    assert lines[0] == "line 20"  # 60 - 40
    assert lines[-1] == "##[error]Process completed with exit code 1."


def test_extract_generic_covers_the_first_to_last_error_marker():
    lines = ["line 0"]
    lines += [f"noise {i}" for i in range(5)]
    lines.append("##[error]first")
    lines += [f"noise {i}" for i in range(5)]
    lines.append("##[error]last")
    log = "\n".join(lines) + "\n"
    out = ci_log.extract_failure_block(log)
    assert out["block"].splitlines()[0] == "line 0"
    assert out["block"].splitlines()[-1] == "##[error]last"


# ── extract_failure_block: tail fallback ─────────────────────────────────────

def test_extract_falls_back_to_the_tail_when_nothing_matches():
    log = "\n".join(f"boring line {i}" for i in range(10)) + "\n"
    out = ci_log.extract_failure_block(log)
    assert out["source"] == "tail"
    assert out["block"].splitlines()[-1] == "boring line 9"
    assert out["trimmed"] is False


def test_the_block_is_trimmed_to_the_last_120_lines_and_says_so():
    lines = [f"##[error]line {i}" for i in range(200)]
    log = "\n".join(lines) + "\n"
    out = ci_log.extract_failure_block(log)
    assert out["source"] == "generic"
    assert out["trimmed"] is True
    assert len(out["block"].splitlines()) == ci_log.TRIM_LINES
    assert out["block"].splitlines()[-1] == "##[error]line 199"


# ── fetch_job_log ─────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, *, status_code=200, chunks=(b"hello\n",), headers=None, body=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.closed = False
        self.headers = headers or {}
        self._body = body if body is not None else {}

    def iter_content(self, chunk_size=65536):  # noqa: ARG002
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._body

    def close(self):
        self.closed = True


def _patch_auth(monkeypatch, *, ok=True):
    import willow_bot.steward.ci_log as mod

    if ok:
        monkeypatch.setattr(mod.github_app, "_auth_headers", lambda repo: {"Authorization": "Bearer t"})
    else:
        def _raise(repo):
            raise RuntimeError("no PEM")
        monkeypatch.setattr(mod.github_app, "_auth_headers", _raise)


def test_fetch_job_log_ok(monkeypatch):
    _patch_auth(monkeypatch)
    resp = _Resp(chunks=(b"hello ", b"world\n"))
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text == "hello world\n"
    assert receipt["status"] == "ok" and receipt["truncated"] is False
    assert resp.closed is True


def test_fetch_job_log_403_names_actions_read_exactly(monkeypatch):
    _patch_auth(monkeypatch)
    resp = _Resp(status_code=403, body={"message": "Resource not accessible by integration"})
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert receipt["missing_permission"] == "actions:read"
    assert receipt["status"] == "could-not-run"


def test_fetch_job_log_403_rate_limited_is_not_named_a_permission(monkeypatch):
    _patch_auth(monkeypatch)
    resp = _Resp(status_code=403, headers={"X-RateLimit-Remaining": "0"},
                 body={"message": "API rate limit exceeded"})
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert receipt["status"] == "rate_limited"
    assert "missing_permission" not in receipt


def test_fetch_job_log_403_retry_after_is_not_named_a_permission(monkeypatch):
    _patch_auth(monkeypatch)
    resp = _Resp(status_code=403, headers={"Retry-After": "30"}, body={"message": "secondary rate limit"})
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert receipt["status"] == "rate_limited"
    assert "missing_permission" not in receipt


def test_fetch_job_log_403_unknown_message_is_forbidden_unknown(monkeypatch):
    _patch_auth(monkeypatch)
    resp = _Resp(status_code=403, body={"message": "SAML enforcement required"})
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert "missing_permission" not in receipt
    assert receipt["forbidden_unknown"] is True
    assert "SAML enforcement required" in receipt["detail"]


def test_fetch_job_log_404(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: _Resp(status_code=404))
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert "404" in receipt["detail"]
    assert "missing_permission" not in receipt


def test_fetch_job_log_no_auth_is_a_receipt_not_a_raise(monkeypatch):
    _patch_auth(monkeypatch, ok=False)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert text is None
    assert receipt["status"] == "could-not-run"
    assert "auth:" in receipt["detail"]


def test_fetch_job_log_caps_at_max_bytes(monkeypatch):
    _patch_auth(monkeypatch)
    big_chunk = b"a" * (ci_log.MAX_LOG_BYTES // 2 + 100)
    resp = _Resp(chunks=(big_chunk, big_chunk, big_chunk))
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert len(text.encode("utf-8")) <= ci_log.MAX_LOG_BYTES
    assert receipt["truncated"] is True


def test_fetch_job_log_cap_keeps_the_tail_not_the_head(monkeypatch):
    _patch_auth(monkeypatch)
    # Three chunks bigger than the cap combined; only the LAST chunk's
    # content (the pytest-summary-shaped needle) should survive.
    noise = b"n" * (ci_log.MAX_LOG_BYTES)
    needle = b"FAILURES: the actual pytest summary\n"
    resp = _Resp(chunks=(noise, noise, needle))
    monkeypatch.setattr(ci_log.requests, "get", lambda *a, **k: resp)
    text, receipt = ci_log.fetch_job_log("x/y", 123)
    assert receipt["truncated"] is True
    assert text.endswith("FAILURES: the actual pytest summary\n")
    assert text.count("n") < len(noise)  # the head noise was dropped, not kept whole


def test_classify_403_rate_limit_header_wins_over_message():
    resp = _Resp(status_code=403, headers={"X-RateLimit-Remaining": "0"},
                 body={"message": "Resource not accessible by integration"})
    assert ci_log.classify_403(resp)["kind"] == "rate_limited"


def test_classify_403_known_message_names_the_permission():
    resp = _Resp(status_code=403, body={"message": "Resource not accessible by integration"})
    assert ci_log.classify_403(resp) == {"kind": "missing_permission",
                                         "message": "Resource not accessible by integration"}


def test_classify_403_unknown_message_is_forbidden_unknown():
    resp = _Resp(status_code=403, body={"message": "something new"})
    assert ci_log.classify_403(resp) == {"kind": "forbidden_unknown", "message": "something new"}
