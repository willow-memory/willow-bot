"""Bot voice on a PR: one status comment and one bot check per head SHA.

Gap acfd27ae3259 (voice sub-part). Every path here is a unit test — no
GitHub, no network. `requests.get/post/patch` is monkeypatched to a
recording fake, and `github_app._auth_headers` is stubbed. The invariants
tested are the idempotency shape: repeat calls for one (repo, pr, head_sha)
update one row, distinct head_shas write side-by-side, a bad conclusion
is refused before a POST, an auth failure is a receipt line not a raise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

import github_app
from willow_bot import pr_voice


@dataclass
class _FakeResp:
    _status: int = 200
    _payload: Any = field(default_factory=dict)

    @property
    def status_code(self) -> int:
        return self._status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self) -> Any:
        return self._payload


@dataclass
class _Recorder:
    """Records every requests call as (method, url, kwargs). Answers with
    a queue of responses per url-prefix so a test can arrange list-then-
    create vs list-then-update flows deterministically."""
    calls: list[tuple[str, str, dict]] = field(default_factory=list)
    responses: dict[tuple[str, str], list[_FakeResp]] = field(default_factory=dict)

    def _next(self, method: str, url: str) -> _FakeResp:
        # Find the most specific prefix match.
        matches = [(k, v) for k, v in self.responses.items()
                   if k[0] == method and url.startswith(k[1])]
        matches.sort(key=lambda kv: -len(kv[0][1]))
        for _, queue in matches:
            if queue:
                return queue.pop(0)
        raise AssertionError(f"no fake response queued for {method} {url}")

    def get(self, url, headers=None, params=None, timeout=None):  # noqa: ARG002
        self.calls.append(("GET", url, {"params": dict(params or {})}))
        return self._next("GET", url)

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ARG002
        self.calls.append(("POST", url, {"json": json}))
        return self._next("POST", url)

    def patch(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ARG002
        self.calls.append(("PATCH", url, {"json": json}))
        return self._next("PATCH", url)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(pr_voice, "requests", r)
    monkeypatch.setattr(github_app, "_auth_headers",
                        lambda repo: {"Authorization": "Bearer TEST", "Accept": "..."})
    return r


REPO = "willow-memory/willow-mcp"
SHA = "deadbeef" * 5
PR = 42


# ── comment: marker + upsert flow ───────────────────────────────────────────


def test_comment_marker_is_stable_and_unique_per_head_sha():
    a = pr_voice.comment_marker("abc123")
    b = pr_voice.comment_marker("abc124")
    assert a != b
    assert a == "<!-- willow-bot:status head=abc123 -->"


def test_upsert_creates_on_first_call(rec: _Recorder):
    """No existing marker on the PR → POST creates the comment. The
    comment body embeds the marker so a later call finds it."""
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[]),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_status=201, _payload={"id": 555, "html_url": "https://github.com/…/555"}),
    ]

    receipt = pr_voice.upsert_status_comment(REPO, PR, SHA, "Body of the status.")
    assert receipt["status"] == "ok"
    assert receipt["action"] == "created"
    assert receipt["comment_id"] == 555

    posted = next(c for c in rec.calls if c[0] == "POST")
    body = posted[2]["json"]["body"]
    assert pr_voice.comment_marker(SHA) in body
    assert "Body of the status." in body


def test_upsert_updates_when_marker_present(rec: _Recorder):
    """A prior call left a comment carrying the marker → PATCH updates it,
    no POST. The comment id from the existing row is what gets patched."""
    existing = {"id": 999, "body": pr_voice.comment_marker(SHA) + "\n\nOld body",
                "html_url": "https://github.com/…/999"}
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[existing]),
    ]
    rec.responses[("PATCH", f"https://api.github.com/repos/{REPO}/issues/comments/999")] = [
        _FakeResp(_payload={"id": 999, "html_url": "https://github.com/…/999"}),
    ]

    receipt = pr_voice.upsert_status_comment(REPO, PR, SHA, "New body")
    assert receipt["status"] == "ok"
    assert receipt["action"] == "updated"
    assert receipt["comment_id"] == 999
    # No POST was issued.
    assert not any(c[0] == "POST" for c in rec.calls)


def test_distinct_head_shas_write_distinct_comments(rec: _Recorder):
    """A force-push moves the head; the new head deserves a fresh comment
    row, not a rewrite of the old one. Marker uniqueness is what makes
    that safe: the head1 marker is not found on head2's call."""
    head1_marker = pr_voice.comment_marker("head1")
    existing = {"id": 100, "body": head1_marker + "\n\nHead1 body"}
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[existing]),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload={"id": 200, "html_url": ""}),
    ]

    receipt = pr_voice.upsert_status_comment(REPO, PR, "head2", "Second head")
    assert receipt["action"] == "created"
    assert receipt["comment_id"] == 200


