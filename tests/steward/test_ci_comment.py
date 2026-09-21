"""The steward tells the PR, like every CI bot (bite E026CFE7).

A red check_run gets its own failure-block comment (marker
`<!-- willow-bot:ci-red:<sha> -->`, distinct from the audit/status
comment `test_ci_notify.py` covers) and an UNCONDITIONAL word to Grove
`#willow` for any willow-memory/* repo — no `pr_watch` row required. The
GitHub calls (`ci_log.fetch_job_log`, `pr_voice.upsert_ci_red_comment`)
and the MCP client are fakes; nothing here reaches the network.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from willow_bot import deposits
from willow_bot.steward import ci_comments, ci_log, tick
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
        self.refuse = refuse
        self.n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if callable(self.refuse):
            out = self.refuse(name, inputs, self.n)
            if out is not None:
                return out
        elif isinstance(self.refuse, dict) and name in self.refuse:
            return {"error": self.refuse[name]}
        return {"ok": True, "id": f"hr-{self.n}"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


class _RedComments:
    """A fake `pr_voice.upsert_ci_red_comment`: one marked row per head."""

    def __init__(self, *, fail_permission=None):
        self.calls: list[dict] = []
        self._by_head: dict[str, int] = {}
        self._next_id = 1
        self.fail_permission = fail_permission

    def __call__(self, repo, pr_number, head_sha, body):
        self.calls.append({"repo": repo, "pr": pr_number, "head_sha": head_sha, "body": body})
        if self.fail_permission:
            return {"status": "could-not-run", "action": "skipped",
                    "missing_permission": self.fail_permission,
                    "detail": f"403: missing {self.fail_permission}"}
        if head_sha in self._by_head:
            return {"status": "ok", "action": "edited", "comment_id": self._by_head[head_sha],
                    "url": f"https://github.com/{repo}/pull/{pr_number}#c{self._by_head[head_sha]}"}
        cid = self._next_id
        self._next_id += 1
        self._by_head[head_sha] = cid
        return {"status": "ok", "action": "created", "comment_id": cid,
                "url": f"https://github.com/{repo}/pull/{pr_number}#c{cid}"}


def _use(monkeypatch, client, *, comments=None, fetch_log=None, extract=None):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)
    comments = comments if comments is not None else _RedComments()
    monkeypatch.setattr("willow_bot.pr_voice.upsert_ci_red_comment", comments)
    fetch_log = fetch_log or (lambda repo, job_id: ("boring log\n", {"status": "ok"}))
    monkeypatch.setattr(ci_log, "fetch_job_log", fetch_log)
    extract = extract or (lambda text: {"block": "BOOM: assert False", "trimmed": False, "source": "pytest"})
    monkeypatch.setattr(ci_log, "extract_failure_block", extract)
    return comments


def _row(repo, sha, cid, name, conclusion, *, pr=None, branch=None):
    rec = deposits.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid, check_name=name,
                                     conclusion=conclusion, pr_number=pr, head_branch=branch)
    rec["html_url"] = f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


def _later(minutes: float):
    base = time.time()
    return lambda: base + minutes * 60


def _prime():
    deposits.append_local(_row("x/y", "0" * 40, 0, "older", "success"))
    assert tick.run_ci(enable_mcp=False)["first_run_skipped_bytes"] > 0


RAT = "willow-memory/ratatosk"
NOT_WM = "someone-else/other-repo"
SHA = "632225cbe7980e169929d2ca0cc2d89ea15eedf6"
SHA2 = "9a61076c01b294fa18e59750ba09841d25aedc88"


# ── red → comment created with marker + failure block ────────────────────────

def test_red_creates_a_ci_red_comment_with_the_failure_block(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert len(r["commented"]) == 1
    entry = r["commented"][0]
    assert entry["action"] == "created" and entry["head_sha"] == SHA and entry["repo_pr"] == f"{RAT}#48"
    assert len(comments.calls) == 1
    body = comments.calls[0]["body"]
    assert "test" in body and "failure" in body
    assert "BOOM: assert False" in body


# ── second red on the same sha → edited, not duplicated ──────────────────────

def test_a_second_red_leg_on_the_same_sha_edits_not_duplicates(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA, 2, "lint", "failure", pr=48))
    r2 = tick.run_ci()
    assert len(comments.calls) == 2
    assert [e["action"] for e in r2["commented"]] == ["edited"]


# ── green after red → comment edited to say so ───────────────────────────────

def test_green_after_red_edits_the_comment_to_green(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA2, 2, "lint", "success", pr=48))
    r = tick.run_ci()
    assert len(r["resolved"]) == 1
    green_entries = [e for e in r["commented"] if e["action"] == "edited"]
    assert len(green_entries) == 1
    assert green_entries[0]["head_sha"] == SHA  # edits the ORIGINAL red comment
    body = comments.calls[-1]["body"]
    assert f"green at {SHA2[:7]}" in body


# ── log fetch failure of any kind → the comment still posts, with a note ─────

def test_log_fetch_403_posts_the_comment_with_a_log_unavailable_note(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c, fetch_log=lambda repo, job_id: (
        None, {"status": "could-not-run", "missing_permission": "actions:read"}))
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    entry = r["commented"][0]
    assert entry["action"] == "created"
    assert len(comments.calls) == 1
    body = comments.calls[0]["body"]
    assert "log unavailable" in body and "actions:read" in body


def test_log_fetch_rate_limited_is_not_named_a_permission_and_still_comments(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c, fetch_log=lambda repo, job_id: (
        None, {"status": "rate_limited", "detail": "GitHub rate limit fetching job logs"}))
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    entry = r["commented"][0]
    assert entry["action"] == "created"
    body = comments.calls[0]["body"]
    assert "rate limited" in body
    assert "actions:read" not in body


# ── the comment is owed until it lands: a 502 is retried, not permanent ──────

def test_comment_502_is_retried_next_tick_and_lands(home, monkeypatch):
    _prime()
    c = _Client()
    calls: list[dict] = []

    def _flaky(repo, pr_number, head_sha, body):
        calls.append({"repo": repo, "pr": pr_number, "head_sha": head_sha, "body": body})
        if len(calls) == 1:
            return {"status": "could-not-run", "detail": "502 Bad Gateway", "action": "skipped"}
        return {"status": "ok", "action": "created", "comment_id": 9,
                "url": f"https://github.com/{repo}/pull/{pr_number}#c9"}

    _use(monkeypatch, c, comments=_flaky)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r1 = tick.run_ci()
    assert r1["commented"][0]["action"] == "skipped"
    assert r1["commented"][0]["reason"] == "502 Bad Gateway"
    # No new deposit on the next tick — the retry comes from the owed
    # table, independent of `ci_filed`, which already remembers this leg.
    r2 = tick.run_ci()
    assert r2["commented"][0]["action"] == "created"
    assert len(calls) == 2


# ── broker independence: a refused/absent broker never blocks the comment ────

def test_broker_refused_enqueue_does_not_prevent_the_comment(home, monkeypatch):
    _prime()
    c = _Client(refuse={"human_required_enqueue": "refused"})
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert len(comments.calls) == 1
    assert r["commented"][0]["action"] == "created"
    assert r["refused"]


def test_broker_down_reports_spoke_unreachable_but_still_comments(home, monkeypatch):
    _prime()
    comments = _RedComments()
    monkeypatch.setattr("willow_bot.pr_voice.upsert_ci_red_comment", comments)
    monkeypatch.setattr(ci_log, "fetch_job_log", lambda repo, job_id: ("boring log\n", {"status": "ok"}))
    monkeypatch.setattr(ci_log, "extract_failure_block",
                        lambda text: {"block": "BOOM: assert False", "trimmed": False, "source": "pytest"})
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci(enable_mcp=False)
    assert len(comments.calls) == 1
    assert r["spoke"] == [{"channel": "willow", "state": "unreachable"}]


# ── a second red job on a later tick keeps BOTH jobs in the body ─────────────

def test_second_red_job_on_a_later_tick_keeps_both_jobs_in_the_body(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA, 2, "test", "failure", pr=48))
    r2 = tick.run_ci()
    assert r2["commented"][0]["action"] == "edited"
    body = comments.calls[-1]["body"]
    assert "**lint**" in body and "**test**" in body
    assert "2 job(s)" in body


# ── body cap: header and links survive, the failure block shrinks ───────────

def test_body_is_capped_and_header_survives(home, monkeypatch):
    _prime()
    c = _Client()
    big_block = "\n".join(f"line {i} " + "x" * 100 for i in range(2000))
    comments = _use(monkeypatch, c,
                    extract=lambda text: {"block": big_block, "trimmed": False, "source": "pytest"})
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    deposits.append_local(_row(RAT, SHA, 2, "lint", "failure", pr=48))
    r = tick.run_ci()
    body = comments.calls[-1]["body"]
    assert len(body) <= tick.BODY_CHAR_CAP
    assert body.startswith(f"CI red: {RAT}#48 @ {SHA[:7]}")
    assert f"https://github.com/{RAT}/actions/runs/1/job/1" in body
    assert f"https://github.com/{RAT}/actions/runs/1/job/2" in body
    assert r["commented"][0]["action"] in ("created", "edited")


# ── Grove line is owed until sent, and never sent twice ──────────────────────

def test_grove_send_failure_is_retried_and_never_resent_after_success(home, monkeypatch):
    _prime()
    calls = {"n": 0}

    def _client(name, inputs):
        if name == "grove_send_message":
            calls["n"] += 1
            if calls["n"] == 1:
                return {"error": "refused"}
            return {"ok": True}
        return {"ok": True, "id": "hr-1"}

    _use(monkeypatch, _client)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r1 = tick.run_ci()
    assert r1["spoke"] == [{"channel": "willow", "ok": False, "reason": "refused"}]
    r2 = tick.run_ci()
    assert r2["spoke"] == [{"channel": "willow", "ok": True}]
    r3 = tick.run_ci()
    assert r3["spoke"] == []
    assert calls["n"] == 2


# ── the Grove summary closes any fence/<details> its own trim leaves open ────

def test_grove_summary_closes_an_open_fence_and_details_block(home, monkeypatch):
    _prime()
    c = _Client()
    many_lines = "\n".join(f"failure line {i}" for i in range(200))
    _use(monkeypatch, c, extract=lambda text: {"block": many_lines, "trimmed": False, "source": "pytest"})
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    sends = c.named("grove_send_message")
    assert len(sends) == 1
    content = sends[0]["content"]
    assert content.count("```") % 2 == 0
    assert content.count("<details>") == content.count("</details>")


# ── PR-comment permission missing → named exactly, no crash ──────────────────

def test_pr_comment_403_names_pull_requests_write(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c, comments=_RedComments(fail_permission="pull_requests:write"))
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    entry = r["commented"][0]
    assert entry["action"] == "skipped"
    assert entry["reason"] == "missing permission: pull_requests:write"


# ── unconditional Grove speak, no pr_watch row needed ─────────────────────────

def test_grove_is_told_unconditionally_for_a_willow_memory_repo_with_no_watch_row(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["spoke"] == [{"channel": "willow", "ok": True}]
    sends = c.named("grove_send_message")
    assert len(sends) == 1
    assert sends[0]["channel_name"] == "willow"
    assert f"https://github.com/{RAT}/pull/48" in sends[0]["content"]
    # head(60) + tail(60) of the body, not a naive first-N-lines cut
    assert len(sends[0]["content"].splitlines()) <= 121


def test_grove_is_not_told_for_a_non_willow_memory_repo(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(NOT_WM, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["spoke"] == []
    assert c.named("grove_send_message") == []


def test_grove_is_spoken_to_only_once_per_item(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA, 2, "lint", "failure", pr=48))
    r2 = tick.run_ci()
    assert r2["spoke"] == []
    assert len(c.named("grove_send_message")) == 1


# ── stuck-only (cancelled) heads: no failure log to show, no comment ─────────

def test_stuck_only_head_gets_no_ci_red_comment(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test-windows (3.10)", "cancelled", pr=48))
    tick.run_ci()
    monkeypatch.setattr(tick, "_ci_clock", _later(11))
    r = tick.run_ci()
    assert any(x["state"] == "stuck" for x in r["cancelled"])
    assert comments.calls == []
    assert any(e["action"] == "skipped" and "stuck" in e["reason"] for e in r["commented"])


# ── PR-less head: no PR to comment on ────────────────────────────────────────

def test_prless_head_skips_comment_with_reason(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", branch="main"))
    r = tick.run_ci()
    assert comments.calls == []
    assert r["commented"] == [{"repo_pr": f"{RAT}@{SHA[:12]}", "head_sha": SHA,
                               "comment_id": None, "action": "skipped", "reason": "no pr"}]
    assert r["spoke"] == []


# ── Loki's re-audit (dispatch E026CFE7, second pass): never abandon ──────────
# a GitHub outage stalls the comment, never abandons it, and never silences
# Grove (which needs nothing from GitHub) while it waits.

def test_github_down_45_ticks_then_up_lands_within_12_never_abandons(home, monkeypatch):
    _prime()
    c = _Client()
    wall = {"tick": 0}
    UP_AFTER = 45  # GitHub is down for this many of THIS test's own ticks

    def _flaky(repo, pr_number, head_sha, body):
        if wall["tick"] <= UP_AFTER:
            return {"status": "could-not-run", "detail": "502 Bad Gateway", "action": "skipped"}
        return {"status": "ok", "action": "created", "comment_id": 42,
                "url": f"https://github.com/{repo}/pull/{pr_number}#c42"}

    _use(monkeypatch, c, comments=_flaky)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))

    wall["tick"] = 1
    first = tick.run_ci()
    # Grove hears immediately — it never waited on the comment landing.
    assert first["spoke"] == [{"channel": "willow", "ok": True}]

    saw_stalled = False
    landed_at = None
    for i in range(2, UP_AFTER + ci_comments.STALL_PROBE_TICKS + 4):
        wall["tick"] = i
        r = tick.run_ci()
        if any(x["channel"] == "comment" and x["attempts"] >= ci_comments.MAX_ATTEMPTS
               for x in r["stalled"]):
            saw_stalled = True
        created = [e for e in r["commented"] if e["action"] == "created"]
        if created:
            landed_at = i
            break
    assert saw_stalled, "a long outage must read `stalled` on the receipt, never silence"
    assert landed_at is not None, "GitHub coming back must eventually post the comment — never abandoned"
    assert landed_at <= UP_AFTER + ci_comments.STALL_PROBE_TICKS + 3


# ── a rate limit is a pause, not a failure ────────────────────────────────────

def test_rate_limited_does_not_burn_an_attempt_and_honours_retry_after(home, monkeypatch):
    _prime()
    c = _Client()
    calls: list = []

    def _flaky(repo, pr_number, head_sha, body):
        calls.append(1)
        if len(calls) == 1:
            return {"status": "rate_limited", "detail": "GitHub rate limit posting a PR comment",
                    "retry_after": "900", "action": "skipped"}
        return {"status": "ok", "action": "created", "comment_id": 7,
                "url": f"https://github.com/{repo}/pull/{pr_number}#c7"}

    _use(monkeypatch, c, comments=_flaky)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))

    r1 = tick.run_ci()
    assert r1["commented"][0]["action"] == "skipped"
    assert "rate limited" in r1["commented"][0]["reason"]
    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["comment"]["attempts"] == 0, "a rate limit must never increment attempts"

    # retry_after=900s at the 300s default interval is 3 ticks — paused
    # through ticks 2 and 3, due again at tick 4.
    tick.run_ci()
    tick.run_ci()
    assert len(calls) == 1, "still paused; must not retry before Retry-After elapses"
    r4 = tick.run_ci()
    assert len(calls) == 2
    assert r4["commented"][0]["action"] == "created"


# ── broker refused: re-fetch/reset only on a genuinely new leg ───────────────

def test_broker_refused_every_tick_fetches_and_edits_once(home, monkeypatch):
    _prime()
    c = _Client(refuse={"human_required_enqueue": "refused"})
    fetch_calls = {"n": 0}

    def _fetch(repo, job_id):
        fetch_calls["n"] += 1
        return "boring log\n", {"status": "ok"}

    comments = _use(monkeypatch, c, fetch_log=_fetch)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    for _ in range(4):
        tick.run_ci()
    assert fetch_calls["n"] == 1
    assert len(comments.calls) == 1


# ── body cap through the REAL ci_log trim, per-leg budgets, balanced fences ──

def test_body_char_cap_via_real_trim_leaves_balanced_fences(home, monkeypatch):
    _prime()
    c = _Client()
    huge_line = "line " + ("x" * 4000)
    log_text = (
        "________________________________ FAILURES _________________________________\n"
        + "\n".join(huge_line for _ in range(2000))
        + "\n=== 1 failed in 1s ===\n"
    )
    comments = _use(monkeypatch, c, fetch_log=lambda repo, job_id: (log_text, {"status": "ok"}),
                    extract=ci_log.extract_failure_block)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    deposits.append_local(_row(RAT, SHA, 2, "lint", "failure", pr=48))
    r = tick.run_ci()
    body = comments.calls[-1]["body"]
    assert len(body) <= tick.BODY_CHAR_CAP
    assert body.count("```") % 2 == 0
    assert body.count("<details>") == body.count("</details>")
    if "_body capped at" in body:
        prefix = body[: body.index("_body capped at")]
        assert prefix.count("```") % 2 == 0
        assert prefix.count("<details>") == prefix.count("</details>")
    assert r["commented"][0]["action"] in ("created", "edited")


# ── retirement: green retires after one tick, a closed PR retires at once ────

def test_green_entry_is_retired_after_one_tick(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "lint", "failure", pr=48))
    tick.run_ci()
    deposits.append_local(_row(RAT, SHA2, 2, "lint", "success", pr=48))
    tick.run_ci()  # this tick edits the comment to green
    owed, _ = ci_comments.load()
    assert ci_comments.head_keys(owed) != [], "retires the NEXT tick, not the same one"
    r3 = tick.run_ci()
    owed2, _ = ci_comments.load()
    assert ci_comments.head_keys(owed2) == []
    assert r3.get("retired")


def test_pr_closed_retires_the_entry_immediately(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    owed, _ = ci_comments.load()
    assert ci_comments.head_keys(owed) != []
    st = state_path()
    state = json.loads(st.read_text())
    state["pr_closed"] = {f"{RAT}#48": {"at": "2026-09-21T00:00:00+00:00", "merged": True}}
    st.write_text(json.dumps(state))
    r = tick.run_ci()
    owed2, _ = ci_comments.load()
    assert ci_comments.head_keys(owed2) == []
    assert r.get("retired")


# ── the drain: guarded, and a corrupt table is quarantined, never {} ─────────

def test_corrupt_owed_table_is_quarantined_not_silently_forgotten(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    p = ci_comments.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["ci_comments_table"]["status"] == "unreadable"
    quarantined = r["ci_comments_table"]["quarantined"]
    assert quarantined and Path(quarantined).name.startswith("ci_comments.json.corrupt-")
    assert Path(quarantined).read_text(encoding="utf-8") == "{not json"
    assert p.exists()
    assert r["commented"][0]["action"] == "created"


# ── unreadable (not missing, not corrupt-json) table: skip, never replace ────

def test_unreadable_table_skips_the_drain_and_is_never_overwritten(home, monkeypatch):
    """Loki's re-audit, MEDIUM finding 4: `load()` used to catch EVERY
    `OSError` (including EACCES/EIO on a file that is there and fine, just
    unreadable right now) the same way it catches a missing file —
    `({}, None)` — so the receipt read `ci_comments_table: ok` and the
    next `save()` happily `os.replace`'d a fresh empty table over the one
    it never actually read. Simulate an unreadable-but-present file by
    monkeypatching `Path.read_text` to raise a permission error."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    p = ci_comments.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"some#head": {"repo": "x/y", "pr": 1, "head_sha": "a" * 40, "where": "x/y#1",
                                           "legs": {}, "comment": ci_comments._sub(), "spoke": ci_comments._sub(),
                                           "_created_at": time.time()}}), encoding="utf-8")

    real_read_text = Path.read_text

    def _denied(self, *a, **kw):
        if self == p:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _denied)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    assert r["ci_comments_table"]["status"] == "unreachable"
    assert r["ci_comments_table"]["drain_skipped"] is True
    monkeypatch.setattr(Path, "read_text", real_read_text)
    # The file on disk is untouched — never blown away by a save() over a
    # table this process could not read.
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert "some#head" in on_disk and len(on_disk) == 1


