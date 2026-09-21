"""The steward tells the seat that opened the PR (sealed pair 11ccb0f7, part 1).

pr_open_execute writes `$WILLOW_HOME/willow-bot/pr_watch.json`; on filing
a red or stuck item for a watched PR, run_ci posts ONE Grove message to the
watcher's channel and keeps the bot's per-head PR comment saying the same;
on resolve, one follow-up. Markers land only on `sent`, so refusals and
budget stops retry next tick and a re-tick never repeats a delivered line.
Unwatched PRs: nothing. The MCP client and the PR comment are fakes.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from willow_bot import deposits, pr_voice
from willow_bot.steward import pr_watch, tick, voice
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    monkeypatch.delenv(tick._CI_CANCELLED_GRACE_ENV, raising=False)
    monkeypatch.delenv(tick._GROVE_SENDER_ENV, raising=False)
    return tmp_path


class _Client:
    def __init__(self, *, refuse=None):
        self.calls = []
        self.refuse = refuse  # tool name -> error string, or callable(name, inputs, n)
        self.n = 0
        # `hr-N` numbers the human_required_* calls specifically — the
        # CI-red comment/Grove line now make their own `grove_send_message`
        # calls through this same fake client, interleaved with
        # `human_required_enqueue`/`_resolve` (finding 2, dispatch
        # E026CFE7), so a plain "Nth call of any kind" id would renumber
        # `hr-` ids out from under tests that never asked about Grove.
        self._hr_n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if callable(self.refuse):
            out = self.refuse(name, inputs, self.n)
            if out is not None:
                return out
        elif isinstance(self.refuse, dict) and name in self.refuse:
            return {"error": self.refuse[name]}
        if name.startswith("human_required"):
            self._hr_n += 1
            return {"ok": True, "id": f"hr-{self._hr_n}"}
        return {"ok": True}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


class _Comments:
    """A fake `pr_voice.upsert_status_comment`: remembers bodies per head."""

    def __init__(self, *, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, repo, pr_number, head_sha, body):
        self.calls.append({"repo": repo, "pr": pr_number, "head_sha": head_sha, "body": body})
        if not self.ok:
            return {"status": "could-not-run", "detail": "auth: no PEM", "action": "skipped"}
        seen = sum(1 for c in self.calls if c["head_sha"] == head_sha)
        return {"status": "ok", "action": "created" if seen == 1 else "updated",
                "comment_id": 1, "url": f"https://github.com/{repo}/pull/{pr_number}#c1"}


def _use(monkeypatch, client, comments=None):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)
    comments = comments if comments is not None else _Comments()
    monkeypatch.setattr(pr_voice, "upsert_status_comment", comments)
    return comments


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row(repo, sha, cid, name, conclusion, *, pr=None, branch=None, received_at=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid, check_name=name,
                                     conclusion=conclusion, pr_number=pr, head_branch=branch,
                                     received_at=received_at)
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


def _later(minutes: float):
    base = time.time()
    return lambda: base + minutes * 60


def _prime():
    deposits.append_local(_row("x/y", "0" * 40, 0, "older", "success"))
    assert tick.run_ci(enable_mcp=False)["first_run_skipped_bytes"] > 0


def _watch(repo, pr, *, app_id="willow", channel=None):
    p = pr_watch.watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    table = pr_watch.load(p)
    table[f"{repo}#{pr}"] = {"app_id": app_id, "session_id": "sess-1",
                             "channel": channel or f"#{app_id}",
                             "opened_at": "2026-09-21T02:03:12Z", "head": "feat/x"}
    p.write_text(json.dumps(table))


RAT = "willow-memory/ratatosk"
SHA = "632225cbe7980e169929d2ca0cc2d89ea15eedf6"
SHA2 = "9a61076c01b294fa18e59750ba09841d25aedc88"


# ── the watch file ───────────────────────────────────────────────────────────

def test_watch_reads_the_row_the_broker_wrote(home):
    _watch(RAT, 48)
    row = pr_watch.watcher_for(RAT, 48)
    assert row["channel"] == "#willow" and row["app_id"] == "willow"
    assert pr_watch.watcher_for(RAT, 49) is None
    assert pr_watch.watcher_for(RAT, None) is None


def test_watch_absent_or_malformed_reads_as_empty(home):
    assert pr_watch.load() == {}
    p = pr_watch.watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{oops")
    assert pr_watch.load() == {}
    p.write_text(json.dumps({"a#1": "not a row", "b#2": {"channel": "#b"}}))
    assert list(pr_watch.load()) == ["b#2"]


# ── filed red on a watched PR → one Grove message + one PR comment ───────────

def test_filed_red_on_a_watched_pr_is_told_to_the_seat_and_the_pr(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    deposits.append_local(_row(RAT, SHA, 2, "test", "failure", pr=48))
    r = tick.run_ci()
    assert len(r["filed"]) == 1
    sends = c.named("grove_send_message")
    assert len(sends) == 1
    msg = sends[0]
    assert msg["channel_name"] == "willow" and msg["sender"] == "willow-bot" and msg["app_id"] == "willow"
    assert msg["content"] == (f"CI red: {RAT}#48 @ {SHA[:7]} — lint, test — "
                              f"https://github.com/{RAT}/actions/runs/1/job/1")
    # The PR comment carries the same line, once, keyed on the head.
    assert len(comments.calls) == 1
    assert comments.calls[0]["head_sha"] == SHA and comments.calls[0]["pr"] == 48
    assert msg["content"] in comments.calls[0]["body"]
    line = r["notified"][0]
    assert line["state"] == "sent" and line["kind"] == "filed" and line["channel"] == "#willow"
    assert line["comment"]["state"] == "sent" and line["comment"]["action"] == "created"
    assert r["notified_counts"] == {"sent": 1, "refused": 0, "skipped": 0, "stopped": 0}
    state = json.loads(state_path().read_text())
    assert "filed" in state["ci_notified"][r["filed"][0]["id"]]


def test_a_re_tick_does_not_repeat_a_delivered_line(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    r2 = tick.run_ci()
    assert len(c.named("grove_send_message")) == 1
    assert len(comments.calls) == 1
    assert r2["notified"] == [] and r2["notified_counts"]["sent"] == 0


def test_a_second_red_leg_joining_the_item_does_not_resend(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA, 2, "test", "failure", pr=48))
    r2 = tick.run_ci()
    assert len(r2["appended"]) == 1
    assert len(c.named("grove_send_message")) == 1


# ── stuck wording ────────────────────────────────────────────────────────────

def test_a_stuck_filing_says_stuck_not_red(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test-windows (3.10)", "cancelled", pr=48))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    assert [x["state"] for x in r["cancelled"]] == ["stuck"]
    sends = c.named("grove_send_message")
    assert len(sends) == 1
    assert sends[0]["content"].startswith(f"CI stuck: {RAT}#48 @ {SHA[:7]} — test-windows (3.10) cancelled, "
                                          "no successor after 10 min — ")
    assert "CI red" not in sends[0]["content"]


# ── resolve → follow-up + comment edit ──────────────────────────────────────

def test_resolve_posts_a_follow_up_and_edits_the_comment(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    comments = _use(monkeypatch, c)
    base = time.time()
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48, received_at=_iso(base)))
    tick.run_ci()
    # The lint fix lands: a later head, every recorded leg green.
    deposits.append_local(_row(RAT, SHA2, 2, "lint", "success", pr=48, received_at=_iso(base + 60)))
    r = tick.run_ci()
    assert len(r["resolved"]) == 1 and r["resolved"][0]["how"] == "superseded"
    sends = c.named("grove_send_message")
    assert len(sends) == 2
    assert sends[1]["content"] == f"CI resolved: {RAT}#48 @ {SHA[:7]} — superseded by {SHA2[:7]}, green"
    # The comment on the RED head is edited to say so — same head, second write.
    assert [x["head_sha"] for x in comments.calls] == [SHA, SHA]
    assert "CI resolved:" in comments.calls[1]["body"]
    assert r["notified"][0]["kind"] == "resolved" and r["notified"][0]["comment"]["action"] == "updated"
    state = json.loads(state_path().read_text())
    # The item was delivered both ways, so the prune took it AND its marks
    # (Loki 717E236C: ci_notified used to outlive its items).
    assert r["resolved"][0]["item_id"] not in state["ci_notified"]
    assert not any(v.get("head_sha") == SHA for v in state["ci_items"].values())
    # And once more: nothing.
    r3 = tick.run_ci()
    assert r3["notified"] == [] and len(c.named("grove_send_message")) == 2


def test_a_refused_resolved_line_survives_the_prune_and_is_delivered_next_tick(home, monkeypatch):
    """Loki 717E236C: the item resolved at tick N, the `resolved` send was
    refused, and the prune at the end of N dropped the item — the seat
    heard 'CI red' and never 'CI resolved'. Now the item stays one tick."""
    _prime()
    _watch(RAT, 48)
    base = time.time()
    sends = {"refuse_resolved": True}

    def refuse(name, inputs, n):
        if name == "grove_send_message" and "CI resolved" in inputs["content"] and sends["refuse_resolved"]:
            return {"error": "postgres_unavailable"}
        return None

    c = _Client(refuse=refuse)
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48, received_at=_iso(base)))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA2, 2, "lint", "success", pr=48, received_at=_iso(base + 60)))
    r = tick.run_ci()
    assert len(r["resolved"]) == 1
    assert r["notified"][0]["kind"] == "resolved" and r["notified"][0]["state"] == "refused"
    state = json.loads(state_path().read_text())
    assert any(v.get("head_sha") == SHA and v.get("resolved") for v in state["ci_items"].values())
    assert "resolved" not in state["ci_notified"][r["resolved"][0]["item_id"]]
    sends["refuse_resolved"] = False
    r2 = tick.run_ci()
    assert r2["notified"][0]["kind"] == "resolved" and r2["notified"][0]["state"] == "sent"
    assert [m["content"][:11] for m in c.named("grove_send_message")] == ["CI red: wil", "CI resolved", "CI resolved"]
    state = json.loads(state_path().read_text())
    assert not any(v.get("head_sha") == SHA for v in state["ci_items"].values())
    assert r["resolved"][0]["item_id"] not in state["ci_notified"]


# ── refusal → retried next tick ─────────────────────────────────────────────

def test_a_refused_grove_send_is_a_line_and_retried_next_tick(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client(refuse={"grove_send_message": "postgres_unavailable"})
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    r = tick.run_ci()
    assert r["notified"][0]["state"] == "refused" and "postgres_unavailable" in r["notified"][0]["reason"]
    assert r["notified_counts"]["refused"] == 1
    assert comments.calls == []  # no comment without the seat told first
    state = json.loads(state_path().read_text())
    assert state["ci_notified"] == {}
    # The filing itself stood (it is in ci_filed); the notification retries.
    c.refuse = None
    r2 = tick.run_ci()
    assert r2["notified"][0]["state"] == "sent"
    assert len(c.named("grove_send_message")) == 2 and len(c.named("human_required_enqueue")) == 1


def test_a_refused_pr_comment_is_reported_but_the_seat_was_told(home, monkeypatch):
    _prime()
    _watch(RAT, 48)
    c = _Client()
    _use(monkeypatch, c, _Comments(ok=False))
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    r = tick.run_ci()
    line = r["notified"][0]
    assert line["state"] == "sent" and line["comment"]["state"] == "refused"
    assert "no PEM" in line["comment"]["reason"]


def test_a_spent_budget_stops_the_notification_and_retries(home, monkeypatch):
    _prime()
    _watch(RAT, 48)

    def refuse(name, inputs, n):
        if name == "grove_send_message":
            return {"error": "rate_limited", "retry_after": 100}
        return None

    c = _Client(refuse=refuse)
    _use(monkeypatch, c)
    t = {"now": 1000.0}
    monkeypatch.setattr(tick, "_clock", lambda: t["now"])
    monkeypatch.setattr(tick, "_sleep", lambda s: t.__setitem__("now", t["now"] + s))
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    r = tick.run_ci()
    assert r["notified"][0]["state"] == "stopped"
    assert r["notified_counts"]["stopped"] == 1
    assert json.loads(state_path().read_text())["ci_notified"] == {}
    c.refuse = None
    r2 = tick.run_ci()
    assert r2["notified"][0]["state"] == "sent"


# ── unwatched → skipped, said once ───────────────────────────────────────────

def test_an_unwatched_pr_is_skipped_and_said_only_when_filed(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    r = tick.run_ci()
    assert r["notified"] == [{"item_id": r["filed"][0]["id"], "where": f"{RAT}#48", "kind": "filed",
                              "state": "skipped", "reason": "no watch row"}]
    assert c.named("grove_send_message") == [] and comments.calls == []
    r2 = tick.run_ci()
    assert r2["notified"] == []


def test_a_prless_head_is_never_a_watched_pr(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", branch="main"))
    r = tick.run_ci()
    assert r["notified"][0]["state"] == "skipped" and r["notified"][0]["reason"] == "no pr"
    assert c.named("grove_send_message") == []


def test_the_sender_and_channel_come_from_the_row_and_the_env(home, monkeypatch):
    _prime()
    _watch(RAT, 48, app_id="hanuman")
    monkeypatch.setenv(tick._GROVE_SENDER_ENV, "steward")
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    msg = c.named("grove_send_message")[0]
    assert msg["channel_name"] == "hanuman" and msg["sender"] == "steward"


# ── the voice step's comment says the same ──────────────────────────────────

def test_voice_comment_body_carries_the_ci_line_for_a_filed_head(home):
    state = {"ci_items": {f"{RAT}#48@{SHA}": {"id": "hr-1", "repo": RAT, "pr": 48, "head_sha": SHA,
                                             "legs": ["lint"], "stuck": False,
                                             "url": "https://x/job/1"}},
             "ci_filed": {f"{SHA}:1": "hr-1"}}
    body = voice._status_comment_body(f"{RAT}#48", SHA, state, at="T")
    assert f"- CI red: {RAT}#48 @ {SHA[:7]} — lint — https://x/job/1\n" in body
    state["ci_items"][f"{RAT}#48@{SHA}"]["resolved"] = {"by": SHA2, "how": "superseded", "at": "T"}
    body2 = voice._status_comment_body(f"{RAT}#48", SHA, state, at="T")
    assert f"- CI resolved: {RAT}#48 @ {SHA[:7]} — superseded by {SHA2[:7]}, green\n" in body2


def test_voice_comment_body_is_unchanged_when_nothing_is_filed(home):
    body = voice._status_comment_body(f"{RAT}#48", SHA, {}, at="T")
    assert body == (f"willow-bot status for `{SHA[:12]}`\n- audit: not dispatched\n"
                    "- CI red: none filed\n- last tick: T\n")