def test_upsert_walks_paginated_comments(rec: _Recorder):
    """A PR with more than 100 comments splits across pages; the marker
    can live on any page. The walker stops as soon as a short page is
    returned (fewer than 100 rows)."""
    page1 = [{"id": i, "body": f"noise {i}"} for i in range(1, 101)]
    marker = pr_voice.comment_marker(SHA)
    page2 = [{"id": 200, "body": marker + "\n\nOn page 2"}]
    url = f"https://api.github.com/repos/{REPO}/issues/{PR}/comments"
    rec.responses[("GET", url)] = [_FakeResp(_payload=page1), _FakeResp(_payload=page2)]
    rec.responses[("PATCH", f"https://api.github.com/repos/{REPO}/issues/comments/200")] = [
        _FakeResp(_payload={"id": 200}),
    ]

    receipt = pr_voice.upsert_status_comment(REPO, PR, SHA, "Body")
    assert receipt["action"] == "updated"
    assert receipt["comment_id"] == 200
    gets = [c for c in rec.calls if c[0] == "GET"]
    assert gets[0][2]["params"]["page"] == 1
    assert gets[1][2]["params"]["page"] == 2


def test_upsert_missing_head_sha_is_a_line_not_a_raise(rec: _Recorder):
    receipt = pr_voice.upsert_status_comment(REPO, PR, "", "Body")
    assert receipt["status"] == "could-not-run"
    assert receipt["action"] == "skipped"
    assert receipt["detail"] == "no head_sha"
    assert rec.calls == []  # no GH call was made


def test_upsert_auth_failure_is_a_line(rec: _Recorder, monkeypatch):
    def _boom(_repo):
        raise RuntimeError("no PEM")
    monkeypatch.setattr(github_app, "_auth_headers", _boom)
    receipt = pr_voice.upsert_status_comment(REPO, PR, SHA, "Body")
    assert receipt["status"] == "could-not-run"
    assert "auth: no PEM" in receipt["detail"]
    assert rec.calls == []


def test_upsert_http_failure_on_create_reports_reason(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[]),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_status=422),
    ]
    receipt = pr_voice.upsert_status_comment(REPO, PR, SHA, "Body")
    assert receipt["status"] == "could-not-run"
    assert "create" in receipt["detail"]


# ── check: refuse-before-post + upsert flow ─────────────────────────────────


def test_publish_check_creates_on_first_call(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/commits/{SHA}/check-runs")] = [
        _FakeResp(_payload={"total_count": 0, "check_runs": []}),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/check-runs")] = [
        _FakeResp(_status=201, _payload={"id": 777, "html_url": "https://github.com/…/checks/777"}),
    ]

    receipt = pr_voice.publish_check(
        REPO, SHA, "willow-bot/steward",
        status="completed", conclusion="success",
        output={"title": "steward", "summary": "green"},
    )
    assert receipt["status"] == "ok"
    assert receipt["action"] == "created"
    assert receipt["check_run_id"] == 777

    posted = next(c for c in rec.calls if c[0] == "POST")
    body = posted[2]["json"]
    assert body["name"] == "willow-bot/steward"
    assert body["head_sha"] == SHA
    assert body["status"] == "completed"
    assert body["conclusion"] == "success"


