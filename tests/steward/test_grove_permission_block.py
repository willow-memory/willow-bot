"""A permission-class Grove refusal is filed once, not retried into a wall.

Companion to test_ci_comment.py's transient-refusal coverage (a "refused"/
502-shaped error keeps retrying with the existing cap). This file covers
the OTHER branch: `sender_forbidden` / a manifest `"gate denied: ..."`
string is terminal for that (head, channel) — one attempt, then `blocked`,
filed once to human_required, surfaced on the heartbeat and `status.py`,
and re-probed only on `ci_comments.BLOCKED_PROBE_TICKS` or a manifest
fingerprint change — never every tick. The MCP client and the PR comment
are fakes; nothing here reaches the network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from willow_bot import deposits
from willow_bot import status as status_mod
from willow_bot.steward import ci_comments, ci_log, heartbeat, tick


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    monkeypatch.delenv(tick._CI_CANCELLED_GRACE_ENV, raising=False)
    monkeypatch.delenv(tick._GROVE_SENDER_ENV, raising=False)
    monkeypatch.delenv("WILLOW_BOT_MCP_APP_ID", raising=False)
    return tmp_path


class _Client:
    def __init__(self, *, grove_error="sender_forbidden"):
        self.calls: list[tuple[str, dict]] = []
        self.grove_error = grove_error
        self.n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if name == "grove_send_message":
            return {"error": self.grove_error}
        return {"ok": True, "id": f"hr-{self.n}"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)
    comments = lambda repo, pr_number, head_sha, body: {  # noqa: E731
        "status": "ok", "action": "created", "comment_id": 1,
        "url": f"https://github.com/{repo}/pull/{pr_number}#c1",
    }
    monkeypatch.setattr("willow_bot.pr_voice.upsert_ci_red_comment", comments)
    monkeypatch.setattr(ci_log, "fetch_job_log", lambda repo, job_id: ("boring log\n", {"status": "ok"}))
    monkeypatch.setattr(ci_log, "extract_failure_block",
                        lambda text: {"block": "BOOM", "trimmed": False, "source": "pytest"})


def _row(repo, sha, cid, name, conclusion, *, pr=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid, check_name=name,
                                     conclusion=conclusion, pr_number=pr)
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


def _prime():
    deposits.append_local(_row("x/y", "0" * 40, 0, "older", "success"))
    assert tick.run_ci(enable_mcp=False)["first_run_skipped_bytes"] > 0


RAT = "willow-memory/ratatosk"
SHA = "632225cbe7980e169929d2ca0cc2d89ea15eedf6"


# ── one attempt, then blocked — never retried like a transient refusal ──────

def test_sender_forbidden_blocks_after_exactly_one_attempt(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["spoke"] == [{"channel": "willow", "grove_sender": "willow-bot",
                           "ok": False, "reason": "sender_forbidden", "blocked": True}]
    sends = c.named("grove_send_message")
    assert len(sends) == 1

    # A second (and third) tick spends no further attempt.
    tick.run_ci()
    tick.run_ci()
    assert len(c.named("grove_send_message")) == 1, "blocked must not be retried like a transient refusal"

    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["spoke"]["status"] == "blocked"
    assert entry["spoke"]["attempts"] == 1
    assert entry["spoke"]["last_error"] == "sender_forbidden"


def test_gate_denied_string_is_also_permission_class(home, monkeypatch):
    _prime()
    c = _Client(grove_error="gate denied: 'willow-bot' not permitted for 'grove_send_message'.")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["spoke"][0]["blocked"] is True
    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["spoke"]["status"] == "blocked"


# ── one human_required item per (sender, channel), not per head ─────────────

def test_ten_reds_from_the_same_identity_file_one_human_required_item(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    for n in range(10):
        deposits.append_local(_row(RAT, "a" * 39 + str(n), n + 1, "test", "failure", pr=48 + n))
    tick.run_ci()
    # human_required_enqueue also files one "CI red: ..." review item per
    # head (a separate, pre-existing concern) — filter to the notifier's
    # OWN item, named by its "Grove refuses" title.
    enq = [i for i in c.named("human_required_enqueue") if i["title"].startswith("Grove refuses")]
    assert len(enq) == 1, [i["title"] for i in c.named("human_required_enqueue")]
    assert enq[0]["kind"] == "review"
    assert "willow-bot" in enq[0]["title"] and "willow" in enq[0]["title"]

    owed, _ = ci_comments.load()
    assert len(ci_comments.blocked_report(owed)) == 10


# ── surfaced where the desk reads: heartbeat + status.py ────────────────────

def test_heartbeat_carries_the_blocked_notifier_problem(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()

    receipt = heartbeat.run_heartbeat(enable_mcp=False)
    assert len(receipt["problems"]) == 1
    problem = receipt["problems"][0]
    assert problem["kind"] == "notifier_blocked"
    assert problem["channel"] == "spoke"
    assert problem["last_error"] == "sender_forbidden"


def test_heartbeat_problems_empty_when_nothing_blocked(home, monkeypatch):
    assert heartbeat.run_heartbeat(enable_mcp=False)["problems"] == []


def test_status_notifier_field_is_three_state(home, monkeypatch):
    assert status_mod.report()["notifier"] == {"status": "empty"}

    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()

    r = status_mod.report()["notifier"]
    assert r["status"] == "populated"
    assert r["blocked"][0]["last_error"] == "sender_forbidden"


def test_status_notifier_field_unreachable_on_a_corrupt_table(home, monkeypatch):
    p = ci_comments.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert status_mod.report()["notifier"]["status"] == "unreachable"


# ── un-block: a manifest change re-probes immediately; otherwise the cadence ─

def test_blocked_reprobes_immediately_on_a_manifest_fingerprint_change(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    assert len(c.named("grove_send_message")) == 1

    # A grant lands: the operator writes/updates this app's manifest.
    c.grove_error = None
    manifest = Path(home) / "mcp_apps" / "willow-bot" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"app_id": "willow-bot", "permissions": ["grove_write"]}),
                        encoding="utf-8")

    r = tick.run_ci()
    assert len(c.named("grove_send_message")) == 2, "a manifest change must re-probe without waiting"
    assert r["spoke"] == [{"channel": "willow", "grove_sender": "willow-bot", "ok": True}]
    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["spoke"]["status"] == "posted"


def test_blocked_reprobes_after_BLOCKED_PROBE_TICKS_with_no_manifest(home, monkeypatch):
    """No manifest file at all (``manifest_fingerprint`` returns ``None``):
    falls back to the periodic cadence — never probed every tick, but
    never forgotten either."""
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    assert len(c.named("grove_send_message")) == 1

    for _ in range(ci_comments.BLOCKED_PROBE_TICKS - 1):
        tick.run_ci()
    assert len(c.named("grove_send_message")) == 1, "must not probe before BLOCKED_PROBE_TICKS"

    tick.run_ci()
    assert len(c.named("grove_send_message")) == 2, "must probe again once BLOCKED_PROBE_TICKS elapse"
