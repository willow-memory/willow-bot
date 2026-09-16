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

    def __call__(self, repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX):
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

    def _fake(repo, pr_number, desired, *, owned_prefix=pr_labels.DEFAULT_PREFIX):
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
        lambda repo, pr, desired, owned_prefix=pr_labels.DEFAULT_PREFIX:
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
    body = voice._status_comment_body("o/r#1", "aaa111", state, at="2026-09-16T00:00:00Z")
    assert "dispatched" in body and "not dispatched" not in body
    assert "1 red leg(s) filed" in body
    assert "2026-09-16T00:00:00Z" in body


def test_status_comment_body_reports_absence_honestly():
    body = voice._status_comment_body("o/r#1", "aaa111", {}, at="2026-09-16T00:00:00Z")
    assert "not dispatched" in body
    assert "none filed" in body


# ── run_voice: comment upsert idempotency ────────────────────────────────


def test_run_voice_upserts_one_comment_per_head_sha(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert
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
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert
):
    """Two ticks against the same head_sha update the same comment —
    `pr_voice`'s own fake reports `updated` the second time, and this
    step's receipt reflects that, not a second `created`."""
    state = {"open": ["o/r#1"], "webhook_signals": [_pr_signal("o/r#1", "aaa111")]}
    voice.run_voice(dict(state))
    receipt = voice.run_voice(dict(state))
    assert len(fake_upsert.calls) == 2
    assert receipt["voiced"] == [{"repo_pr": "o/r#1", "head_sha": "aaa111", "action": "updated"}]


def test_run_voice_new_sha_opens_a_second_comment(
    home: Path, fake_reconcile: _RecordReconcile, fake_upsert: _RecordUpsert
):
    """A force-push's new head_sha is a fresh comment, not a rewrite of
    the old one — the old comment is left as-is (this step never deletes
    or edits a comment keyed on a different sha)."""
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
    home: Path, fake_reconcile: _RecordReconcile, monkeypatch
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