def test_publish_check_updates_when_present(rec: _Recorder):
    existing = {"id": 888, "html_url": "https://github.com/…/checks/888"}
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/commits/{SHA}/check-runs")] = [
        _FakeResp(_payload={"total_count": 1, "check_runs": [existing]}),
    ]
    rec.responses[("PATCH", f"https://api.github.com/repos/{REPO}/check-runs/888")] = [
        _FakeResp(_payload={"id": 888, "html_url": "https://github.com/…/checks/888"}),
    ]
    receipt = pr_voice.publish_check(
        REPO, SHA, "willow-bot/steward",
        status="completed", conclusion="failure",
    )
    assert receipt["action"] == "updated"
    assert receipt["check_run_id"] == 888
    # PATCH must not resend name/head_sha (GitHub rejects those on PATCH).
    patched = next(c for c in rec.calls if c[0] == "PATCH")
    body = patched[2]["json"]
    assert "name" not in body
    assert "head_sha" not in body
    assert body["conclusion"] == "failure"


def test_publish_check_refuses_completed_without_conclusion(rec: _Recorder):
    receipt = pr_voice.publish_check(REPO, SHA, "willow-bot/steward", status="completed")
    assert receipt["status"] == "could-not-run"
    assert "conclusion" in receipt["detail"]
    assert rec.calls == []  # no GH call


def test_publish_check_refuses_bad_conclusion(rec: _Recorder):
    receipt = pr_voice.publish_check(
        REPO, SHA, "willow-bot/steward",
        status="completed", conclusion="green",  # not a GH value
    )
    assert receipt["status"] == "could-not-run"
    assert rec.calls == []


def test_publish_check_refuses_conclusion_on_non_terminal(rec: _Recorder):
    receipt = pr_voice.publish_check(
        REPO, SHA, "willow-bot/steward",
        status="in_progress", conclusion="success",
    )
    assert receipt["status"] == "could-not-run"
    assert rec.calls == []


def test_publish_check_missing_head_sha_is_a_line(rec: _Recorder):
    receipt = pr_voice.publish_check(REPO, "", "willow-bot/steward",
                                     status="completed", conclusion="success")
    assert receipt["status"] == "could-not-run"
    assert receipt["detail"] == "no head_sha"
    assert rec.calls == []


def test_publish_check_filters_by_name_on_the_server(rec: _Recorder):
    """The GET uses `filter=app` and `check_name=NAME` so we only see this
    App's runs by that name — two apps posting a check with the same name
    on the same sha do not confuse this call."""
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/commits/{SHA}/check-runs")] = [
        _FakeResp(_payload={"total_count": 0, "check_runs": []}),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/check-runs")] = [
        _FakeResp(_status=201, _payload={"id": 1}),
    ]
    pr_voice.publish_check(REPO, SHA, "willow-bot/steward",
                           status="completed", conclusion="success")
    got = next(c for c in rec.calls if c[0] == "GET")
    assert got[2]["params"]["filter"] == "app"
    assert got[2]["params"]["check_name"] == "willow-bot/steward"


def test_publish_check_carries_external_id_and_output(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/commits/{SHA}/check-runs")] = [
        _FakeResp(_payload={"total_count": 0, "check_runs": []}),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/check-runs")] = [
        _FakeResp(_status=201, _payload={"id": 1}),
    ]
    pr_voice.publish_check(
        REPO, SHA, "willow-bot/steward",
        status="completed", conclusion="success",
        output={"title": "ok", "summary": "green"},
        external_id="steward-tick-1234",
    )
    posted = next(c for c in rec.calls if c[0] == "POST")
    body = posted[2]["json"]
    assert body["output"] == {"title": "ok", "summary": "green"}
    assert body["external_id"] == "steward-tick-1234"


