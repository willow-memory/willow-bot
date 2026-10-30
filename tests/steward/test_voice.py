"""Steward voice step — reconcile owned-prefix labels per tracked PR.

Closes gap acfd27ae3259 (caller side of voice sub-part). The label
primitive already has its own unit tests; this file exercises the state
→ desired-labels mapping inside `run_voice`, and confirms that the step
calls the primitive with the right shape for each open PR.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from willow_bot import pr_labels, pr_voice
from willow_bot.steward import voice


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    return tmp_path


class _RecordReconcile:
    """Records each `reconcile_labels` call and returns a canned result."""

    def __init__(self, *, result=None):
        self.calls: list[tuple[str, int, set[str]]] = []
        self.result = result or {"status": "ok", "action": "no-op",
                                 "added": [], "removed": [], "found_owned": []}

    def __call__(self, repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=()):
        self.calls.append((repo, pr_number, set(desired)))
        return dict(self.result)


@pytest.fixture
def fake_reconcile(monkeypatch):
    r = _RecordReconcile()
    monkeypatch.setattr(pr_labels, "reconcile_labels", r)
    return r


class _RecordUpsert:
    """Records each `upsert_status_comment` call and simulates a real
    comment store keyed on (repo, pr, head_sha) → comment_id, so a second
    call for the same sha reports `updated` and a new sha reports
    `created` — the same idempotency contract `pr_voice` itself tests."""

    def __init__(self):
        self.calls: list[tuple[str, int, str, str]] = []
        self._by_sha: dict[tuple[str, int, str], int] = {}
        self._next_id = 1

    def __call__(self, repo, pr_number, head_sha, body):
        self.calls.append((repo, pr_number, head_sha, body))
        if not head_sha:
            return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
        key = (repo, pr_number, head_sha)
        if key in self._by_sha:
            return {"status": "ok", "action": "updated", "comment_id": self._by_sha[key]}
        self._by_sha[key] = self._next_id
        self._next_id += 1
        return {"status": "ok", "action": "created", "comment_id": self._by_sha[key]}


@pytest.fixture
def fake_upsert(monkeypatch):
    u = _RecordUpsert()
    monkeypatch.setattr(pr_voice, "upsert_status_comment", u)
    return u


class _RecordPublishCheck:
    """Records each `publish_check` call and simulates a real check-run
    store keyed on (repo, head_sha, name) → check_run_id, so a second
    call for the same sha reports `updated` and a new sha reports
    `created` — the same idempotency contract `pr_voice` itself tests."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None, dict | None]] = []
        self._by_sha: dict[tuple[str, str, str], int] = {}
        self._next_id = 1

    def __call__(self, repo, head_sha, name, *, status="completed",
                 conclusion=None, output=None, external_id=None):
        self.calls.append((repo, head_sha, name, conclusion, output))
        if not head_sha:
            return {"status": "could-not-run", "detail": "no head_sha", "action": "skipped"}
        key = (repo, head_sha, name)
        if key in self._by_sha:
            return {"status": "ok", "action": "updated", "check_run_id": self._by_sha[key]}
        self._by_sha[key] = self._next_id
        self._next_id += 1
        return {"status": "ok", "action": "created", "check_run_id": self._by_sha[key]}


@pytest.fixture
def fake_publish_check(monkeypatch):
    p = _RecordPublishCheck()
    monkeypatch.setattr(pr_voice, "publish_check", p)
    return p


def _pr_signal(repo_pr: str, head_sha: str) -> dict:
    return {"repo_pr": repo_pr, "action": "synchronize", "title": "", "url": "",
            "received_at": "2026-09-16T00:00:00Z", "work_id": f"wid-{head_sha}",
            "merged": False, "pr_state": "open", "source": "willow-bot",
            "head_sha": head_sha}


# ── parse_key: the boring cases first ────────────────────────────────────


def test_parse_key_returns_repo_and_num():
    assert voice._parse_key("owner/repo#42") == ("owner/repo", 42)


def test_parse_key_returns_none_for_garbled():
    assert voice._parse_key(None) is None
    assert voice._parse_key("") is None
    assert voice._parse_key("no-hash") is None
    assert voice._parse_key("owner/repo#abc") is None
    assert voice._parse_key("#123") is None


