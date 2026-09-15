"""Steward-state labels: reconcile the bot's owned label namespace.

Gap acfd27ae3259 (labels sub-part). Every path here is a unit test —
requests is monkeypatched. The invariants: labels outside the owned
prefix are read but never touched, a repeat call is a no-op on the
network, a call with an out-of-namespace label in desired is refused
before any HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import github_app
from willow_bot import pr_labels


REPO = "willow-memory/willow-mcp"
PR = 42


@dataclass
class _FakeResp:
    _status: int = 200
    _payload: Any = field(default_factory=list)

    def raise_for_status(self) -> None:
        if self._status >= 400 and self._status != 404:
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

    def get(self, url, headers=None, params=None, timeout=None):  # noqa: ARG002
        self.calls.append(("GET", url, {"params": dict(params or {})}))
        return self._next("GET", url)

    def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002, ARG002
        self.calls.append(("POST", url, {"json": json}))
        return self._next("POST", url)

    def delete(self, url, headers=None, timeout=None):  # noqa: ARG002
        self.calls.append(("DELETE", url, {}))
        return self._next("DELETE", url)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(pr_labels, "requests", r)
    monkeypatch.setattr(github_app, "_auth_headers",
                        lambda repo: {"Authorization": "Bearer TEST"})
    return r


def _label(name: str) -> dict:
    return {"name": name, "color": "aaaaaa"}


LABELS_URL = f"https://api.github.com/repos/{REPO}/issues/{PR}/labels"


# ── vocabulary ──────────────────────────────────────────────────────────────


def test_vocabulary_lives_under_the_owned_prefix():
    """A rename of the prefix lands in one place — the constants and the
    default. Test the invariant, not the strings themselves."""
    assert pr_labels.LABEL_CI_RED.startswith(pr_labels.DEFAULT_PREFIX)
    assert pr_labels.LABEL_NEEDS_RATIFICATION.startswith(pr_labels.DEFAULT_PREFIX)
    assert pr_labels.LABEL_AUDIT_DISPATCHED.startswith(pr_labels.DEFAULT_PREFIX)
    assert pr_labels.LABEL_BOT_OPENED.startswith(pr_labels.DEFAULT_PREFIX)


# ── reconcile: additive within namespace ────────────────────────────────────


def test_first_call_adds_desired_and_touches_nothing_else(rec: _Recorder):
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label("bug"), _label("dependencies")]),
    ]
    rec.responses[("POST", LABELS_URL)] = [
        _FakeResp(_status=200,
                  _payload=[_label("bug"), _label("dependencies"),
                            _label(pr_labels.LABEL_CI_RED)]),
    ]
    receipt = pr_labels.reconcile_labels(REPO, PR, {pr_labels.LABEL_CI_RED})
    assert receipt["status"] == "ok"
    assert receipt["action"] == "reconciled"
    assert receipt["added"] == [pr_labels.LABEL_CI_RED]
    assert receipt["removed"] == []
    assert receipt["found_other"] == ["bug", "dependencies"]
    assert not any(c[0] == "DELETE" for c in rec.calls)  # touched no other label


def test_repeat_call_with_same_desired_is_a_no_op(rec: _Recorder):
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label("bug"), _label(pr_labels.LABEL_CI_RED)]),
    ]
    receipt = pr_labels.reconcile_labels(REPO, PR, {pr_labels.LABEL_CI_RED})
    assert receipt["status"] == "ok"
    assert receipt["action"] == "no-op"
    assert receipt["added"] == []
    assert receipt["removed"] == []
    # Only the GET was made.
    assert [c[0] for c in rec.calls] == ["GET"]


def test_moving_between_states_adds_new_and_removes_stale(rec: _Recorder):
    """CI turned green: ci-red goes, audit-dispatched stays. The diff is
    two operations — one POST for the new labels, one DELETE for each
    stale one — so a state transition is a bounded number of writes."""
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label(pr_labels.LABEL_CI_RED),
                            _label(pr_labels.LABEL_AUDIT_DISPATCHED),
                            _label("area/steward")]),
    ]
    rec.responses[("POST", LABELS_URL)] = [
        _FakeResp(_payload=[]),
    ]
    del_url = f"{LABELS_URL}/{pr_labels.LABEL_CI_RED}"
    rec.responses[("DELETE", del_url)] = [_FakeResp(_status=200)]

    receipt = pr_labels.reconcile_labels(
        REPO, PR,
        {pr_labels.LABEL_AUDIT_DISPATCHED, pr_labels.LABEL_NEEDS_RATIFICATION},
    )
    assert receipt["status"] == "ok"
    assert receipt["action"] == "reconciled"
    assert receipt["added"] == [pr_labels.LABEL_NEEDS_RATIFICATION]
    assert receipt["removed"] == [pr_labels.LABEL_CI_RED]
    # `area/steward` is outside the prefix — never in a POST body, never a DELETE.
    posted = next(c for c in rec.calls if c[0] == "POST")
    assert "area/steward" not in posted[2]["json"]["labels"]
    assert not any(c[0] == "DELETE" and "area/steward" in c[1] for c in rec.calls)


def test_labels_outside_prefix_are_reported_but_not_touched(rec: _Recorder):
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label("bug"), _label("area/steward"),
                            _label(pr_labels.LABEL_CI_RED)]),
    ]
    del_url = f"{LABELS_URL}/{pr_labels.LABEL_CI_RED}"
    rec.responses[("DELETE", del_url)] = [_FakeResp(_status=200)]
    receipt = pr_labels.reconcile_labels(REPO, PR, set())  # remove everything owned
    assert receipt["removed"] == [pr_labels.LABEL_CI_RED]
    assert receipt["found_other"] == ["area/steward", "bug"]
    assert receipt["found_owned"] == [pr_labels.LABEL_CI_RED]


# ── refusal before any HTTP ─────────────────────────────────────────────────


def test_desired_outside_prefix_is_refused_before_http(rec: _Recorder):
    receipt = pr_labels.reconcile_labels(REPO, PR, {"blocked"})
    assert receipt["status"] == "could-not-run"
    assert receipt["action"] == "skipped"
    assert "must all start with" in receipt["detail"]
    assert rec.calls == []


def test_empty_string_desired_is_ignored(rec: _Recorder):
    """A caller assembling desired from a set may end up with an empty
    string; that is not a label GitHub accepts, and reconciling it would
    otherwise take a POST for it. Silent drop is the safe read — an
    empty string never lands in the request body."""
    rec.responses[("GET", LABELS_URL)] = [_FakeResp(_payload=[])]
    rec.responses[("POST", LABELS_URL)] = [
        _FakeResp(_payload=[_label(pr_labels.LABEL_CI_RED)]),
    ]
    receipt = pr_labels.reconcile_labels(REPO, PR, {"", pr_labels.LABEL_CI_RED})
    assert receipt["action"] == "reconciled"
    assert receipt["added"] == [pr_labels.LABEL_CI_RED]
    posted = next(c for c in rec.calls if c[0] == "POST")
    assert "" not in posted[2]["json"]["labels"]


# ── failure modes as receipts ───────────────────────────────────────────────


def test_auth_failure_is_a_line(rec: _Recorder, monkeypatch):
    def _boom(_repo):
        raise RuntimeError("no PEM")
    monkeypatch.setattr(github_app, "_auth_headers", _boom)
    receipt = pr_labels.reconcile_labels(REPO, PR, {pr_labels.LABEL_CI_RED})
    assert receipt["status"] == "could-not-run"
    assert "auth" in receipt["detail"]
    assert rec.calls == []


def test_list_failure_is_a_line(rec: _Recorder):
    rec.responses[("GET", LABELS_URL)] = [_FakeResp(_status=500)]
    receipt = pr_labels.reconcile_labels(REPO, PR, {pr_labels.LABEL_CI_RED})
    assert receipt["status"] == "could-not-run"
    assert "list" in receipt["detail"]


def test_add_failure_and_remove_success_both_appear_in_receipt(rec: _Recorder):
    """A partial success is not silent — the caller sees exactly what
    landed and what did not, so a retry can hit only the miss."""
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label(pr_labels.LABEL_CI_RED)]),
    ]
    rec.responses[("POST", LABELS_URL)] = [_FakeResp(_status=422)]
    del_url = f"{LABELS_URL}/{pr_labels.LABEL_CI_RED}"
    rec.responses[("DELETE", del_url)] = [_FakeResp(_status=200)]
    receipt = pr_labels.reconcile_labels(
        REPO, PR,
        {pr_labels.LABEL_AUDIT_DISPATCHED},
    )
    assert receipt["status"] == "could-not-run"
    assert receipt["removed"] == [pr_labels.LABEL_CI_RED]
    assert any(r["op"] == "add" for r in receipt["refused"])


def test_remove_404_is_treated_as_already_gone(rec: _Recorder):
    """A concurrent reconcile (a second webhook that fired in parallel)
    can beat us to a DELETE. A 404 there is not a failure — the label is
    where the reconciler wanted it (absent), so the removal is recorded
    as done."""
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=[_label(pr_labels.LABEL_CI_RED)]),
    ]
    del_url = f"{LABELS_URL}/{pr_labels.LABEL_CI_RED}"
    rec.responses[("DELETE", del_url)] = [_FakeResp(_status=404)]
    receipt = pr_labels.reconcile_labels(REPO, PR, set())
    assert receipt["status"] == "ok"
    assert receipt["removed"] == [pr_labels.LABEL_CI_RED]


def test_pagination_walks_until_short_page(rec: _Recorder):
    page1 = [_label(f"noise-{i}") for i in range(100)]
    page2 = [_label(pr_labels.LABEL_CI_RED)]
    rec.responses[("GET", LABELS_URL)] = [
        _FakeResp(_payload=page1), _FakeResp(_payload=page2),
    ]
    receipt = pr_labels.reconcile_labels(REPO, PR, {pr_labels.LABEL_CI_RED})
    assert receipt["action"] == "no-op"
    assert pr_labels.LABEL_CI_RED in receipt["found_owned"]


def test_get_pagination_params_are_correct(rec: _Recorder):
    rec.responses[("GET", LABELS_URL)] = [_FakeResp(_payload=[])]
    pr_labels.reconcile_labels(REPO, PR, set())
    got = next(c for c in rec.calls if c[0] == "GET")
    assert got[2]["params"] == {"per_page": 100, "page": 1}