def test_save_sweeps_a_leftover_tmp_sibling(home):
    p = ci_comments.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    stray = p.parent / ".ci_comments.abc123.tmp"
    stray.write_text("half-written", encoding="utf-8")
    ci_comments.save({})
    assert not stray.exists()


# ── prune vs forever: only a green/closed entry ages out, never a live one ──

def test_stalled_entry_survives_the_14_day_prune_and_is_still_probed(home, monkeypatch):
    _prime()
    c = _Client()

    def _flaky(repo, pr_number, head_sha, body):
        return {"status": "could-not-run", "detail": "502 Bad Gateway", "action": "skipped"}

    _use(monkeypatch, c, comments=_flaky)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    entry = None
    for _ in range(60):  # backoff grows with attempts; MAX_ATTEMPTS ticks alone is not enough clock
        tick.run_ci()
        owed, _ = ci_comments.load()
        entry = owed[ci_comments.head_keys(owed)[0]]
        if entry["comment"]["status"] == "stalled":
            break
    assert entry["comment"]["status"] == "stalled"
    entry["_created_at"] = time.time() - 15 * 86400
    ci_comments.save(owed)
    r = tick.run_ci()
    assert not r.get("retired")
    owed2, _ = ci_comments.load()
    assert ci_comments.head_keys(owed2) != [], "a still-stalled entry must never be pruned by age alone"
    assert any(x["channel"] == "comment" for x in r["stalled"]), "and must still be probed"