# ── desired-labels: audit_dispatched → audit-dispatched ─────────────────


def test_audit_dispatched_maps_to_audit_dispatched_label():
    state = {"audit_dispatched": {"owner/repo#7": "D-abc"}}
    got = voice._desired_labels_by_pr(state)
    assert got == {"owner/repo#7": {pr_labels.LABEL_AUDIT_DISPATCHED}}


def test_garbled_audit_key_is_dropped():
    state = {"audit_dispatched": {"not-a-key": "D-abc", "owner/repo#7": "D-def"}}
    got = voice._desired_labels_by_pr(state)
    assert set(got.keys()) == {"owner/repo#7"}


def test_empty_state_yields_empty_mapping():
    assert voice._desired_labels_by_pr({}) == {}


# ── desired-labels: ci_filed (joined through latest head_sha) → ci-red ──


def test_ci_filed_for_latest_head_sha_maps_to_ci_red_label():
    state = {
        "webhook_signals": [_pr_signal("owner/repo#7", "aaa111")],
        "ci_filed": {"aaa111:1": "H-abc"},
    }
    got = voice._desired_labels_by_pr(state)
    assert got == {"owner/repo#7": {pr_labels.LABEL_CI_RED}}


def test_ci_filed_for_a_stale_head_sha_does_not_map():
    """A force-push moved the PR's head on; a red leg filed against the
    old sha is not this PR's current state."""
    state = {
        "webhook_signals": [_pr_signal("owner/repo#7", "bbb222")],
        "ci_filed": {"aaa111:1": "H-abc"},
    }
    got = voice._desired_labels_by_pr(state)
    assert got == {}


def test_audit_dispatched_and_ci_red_both_apply_to_the_same_pr():
    state = {
        "audit_dispatched": {"owner/repo#7": "D-abc"},
        "webhook_signals": [_pr_signal("owner/repo#7", "aaa111")],
        "ci_filed": {"aaa111:1": "H-abc"},
    }
    got = voice._desired_labels_by_pr(state)
    assert got == {
        "owner/repo#7": {pr_labels.LABEL_AUDIT_DISPATCHED, pr_labels.LABEL_CI_RED},
    }


def test_run_voice_drops_ci_red_once_no_longer_filed(home: Path, fake_reconcile: _RecordReconcile):
    """A PR that was red converges to the empty owned set (minus whatever
    else applies) once its ci_filed rows are gone — reconcile_labels does
    the actual removal, but this step must stop asking for the label."""
    state = {
        "open": ["owner/repo#7"],
        "webhook_signals": [_pr_signal("owner/repo#7", "aaa111")],
    }
    voice.run_voice(state)
    call_map = {(repo, num): desired for repo, num, desired in fake_reconcile.calls}
    assert call_map[("owner/repo", 7)] == set()


# ── run_voice: iterates open, reconciles each ───────────────────────────


def test_run_voice_reconciles_each_open_pr(home: Path, fake_reconcile: _RecordReconcile):
    state = {
        "open": ["owner/repo#1", "owner/repo#2"],
        "audit_dispatched": {"owner/repo#1": "D-abc"},
    }
    receipt = voice.run_voice(state)
    assert receipt["status"] == "ok"
    assert receipt["open"] == 2

    # PR #1 should get the audit-dispatched label; PR #2 gets empty (stale
    # owned labels would be removed if any were there).
    call_map = {(repo, num): desired for repo, num, desired in fake_reconcile.calls}
    assert call_map[("owner/repo", 1)] == {pr_labels.LABEL_AUDIT_DISPATCHED}
    assert call_map[("owner/repo", 2)] == set()


