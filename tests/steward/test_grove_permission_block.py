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
    # This file tests the notifier mechanism itself (blocking, dedup,
    # reprobe), not the app_id default (that is test_config_app_id.py's
    # job, Loki 738DB24E F1) — set the identity explicitly to the
    # post-4326FDFE shape rather than relying on config.py's default,
    # which now stays "willow" until an operator's unit says otherwise.
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "willow-bot")
    return tmp_path


class _Client:
    def __init__(self, *, grove_error="sender_forbidden", enqueue_error=None):
        self.calls: list[tuple[str, dict]] = []
        self.grove_error = grove_error
        self.enqueue_error = enqueue_error
        self.n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if name == "grove_send_message":
            return {"error": self.grove_error}
        if name == "human_required_enqueue" and self.enqueue_error:
            return {"error": self.enqueue_error}
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


# ── F2: a disagreeing sender override is a refusal, never a substitution ────

def test_sender_mismatch_refuses_without_ever_sending(home, monkeypatch):
    """Loki 738DB24E F2: a WILLOW_BOT_GROVE_SENDER that disagrees with the
    resolved app_id must refuse outright — no grove_send_message call is
    made at all, never a substituted send under the app_id's name (the
    earlier cut of this fix substituted silently and sent, which under the
    interim box config posts as the human trust-root seat)."""
    _prime()
    monkeypatch.setenv(tick._GROVE_SENDER_ENV, "someone-else")
    c = _Client(grove_error="sender_forbidden")  # must never be reached
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()

    assert c.named("grove_send_message") == [], "a mismatched sender must never be sent as anyone"
    assert r["spoke"][0]["ok"] is False
    assert r["spoke"][0]["blocked"] is True
    assert "sender_mismatch" in r["spoke"][0]["reason"]

    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["spoke"]["status"] == "blocked"
    assert "sender_mismatch" in entry["spoke"]["last_error"]

    # And it stays refused on later ticks, not retried every tick.
    tick.run_ci()
    assert c.named("grove_send_message") == []


# ── F3: a refused enqueue is not filed; it is retried, not silenced ─────────

def _grove_refuses_enqueues(c):
    """Filter c's human_required_enqueue calls down to the notifier's OWN
    item — the filing loop's separate per-head 'CI red: ...' review item
    also calls human_required_enqueue, and also retries every tick while
    refused, on the same fake tool name."""
    return [i for i in c.named("human_required_enqueue") if i["title"].startswith("Grove refuses")]


def test_enqueue_error_is_not_filed_and_is_retried_every_tick(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden",
               enqueue_error="gate denied: 'willow-bot' not permitted for 'human_required_enqueue'.")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()

    dedup_key = "willow-bot::willow"
    owed, _ = ci_comments.load()
    assert not ci_comments.human_required_is_filed(owed, dedup_key)
    row = ci_comments.human_required_row(owed, dedup_key)
    assert row["status"] == "could_not_run"
    assert "gate denied" in row["last_error"]
    assert len(_grove_refuses_enqueues(c)) == 1

    # Retried on the very next tick — decoupled from the Grove resend's
    # own much slower BLOCKED_PROBE_TICKS cadence.
    tick.run_ci()
    assert len(_grove_refuses_enqueues(c)) == 2
    assert len(c.named("grove_send_message")) == 1, "the Grove line itself stays blocked, not re-sent"

    # Once the enqueue itself succeeds, it is filed and stops retrying.
    c.enqueue_error = None
    tick.run_ci()
    assert len(_grove_refuses_enqueues(c)) == 3
    owed2, _ = ci_comments.load()
    assert ci_comments.human_required_is_filed(owed2, dedup_key)

    tick.run_ci()
    assert len(_grove_refuses_enqueues(c)) == 3, "filed — no further enqueue attempts"


# ── F4: un-block resolves the item and releases the dedupe key ──────────────

def test_unblock_resolves_the_item_and_releases_the_dedupe_key(home, monkeypatch):
    _prime()
    c = _Client(grove_error="sender_forbidden")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()

    dedup_key = "willow-bot::willow"
    owed, _ = ci_comments.load()
    row = ci_comments.human_required_row(owed, dedup_key)
    assert row["status"] == "filed"
    item_id = row["item_id"]

    # A grant lands: force an immediate re-probe via a manifest change
    # rather than waiting out BLOCKED_PROBE_TICKS (same trick the reprobe
    # test above uses).
    c.grove_error = None
    manifest = Path(home) / "mcp_apps" / "willow-bot" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"app_id": "willow-bot", "permissions": ["grove_write"]}),
                        encoding="utf-8")
    tick.run_ci()

    resolves = c.named("human_required_resolve")
    assert len(resolves) == 1
    assert resolves[0]["item_id"] == item_id
    assert resolves[0]["status"] == "resolved"

    owed2, _ = ci_comments.load()
    assert ci_comments.human_required_row(owed2, dedup_key) is None, "the dedupe key must be released"

    # A LATER new refusal on the same (identity, channel) — a revoked
    # grant, say — files a fresh item rather than staying deduped against
    # one that no longer describes anything.
    deposits.append_local(_row(RAT, "b" * 40, 99, "test", "failure", pr=99))
    c.grove_error = "sender_forbidden"
    tick.run_ci()
    fresh = [i for i in c.named("human_required_enqueue") if i["title"].startswith("Grove refuses")]
    assert len(fresh) == 2, "release must allow a fresh filing on a later refusal"


def test_unblock_without_a_filed_item_still_releases_cleanly(home, monkeypatch):
    """A block whose OWN human_required filing never succeeded
    (could_not_run) has no item to resolve — un-blocking must not error,
    and must still release the dedupe key."""
    _prime()
    c = _Client(grove_error="sender_forbidden", enqueue_error="postgres_unavailable")
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()

    dedup_key = "willow-bot::willow"
    owed, _ = ci_comments.load()
    assert ci_comments.human_required_row(owed, dedup_key)["status"] == "could_not_run"

    c.grove_error = None
    manifest = Path(home) / "mcp_apps" / "willow-bot" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"app_id": "willow-bot", "permissions": ["grove_write"]}),
                        encoding="utf-8")
    r = tick.run_ci()
    assert r["spoke"] == [{"channel": "willow", "grove_sender": "willow-bot", "ok": True}]
    assert c.named("human_required_resolve") == [], "nothing was ever filed — nothing to resolve"

    owed2, _ = ci_comments.load()
    assert ci_comments.human_required_row(owed2, dedup_key) is None