def test_publish_check_http_failure_is_a_receipt_not_a_raise(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/commits/{SHA}/check-runs")] = [
        _FakeResp(_status=500),
    ]
    receipt = pr_voice.publish_check(REPO, SHA, "willow-bot/steward",
                                     status="completed", conclusion="success")
    assert receipt["status"] == "could-not-run"


# ── the ci-red comment: a second marker, never confused with the status one ──


def test_ci_red_marker_is_distinct_from_the_status_marker():
    m = pr_voice.ci_red_comment_marker(SHA)
    assert m == f"<!-- willow-bot:ci-red:{SHA} -->"
    assert m != pr_voice.comment_marker(SHA)


def test_ci_red_comment_creates_on_first_call(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[]),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_status=201, _payload={"id": 777, "html_url": "https://github.com/…/777"}),
    ]
    receipt = pr_voice.upsert_ci_red_comment(REPO, PR, SHA, "the failure block")
    assert receipt["status"] == "ok" and receipt["action"] == "created" and receipt["comment_id"] == 777
    posted = next(c for c in rec.calls if c[0] == "POST")
    body = posted[2]["json"]["body"]
    assert pr_voice.ci_red_comment_marker(SHA) in body
    assert "the failure block" in body


def test_ci_red_comment_edits_the_same_comment_on_a_second_call(rec: _Recorder):
    """A second red leg on the same head must edit, never duplicate."""
    existing = {"id": 888, "body": pr_voice.ci_red_comment_marker(SHA) + "\n\nfirst failure"}
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[existing]),
    ]
    rec.responses[("PATCH", f"https://api.github.com/repos/{REPO}/issues/comments/888")] = [
        _FakeResp(_payload={"id": 888, "html_url": "https://github.com/…/888"}),
    ]
    receipt = pr_voice.upsert_ci_red_comment(REPO, PR, SHA, "second failure")
    assert receipt["status"] == "ok" and receipt["action"] == "edited" and receipt["comment_id"] == 888
    assert not any(c[0] == "POST" for c in rec.calls)


def test_ci_red_comment_edited_green_on_resolve(rec: _Recorder):
    existing = {"id": 888, "body": pr_voice.ci_red_comment_marker(SHA) + "\n\nfirst failure"}
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[existing]),
    ]
    rec.responses[("PATCH", f"https://api.github.com/repos/{REPO}/issues/comments/888")] = [
        _FakeResp(_payload={"id": 888}),
    ]
    receipt = pr_voice.upsert_ci_red_comment(REPO, PR, SHA, "green at abc1234\nresolved at T")
    assert receipt["action"] == "edited"
    patched = next(c for c in rec.calls if c[0] == "PATCH")
    assert "green at abc1234" in patched[2]["json"]["body"]


def test_ci_red_comment_403_names_pull_requests_write_exactly(rec: _Recorder):
    rec.responses[("GET", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_payload=[]),
    ]
    rec.responses[("POST", f"https://api.github.com/repos/{REPO}/issues/{PR}/comments")] = [
        _FakeResp(_status=403),
    ]
    receipt = pr_voice.upsert_ci_red_comment(REPO, PR, SHA, "body")
    assert receipt["status"] == "could-not-run"
    assert receipt["missing_permission"] == "pull_requests:write"
    assert receipt["action"] == "skipped"


def test_ci_red_comment_missing_head_sha_is_a_line_not_a_raise(rec: _Recorder):
    receipt = pr_voice.upsert_ci_red_comment(REPO, PR, "", "body")
    assert receipt["status"] == "could-not-run" and receipt["action"] == "skipped"
    assert receipt["detail"] == "no head_sha"
    assert rec.calls == []