def test_run_voice_partial_when_some_reconciles_fail(home: Path, monkeypatch):
    """A per-PR failure surfaces in `refused` while successful reconciles
    still land — the receipt's `status` reflects the mix."""
    outcomes = [
        {"status": "ok", "action": "reconciled", "added": [pr_labels.LABEL_AUDIT_DISPATCHED],
         "removed": []},
        {"status": "could-not-run", "detail": "auth: no PEM",
         "refused": [{"op": "add", "labels": pr_labels.LABEL_AUDIT_DISPATCHED, "error": "auth"}]},
    ]

    def _fake(repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=()):
        return outcomes.pop(0)

    monkeypatch.setattr(pr_labels, "reconcile_labels", _fake)
    state = {
        "open": ["owner/repo#1", "owner/repo#2"],
        "audit_dispatched": {"owner/repo#1": "D1", "owner/repo#2": "D2"},
    }
    receipt = voice.run_voice(state)
    assert receipt["status"] == "partial"
    assert len(receipt["reconciled"]) == 1
    assert len(receipt["refused"]) == 1
    assert receipt["refused"][0]["repo_pr"] == "owner/repo#2"


def test_run_voice_could_not_run_when_all_fail(home: Path, monkeypatch):
    monkeypatch.setattr(
        pr_labels, "reconcile_labels",
        lambda repo, pr, desired, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=():
            {"status": "could-not-run", "detail": "auth: no PEM"},
    )
    state = {"open": ["owner/repo#1"], "audit_dispatched": {"owner/repo#1": "D1"}}
    receipt = voice.run_voice(state)
    assert receipt["status"] == "could-not-run"
    assert receipt["reconciled"] == []


def test_run_voice_skips_garbled_open_keys(home: Path, fake_reconcile: _RecordReconcile):
    state = {"open": ["good/repo#1", "bad-key"], "audit_dispatched": {}}
    receipt = voice.run_voice(state)
    assert len(receipt["skipped"]) == 1
    assert receipt["skipped"][0]["repo_pr"] == "bad-key"
    # The good one was still reconciled.
    assert (("good/repo", 1, set()) in fake_reconcile.calls)


def test_run_voice_empty_open_is_ok(home: Path, fake_reconcile: _RecordReconcile):
    receipt = voice.run_voice({"open": []})
    assert receipt["status"] == "ok"
    assert receipt["open"] == 0
    assert fake_reconcile.calls == []


def test_run_voice_appends_to_steward_ticks_jsonl(home: Path, fake_reconcile: _RecordReconcile):
    """The step writes its receipt to the same jsonl every other step
    uses, so the seat's status surface picks it up in `journal`."""
    voice.run_voice({"open": ["o/r#1"], "audit_dispatched": {}})
    path = home / "willow-bot" / "steward_ticks.jsonl"
    assert path.is_file()
    line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
    import json as _json
    rec = _json.loads(line)
    assert rec["event"] == "steward_voice"
    assert rec["status"] == "ok"


def test_run_voice_reads_state_from_file_when_none_passed(home: Path, monkeypatch,
                                                          fake_reconcile: _RecordReconcile):
    """A caller that does not pass in state reads from the tick's state
    file. This is the shape the `willow-bot-steward voice` CLI uses."""
    import json as _json
    from willow_bot.steward.config import state_path

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(
        {"open": ["o/r#5"], "audit_dispatched": {"o/r#5": "D5"}}
    ))
    receipt = voice.run_voice()
    assert receipt["status"] == "ok"
    assert receipt["open"] == 1
    assert fake_reconcile.calls == [("o/r", 5, {pr_labels.LABEL_AUDIT_DISPATCHED})]


# ── voice: latest head_sha lookup + status comment body ─────────────────