def test_a_green_entry_past_14_days_is_pruned_with_a_reason(home, monkeypatch):
    owed = {}
    entry = ci_comments.entry_for(owed, "x/y#1@" + "a" * 40, repo="x/y", pr=1, head_sha="a" * 40, where="x/y#1")
    ci_comments.record_success(entry["comment"], status="green", tick=1)
    entry["_created_at"] = time.time() - 15 * 86400
    pruned = ci_comments.prune_old(owed, now_epoch=time.time())
    assert pruned == [{"key": "x/y#1@" + "a" * 40, "reason": pruned[0]["reason"]}]
    assert "green" in pruned[0]["reason"]
    assert owed == {}


def test_a_row_without_created_at_gets_one_on_load(home):
    p = ci_comments.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"x/y#1@" + "a" * 40: {"repo": "x/y", "pr": 1, "head_sha": "a" * 40,
                                                    "where": "x/y#1", "legs": {},
                                                    "comment": ci_comments._sub(), "spoke": ci_comments._sub()}}),
                encoding="utf-8")
    owed, corrupt = ci_comments.load()
    assert corrupt is None
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert isinstance(entry.get("_created_at"), (int, float))


# ── strip_blocks + a later leg: the first leg's real block must survive ──────

def test_stripped_block_is_refetched_before_a_later_leg_rewrites_the_body(home, monkeypatch):
    """Loki's re-audit, HIGH finding 1: once both channels land,
    `strip_blocks` drops every leg's block (to keep the owed table small).
    A second red job on the same head used to rebuild the body straight
    from the (now block-less) table and edit the PR comment to
    `_test: log unavailable (unknown error)_` for the FIRST job — a
    falsehood: the bot had that block minutes earlier and threw it away
    on purpose, not because the fetch failed. The body must re-fetch a
    stripped leg before rendering it again."""
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()  # comment + Grove land; test's block is stripped afterward
    owed, _ = ci_comments.load()
    entry = owed[ci_comments.head_keys(owed)[0]]
    assert entry["comment"]["status"] == "posted"
    assert all(not v.get("block") for v in entry["legs"].values())  # stripped

    deposits.append_local(_row(RAT, SHA, 2, "lint", "failure", pr=48))
    r2 = tick.run_ci()
    assert r2["commented"][0]["action"] == "edited"
    body = comments.calls[-1]["body"]
    assert "**test**" in body and "**lint**" in body
    assert "BOOM: assert False" in body, "the FIRST job's real block must be back, not 'log unavailable'"
    assert "log unavailable" not in body


