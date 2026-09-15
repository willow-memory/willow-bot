"""Operator assignment for bot-opened PRs.

Gap acfd27ae3259 (assignment sub-part). requests is monkeypatched.
The invariants: unset env is 'absent' (not error), assign and review
are independent successes, GitHub's silent-drop of an unreachable
login shows up in the receipt as a specific line, a 422 on the
reviewer POST is 'already or refused', not a raise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import github_app
from willow_bot import pr_assign


REPO = "willow-memory/willow-mcp"
PR = 42
OP = "rudi193-cmd"


@dataclass
class _FakeResp:
    _status: int = 200
    _payload: Any = field(default_factory=dict)

    def raise_for_status(self) -> None:
        if self._status >= 400 and self._status not in (422,):
            raise RuntimeError(f"HTTP {self._status}")

    def json(self) -> Any:
        return self._payload

    @property
    def status_code(self) -> int:
        return self._status


@dataclass
class _Recorder:
    calls: list[tuple[str, str, dict]] = field(default_factory=list)
    responses: dict[tuple[str, str], list[_FakeResp]] = field(default_factory=dict)

    def _next(self, method: str, url: str) -> _FakeResp:
        matches = [(k, v) for k, v in self.responses.items()
                   if k[0] == method and url.startswith(k[1])]
        matches.sort(key=lambda kv: -len(kv[0][1]))
        for _, queue in matches:
            if queue:
                return queue.pop(0)
        raise AssertionError(f"no fake response queued for {method} {url}")

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ARG002
        self.calls.append(("POST", url, {"json": json}))
        return self._next("POST", url)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(pr_assign, "requests", r)
    monkeypatch.setattr(github_app, "_auth_headers",
                        lambda repo: {"Authorization": "Bearer TEST"})
    return r


ASSIGN_URL = f"https://api.github.com/repos/{REPO}/issues/{PR}/assignees"
REVIEW_URL = f"https://api.github.com/repos/{REPO}/pulls/{PR}/requested_reviewers"


# ── env lookup ──────────────────────────────────────────────────────────────


def test_operator_login_from_env_empty_is_empty(monkeypatch):
    monkeypatch.delenv(pr_assign.ENV_OPERATOR, raising=False)
    assert pr_assign.operator_login_from_env() == ""


def test_operator_login_from_env_strips_whitespace(monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, "  rudi193-cmd  ")
    assert pr_assign.operator_login_from_env() == "rudi193-cmd"


# ── absent when unconfigured ────────────────────────────────────────────────


def test_unset_env_is_absent_not_error(rec: _Recorder, monkeypatch):
    monkeypatch.delenv(pr_assign.ENV_OPERATOR, raising=False)
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "absent"
    assert receipt["action"] == "skipped"
    assert receipt["assigned"] is False
    assert receipt["review_requested"] is False
    assert receipt["operator"] == ""
    assert rec.calls == []  # no HTTP


def test_blank_env_is_treated_as_unset(rec: _Recorder, monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, "   ")
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "absent"
    assert rec.calls == []


def test_explicit_login_overrides_env(rec: _Recorder, monkeypatch):
    """A caller who already knows the login can pass it; env is not
    consulted. Useful for a per-repo override the module does not yet
    read from a config file."""
    monkeypatch.delenv(pr_assign.ENV_OPERATOR, raising=False)
    rec.responses[("POST", ASSIGN_URL)] = [_FakeResp(_payload={"assignees": [{"login": "other"}]})]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=201)]
    receipt = pr_assign.assign_to_operator(REPO, PR, login="other")
    assert receipt["operator"] == "other"
    assert receipt["status"] == "ok"


# ── happy path ──────────────────────────────────────────────────────────────


def test_first_call_assigns_and_requests_review(rec: _Recorder, monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    rec.responses[("POST", ASSIGN_URL)] = [
        _FakeResp(_payload={"assignees": [{"login": OP}]}),
    ]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=201)]

    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "ok"
    assert receipt["action"] == "assigned"
    assert receipt["assigned"] is True
    assert receipt["review_requested"] is True
    posted_assign = next(c for c in rec.calls if c[1] == ASSIGN_URL)
    assert posted_assign[2]["json"] == {"assignees": [OP]}
    posted_review = next(c for c in rec.calls if c[1] == REVIEW_URL)
    assert posted_review[2]["json"] == {"reviewers": [OP]}


# ── partial success ─────────────────────────────────────────────────────────


def test_review_422_is_already_or_refused_not_a_raise(rec: _Recorder, monkeypatch):
    """A repeat call for a reviewer already requested returns 422 with
    an 'already requested' message; the module treats any 422 there as
    'already or refused' so a repeat call is a partial success (assign
    still landed) rather than a full failure."""
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    rec.responses[("POST", ASSIGN_URL)] = [_FakeResp(_payload={"assignees": [{"login": OP}]})]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=422)]
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "partial"
    assert receipt["assigned"] is True
    assert receipt["review_requested"] is False
    assert "already requested or refused" in receipt["detail"]


def test_unreachable_login_is_a_specific_line_not_a_silent_success(rec: _Recorder, monkeypatch):
    """GitHub answers 200 to POST /assignees with a login it does not
    recognise — but the response's `assignees` list does NOT include the
    login. A caller that only checks the HTTP status would see success
    and move on; this module inspects the response body and reports
    'not in returned list — unreachable login?'."""
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, "nonexistent-login")
    rec.responses[("POST", ASSIGN_URL)] = [
        _FakeResp(_payload={"assignees": []}),  # login was silently dropped
    ]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=422)]
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["assigned"] is False
    assert "unreachable login" in receipt["detail"]
    assert receipt["status"] == "could-not-run"


def test_assign_error_does_not_stop_review_from_running(rec: _Recorder, monkeypatch):
    """The two API calls are independent — a network failure on
    /assignees must not prevent /requested_reviewers from running.
    A partial success is the honest outcome here."""
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    rec.responses[("POST", ASSIGN_URL)] = [_FakeResp(_status=500)]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=201)]
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "partial"
    assert receipt["assigned"] is False
    assert receipt["review_requested"] is True
    assert "assign:" in receipt["detail"]


# ── failure modes ───────────────────────────────────────────────────────────


def test_auth_failure_is_a_line(rec: _Recorder, monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    def _boom(_repo):
        raise RuntimeError("no PEM")
    monkeypatch.setattr(github_app, "_auth_headers", _boom)
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "could-not-run"
    assert receipt["assigned"] is False
    assert receipt["review_requested"] is False
    assert "auth: no PEM" in receipt["detail"]
    assert rec.calls == []


def test_review_5xx_is_reported_and_does_not_raise(rec: _Recorder, monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    rec.responses[("POST", ASSIGN_URL)] = [_FakeResp(_payload={"assignees": [{"login": OP}]})]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=500)]
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "partial"
    assert receipt["assigned"] is True
    assert receipt["review_requested"] is False
    assert "review:" in receipt["detail"]


def test_both_endpoints_fail_is_a_could_not_run(rec: _Recorder, monkeypatch):
    monkeypatch.setenv(pr_assign.ENV_OPERATOR, OP)
    rec.responses[("POST", ASSIGN_URL)] = [_FakeResp(_status=500)]
    rec.responses[("POST", REVIEW_URL)] = [_FakeResp(_status=500)]
    receipt = pr_assign.assign_to_operator(REPO, PR)
    assert receipt["status"] == "could-not-run"
    assert "assign:" in receipt["detail"]
    assert "review:" in receipt["detail"]