def test_latest_head_sha_by_pr_reads_webhook_pr_signals():
    state = {"webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    assert voice._latest_head_sha_by_pr(state) == {"o/r#1": "aaa111"}


def test_latest_head_sha_by_pr_last_wins_on_force_push():
    state = {"webhook_signals": [_pr_signal("o/r#1", "aaa111"), _pr_signal("o/r#1", "bbb222")]}
    assert voice._latest_head_sha_by_pr(state) == {"o/r#1": "bbb222"}


def test_latest_head_sha_by_pr_skips_check_run_signals():
    state = {"webhook_signals": [
        {"kind": "check_run", "repo_pr": "o/r#1", "head_sha": "should-not-count"},
    ]}
    assert voice._latest_head_sha_by_pr(state) == {}


def test_latest_head_sha_by_pr_ignores_signal_without_head_sha():
    sig = _pr_signal("o/r#1", "")
    sig["head_sha"] = ""
    state = {"webhook_signals": [sig]}
    assert voice._latest_head_sha_by_pr(state) == {}


def test_ci_red_legs_for_sha_filters_by_prefix():
    state = {"ci_filed": {"aaa111:1": "id-1", "aaa111:2": "id-2", "bbb222:1": "id-3"}}
    assert voice._ci_red_legs_for_sha(state, "aaa111") == ["aaa111:1", "aaa111:2"]
    assert voice._ci_red_legs_for_sha(state, "ccc333") == []


def test_status_comment_body_names_audit_and_ci_state():
    state = {"audit_dispatched": {"o/r#1": "D1"}, "ci_filed": {"aaa111:1": "id"}}
    body = voice._status_comment_body("o/r#1", "aaa111", state)
    assert "dispatched" in body and "not dispatched" not in body
    assert "1 red leg(s) filed" in body
    assert "tick" not in body  # no clock: the body changes only when the view does


def test_status_comment_body_reports_absence_honestly():
    body = voice._status_comment_body("o/r#1", "aaa111", {})
    assert "not dispatched" in body
    assert "none filed" in body


# ── run_voice: comment upsert idempotency ────────────────────────────────


def test_run_voice_upserts_one_comment_per_head_sha(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    """A PR whose latest webhook_pr signal names a head_sha gets one
    status comment; a PR with no signal yet gets none."""
    state = {
        "open": ["o/r#1", "o/r#2"],
        "webhook_signals": [_pr_signal("o/r#1", "aaa111")],
    }
    receipt = voice.run_voice(state)
    assert receipt["status"] == "ok"
    assert len(fake_upsert.calls) == 1
    repo, pr_num, sha, _body = fake_upsert.calls[0]
    assert (repo, pr_num, sha) == ("o/r", 1, "aaa111")
    assert receipt["voiced"] == [{"repo_pr": "o/r#1", "head_sha": "aaa111", "action": "created"}]


def test_run_voice_same_sha_twice_updates_one_comment(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    """A second tick with nothing new writes nothing: no edit, no event."""
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    receipt = voice.run_voice(dict(state))
    assert len(fake_upsert.calls) == 1
    assert receipt["voiced"] == [{"repo_pr": "o/r#1", "head_sha": "aaa111", "action": "unchanged"}]


def test_run_voice_rewrites_when_the_view_changes(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    receipt = voice.run_voice({**state, "audit_dispatched": {"o/r#1": "D1"}})
    assert len(fake_upsert.calls) == 2
    assert receipt["voiced"][0]["action"] == "updated"


def test_run_voice_rechecks_an_unchanged_comment_daily(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck, monkeypatch,
):
    """A human may have deleted the comment: once a day it is written anyway."""
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    real = voice.time.time
    monkeypatch.setattr(voice.time, "time", lambda: real() + voice._RECHECK_S + 1)
    voice.run_voice(dict(state))
    assert len(fake_upsert.calls) == 2 and len(fake_publish_check.calls) == 2


def test_run_voice_refused_comment_is_retried_next_tick(
    home: Path, fake_reconcile: _RecordReconcile, fake_publish_check: _RecordPublishCheck,
    monkeypatch,
):
    calls = []

    def _refuse(repo, pr_number, head_sha, body):
        calls.append(head_sha)
        return {"status": "could-not-run", "detail": "403"}

    monkeypatch.setattr(pr_voice, "upsert_status_comment", _refuse)
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    voice.run_voice(dict(state))
    assert len(calls) == 2


def test_run_voice_new_sha_opens_a_second_comment(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    """A force-push's new head_sha is written (the step hands `pr_voice`
    the new head; `pr_voice` keeps one comment per PR and edits it)."""
    state1 = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(state1)
    state2 = {"open": ["o/r#1"], "webhook_signals": [
        _pr_signal("o/r#1", "aaa111"), _pr_signal("o/r#1", "bbb222"),
    ]}
    receipt = voice.run_voice(state2)
    assert receipt["voiced"] == [{"repo_pr": "o/r#1", "head_sha": "bbb222", "action": "created"}]
    shas_called = [c[2] for c in fake_upsert.calls]
    assert shas_called == ["aaa111", "bbb222"]


def test_run_voice_no_signal_yet_voices_nothing(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert
):
    receipt = voice.run_voice({"open": ["o/r#1"]})
    assert receipt["voiced"] == []
    assert fake_upsert.calls == []


def test_run_voice_voice_refusal_marks_status_partial(
    home: Path, fake_reconcile: _RecordReconcile, fake_publish_check: _RecordPublishCheck,
    monkeypatch,
):
    monkeypatch.setattr(
        pr_voice, "upsert_status_comment",
        lambda repo, pr_number, head_sha, body: {"status": "could-not-run", "detail": "auth: no PEM"},
    )
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    receipt = voice.run_voice(state)
    assert receipt["status"] == "partial"  # labels reconciled ok; the comment did not
    assert receipt["voice_refused"] == [
        {"repo_pr": "o/r#1", "head_sha": "aaa111", "detail": "auth: no PEM"}
    ]


# ── run_voice: check-run publish, one per head sha ───────────────────────


def test_run_voice_publishes_one_check_per_head_sha(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    receipt = voice.run_voice(state)
    assert len(fake_publish_check.calls) == 1
    repo, sha, name, conclusion, _output = fake_publish_check.calls[0]
    assert (repo, sha, name) == ("o/r", "aaa111", pr_voice.CHECK_NAME)
    assert receipt["checked"] == [
        {"repo_pr": "o/r#1", "head_sha": "aaa111", "action": "created", "conclusion": conclusion}
    ]


def test_run_voice_same_sha_twice_updates_one_check(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    """Idempotent per head sha: a tick with nothing new republishes nothing."""
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    receipt = voice.run_voice(dict(state))
    assert len(fake_publish_check.calls) == 1
    assert receipt["checked"][0]["action"] == "unchanged"


def test_run_voice_new_sha_opens_a_second_check(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state1 = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(state1)
    state2 = {"open": ["o/r#1"], "webhook_signals": [
        _pr_signal("o/r#1", "aaa111"), _pr_signal("o/r#1", "bbb222"),
    ]}
    receipt = voice.run_voice(state2)
    shas_called = [c[1] for c in fake_publish_check.calls]
    assert shas_called == ["aaa111", "bbb222"]
    assert receipt["checked"][0]["action"] == "created"


def test_run_voice_check_conclusion_failure_when_ci_red_filed(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state = {
        "open": ["o/r#1"],
        "webhook_signals": [_pr_signal("o/r#1", "aaa111")],
        "ci_filed": {"aaa111:1": "H-abc"},
    }
    receipt = voice.run_voice(state)
    assert receipt["checked"][0]["conclusion"] == "failure"
    assert fake_publish_check.calls[0][3] == "failure"


def test_run_voice_check_conclusion_success_when_audit_dispatched_and_clean(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state = {
        "open": ["o/r#1"],
        "webhook_signals": [_pr_signal("o/r#1", "aaa111")],
        "audit_dispatched": {"o/r#1": "D-1"},
    }
    receipt = voice.run_voice(state)
    assert receipt["checked"][0]["conclusion"] == "success"


def test_run_voice_check_conclusion_success_when_audit_not_required(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    """No CI red and the PR was never queued for audit (`pending_audit`
    does not name it) — nothing is outstanding, so the view is green."""
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    receipt = voice.run_voice(state)
    assert receipt["checked"][0]["conclusion"] == "success"


def test_run_voice_check_conclusion_neutral_when_audit_pending(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    fake_publish_check: _RecordPublishCheck,
):
    state = {
        "open": ["o/r#1"],
        "webhook_signals": [_pr_signal("o/r#1", "aaa111")],
        "pending_audit": [{"repo_pr": "o/r#1", "title": "t", "url": "u"}],
    }
    receipt = voice.run_voice(state)
    assert receipt["checked"][0]["conclusion"] == "neutral"


def test_run_voice_check_refusal_marks_status_partial(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    monkeypatch,
):
    monkeypatch.setattr(
        pr_voice, "publish_check",
        lambda repo, head_sha, name, **kw: {"status": "could-not-run", "detail": "auth: no PEM"},
    )
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    receipt = voice.run_voice(state)
    assert receipt["status"] == "partial"  # the comment landed; the check did not
    assert receipt["check_refused"] == [
        {"repo_pr": "o/r#1", "head_sha": "aaa111", "detail": "auth: no PEM"}
    ]



def test_run_voice_check_skipped_for_a_read_only_grant_is_not_a_refusal(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert,
    monkeypatch,
):
    monkeypatch.setattr(
        pr_voice, "publish_check",
        lambda repo, head_sha, name, **kw: {"status": "skipped", "missing_permission": "checks:write",
                                            "detail": "installation grants checks:read, not write"},
    )
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    receipt = voice.run_voice(state)
    assert receipt["status"] == "ok"
    assert receipt["check_refused"] == []
    assert receipt["check_skipped"] == [
        {"repo_pr": "o/r#1", "head_sha": "aaa111", "detail": "installation grants checks:read, not write"}
    ]

# ── _check_conclusion / _audit_state_ok directly ─────────────────────────


def test_audit_state_ok_true_when_dispatched():
    state = {"audit_dispatched": {"o/r#1": "D-1"}}
    assert voice._audit_state_ok("o/r#1", state) is True


def test_audit_state_ok_true_when_never_queued():
    assert voice._audit_state_ok("o/r#1", {}) is True


def test_audit_state_ok_false_when_pending_and_not_dispatched():
    state = {"pending_audit": [{"repo_pr": "o/r#1", "title": "t", "url": "u"}]}
    assert voice._audit_state_ok("o/r#1", state) is False


# ── a PR's head and red state come from durable, current state ──────────────


def _check_signal(i: int) -> dict:
    return {"kind": "check_run", "repo": "o/busy", "check_run_id": i, "head_sha": f"c{i}"}


def test_a_quiet_prs_head_survives_a_flood_of_check_signals(tmp_path, monkeypatch):
    """The signals window holds check-run signals too and rolls at 100, so a
    quiet PR's opened signal fell out of it; the inbox step's durable
    `pr_heads` keeps it, and the voice step reads it."""
    import json

    from willow_bot.steward.inbox import ingest

    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("WILLOW_HOME", str(home))
    state = tmp_path / "state.json"
    (inbox / "pr.json").write_text(json.dumps({
        "kind": "pull_request", "work_id": "wh-quiet-pr", "repo": "o/quiet", "number": 3,
        "action": "opened", "head_sha": "q" * 40, "received_at": "2026-10-11T00:00:00Z"}))
    ingest(state)
    for i in range(150):
        (inbox / f"c{i}.json").write_text(json.dumps({
            "kind": "check_run", "work_id": f"wh-check-{i}", "repo": "o/busy", "check_id": i,
            "conclusion": "success", "head_sha": f"c{i}"}))
    ingest(state)
    saved = json.loads(state.read_text())
    assert not any(s.get("repo_pr") == "o/quiet#3" for s in saved["webhook_signals"])  # rolled out
    assert voice._latest_head_sha_by_pr(saved)["o/quiet#3"] == "q" * 40


def test_a_green_rerun_on_the_same_head_clears_ci_red():
    """`ci_filed` only grows, so the label and check stayed red after a
    re-run went green on the same commit; the head's current legs decide."""
    sha = "a" * 40
    state = {
        "pr_heads": {"o/r#1": {"head_sha": sha, "at": "t"}},
        "ci_filed": {f"{sha}:11": "item-1"},
        "ci_head_legs": {f"o/r#1@{sha}": {"Tests": "failure"}},
    }
    assert pr_labels.LABEL_CI_RED in voice._desired_labels_by_pr(state).get("o/r#1", set())
    assert voice._check_conclusion("o/r#1", sha, state) == "failure"
    state["ci_head_legs"][f"o/r#1@{sha}"]["Tests"] = "success"  # the re-run went green
    assert pr_labels.LABEL_CI_RED not in voice._desired_labels_by_pr(state).get("o/r#1", set())
    assert voice._check_conclusion("o/r#1", sha, state) != "failure"
    assert "green since" in voice._status_comment_body("o/r#1", sha, state)


def test_without_current_legs_the_filed_record_still_says_red():
    sha = "b" * 40
    state = {"pr_heads": {"o/r#2": {"head_sha": sha, "at": "t"}}, "ci_filed": {f"{sha}:12": "item"}}
    assert pr_labels.LABEL_CI_RED in voice._desired_labels_by_pr(state)["o/r#2"]


# ── hygiene labels: size, conflict, stale, title ─────────────────────────


def _pull(**over):
    pull = {"additions": 3, "deletions": 2, "mergeable": True, "mergeable_state": "clean",
            "title": "feat: a thing", "head": {"sha": "h" * 40}}
    pull.update(over)
    return pull


@pytest.fixture
def live(monkeypatch):
    pulls: dict[tuple[str, int], dict] = {}
    monkeypatch.setattr(voice, "_fetch_pull", lambda repo, pr: pulls[(repo, pr)])
    return pulls


def _labels_for(fake_reconcile, pr_num=1):
    return next(c[2] for c in fake_reconcile.calls if c[1] == pr_num)


@pytest.mark.parametrize("changed,label", [(0, "xs"), (9, "xs"), (10, "s"), (49, "s"),
                                           (50, "m"), (249, "m"), (250, "l"), (999, "l"), (1000, "xl")])
def test_size_buckets(changed, label):
    assert voice.size_label(changed) == f"willow-bot/size/{label}"


def test_run_voice_adds_size_and_conflict(home, fake_reconcile, live):
    live[("o/r", 1)] = _pull(additions=200, deletions=100, mergeable=False, mergeable_state="dirty")
    voice.run_voice({"open": ["o/r#1"]})
    assert _labels_for(fake_reconcile) >= {"willow-bot/size/l", voice.CONFLICT_LABEL}


def test_unknown_mergeability_keeps_the_conflict_label(home, monkeypatch, live):
    seen = {}

    def _rec(repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=()):
        seen.update(desired=set(desired), keep=set(keep))
        return {"status": "ok", "action": "no-op"}

    monkeypatch.setattr(pr_labels, "reconcile_labels", _rec)
    live[("o/r", 1)] = _pull(mergeable=None, mergeable_state="unknown")
    voice.run_voice({"open": ["o/r#1"]})
    assert voice.CONFLICT_LABEL not in seen["desired"] and voice.CONFLICT_LABEL in seen["keep"]


def test_an_unreadable_pr_keeps_every_hygiene_label(home, monkeypatch):
    seen = {}

    def _rec(repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=()):
        seen.update(keep=set(keep))
        return {"status": "ok", "action": "no-op"}

    monkeypatch.setattr(pr_labels, "reconcile_labels", _rec)
    receipt = voice.run_voice({"open": ["o/r#1"]})  # conftest: no GitHub
    assert seen["keep"] == set(voice.HYGIENE_PREFIXES)
    assert receipt["unread"][0]["repo_pr"] == "o/r#1"


def test_stale_from_the_push_record_not_updated_at(home, fake_reconcile, live):
    sha = "h" * 40
    live[("o/r", 1)] = _pull(updated_at="2099-01-01T00:00:00Z")
    voice.run_voice({"open": ["o/r#1"], "pr_heads": {"o/r#1": {"head_sha": sha, "at": "2020-01-01T00:00:00Z"}}})
    assert voice.STALE_LABEL in _labels_for(fake_reconcile)


def test_a_fresh_head_is_not_stale(home, fake_reconcile, live):
    sha = "h" * 40
    now = voice.datetime.now(voice.timezone.utc).isoformat()
    live[("o/r", 1)] = _pull()
    voice.run_voice({"open": ["o/r#1"], "pr_heads": {"o/r#1": {"head_sha": sha, "at": now}}})
    assert voice.STALE_LABEL not in _labels_for(fake_reconcile)


def test_stale_falls_back_to_the_commit_date_read_once(home, fake_reconcile, live, monkeypatch):
    reads = []
    monkeypatch.setattr(voice, "_fetch_commit_date",
                        lambda repo, sha: reads.append(sha) or "2020-01-01T00:00:00Z")
    live[("o/r", 1)] = _pull()
    voice.run_voice({"open": ["o/r#1"]})
    voice.run_voice({"open": ["o/r#1"]})
    assert reads == ["h" * 40]  # cached per sha across ticks
    assert voice.STALE_LABEL in _labels_for(fake_reconcile)


def test_titles_are_checked_only_when_the_profile_asks(home, fake_reconcile, live, monkeypatch, tmp_path):
    import json as _json

    from willow_bot import operator_profile

    live[("o/r", 1)] = _pull(title="Update stuff")
    voice.run_voice({"open": ["o/r#1"]})
    assert voice.TITLE_LABEL not in _labels_for(fake_reconcile)

    prof = tmp_path / "operator.json"
    prof.write_text(_json.dumps({"pr_hygiene": {"conventional_titles": True, "size": False}}))
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(prof))
    operator_profile.reset()
    try:
        fake_reconcile.calls.clear()
        voice.run_voice({"open": ["o/r#1"]})
        labels = _labels_for(fake_reconcile)
        assert voice.TITLE_LABEL in labels
        assert not any(lbl.startswith(voice.SIZE_PREFIX) for lbl in labels)
    finally:
        operator_profile.reset()


@pytest.mark.parametrize("title,ok", [("feat: x", True), ("fix(api)!: y", True),
                                      ("chore(deps): bump", True), ("Fix: y", False),
                                      ("feat:x", False), ("WIP", False)])
def test_conventional_titles(title, ok):
    assert bool(voice._CONVENTIONAL.match(title)) is ok


# ── area labels: the files a PR changes ───────────────────────────────────


@pytest.fixture
def areas(monkeypatch, tmp_path):
    import json as _json

    from willow_bot import operator_profile

    prof = tmp_path / "operator.json"
    prof.write_text(_json.dumps({"pr_hygiene": {"paths": {
        "language": ["adapters/stdlib/words-*"], "demo": ["demo/*"], "Bad Name": ["x"]}}}))
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(prof))
    operator_profile.reset()
    files: dict[tuple[str, int], list[str]] = {}
    reads = []

    def _fetch(repo, pr):
        reads.append((repo, pr))
        if (repo, pr) not in files:
            raise RuntimeError("unreadable")
        return files[(repo, pr)]

    monkeypatch.setattr(voice, "_fetch_files", _fetch)
    yield files, reads
    operator_profile.reset()


def test_an_area_label_comes_from_the_files_changed(home, fake_reconcile, live, areas):
    files, reads = areas
    live[("o/r", 1)] = _pull()
    files[("o/r", 1)] = ["adapters/stdlib/words-tlh/words_tlh.py", "CHANGELOG.md"]
    voice.run_voice({"open": ["o/r#1"]})
    got = _labels_for(fake_reconcile)
    assert voice.AREA_PREFIX + "language" in got and voice.AREA_PREFIX + "demo" not in got
    assert not any("bad" in label.lower() for label in got)
    voice.run_voice({"open": ["o/r#1"]})
    assert reads == [("o/r", 1)]  # read once per head


def test_unreadable_files_keep_the_area_labels(home, monkeypatch, live, areas):
    seen = {}

    def _rec(repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX, keep=()):
        seen.update(desired=set(desired), keep=set(keep))
        return {"status": "ok", "action": "no-op"}

    monkeypatch.setattr(pr_labels, "reconcile_labels", _rec)
    live[("o/r", 1)] = _pull()
    voice.run_voice({"open": ["o/r#1"]})
    assert voice.AREA_PREFIX in seen["keep"]
    assert not any(label.startswith(voice.AREA_PREFIX) for label in seen["desired"])


def test_no_paths_means_no_file_read(home, fake_reconcile, live, monkeypatch):
    monkeypatch.setattr(voice, "_fetch_files", lambda repo, pr: pytest.fail("read the files"))
    live[("o/r", 1)] = _pull()
    voice.run_voice({"open": ["o/r#1"]})
    assert not any(label.startswith(voice.AREA_PREFIX) for label in _labels_for(fake_reconcile))