# ── refused broker + a re-run of the same check: no forever flap ────────────

def test_broker_refused_rerun_of_same_check_is_one_extra_fetch_then_steady(home, monkeypatch):
    """Loki's re-audit, HIGH finding 2, probe C2: a refused broker (so
    neither leg is ever marked `ci_filed`) plus a re-run of the SAME check
    (new job id, same name) used to flap the owed table forever — the
    table was keyed by check NAME, so the two job ids overwrote each
    other every tick, one 5 MB-capable log fetch and one comment edit per
    tick for the length of the outage. Keyed by job id: the re-run costs
    exactly one more fetch and one more edit, then nothing, ever again,
    for as long as the broker keeps refusing."""
    _prime()
    c = _Client(refuse={"human_required_enqueue": "refused"})
    fetch_calls = {"n": 0}

    def _fetch(repo, job_id):
        fetch_calls["n"] += 1
        return "boring log\n", {"status": "ok"}

    comments = _use(monkeypatch, c, fetch_log=_fetch)
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    assert fetch_calls["n"] == 1 and len(comments.calls) == 1

    # A re-run of the SAME check: new job id 9, same name "test", broker
    # still refusing (job 1's webhook event replays too — the offset never
    # advanced past it while filing stays refused).
    deposits.append_local(_row(RAT, SHA, 9, "test", "failure", pr=48))
    tick.run_ci()
    assert fetch_calls["n"] == 2, "one extra fetch for the new job id"
    assert len(comments.calls) == 2, "one extra edit"

    for _ in range(10):
        tick.run_ci()
    assert fetch_calls["n"] == 2, "steady — the superseded job id is never fetched again"
    assert len(comments.calls) == 2, "steady — no more flapping"


# ── the Grove summary caps by characters, not lines ──────────────────────────

def test_grove_summary_is_capped_by_characters_not_lines(home, monkeypatch):
    """Loki's re-audit: a 60-line head+tail cut let a real body through
    untouched when its lines were each several KB wide (a wrapped pytest
    failure) — 59,864 characters into a chat channel. The cap must bind
    on characters."""
    _prime()
    c = _Client()
    huge_block = "\n".join("x" * 4000 for _ in range(60))  # ~240k chars, 60 "lines"
    _use(monkeypatch, c, extract=lambda text: {"block": huge_block, "trimmed": False, "source": "pytest"})
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    tick.run_ci()
    sends = [m for m in c.named("grove_send_message")]
    assert len(sends) == 1
    # The cap plus the PR url and close-block slack — nowhere near the
    # ~240k-char body a line-based cut would have let through untouched.
    assert len(sends[0]["content"]) <= tick.GROVE_SUMMARY_CHAR_CAP + 500
