"""The steward tells the PR, like every CI bot (bite E026CFE7).

A red check_run gets its own failure-block comment (marker
`<!-- willow-bot:ci-red:<sha> -->`, distinct from the audit/status
comment `test_ci_notify.py` covers) and an UNCONDITIONAL word to Grove
`#willow` for any willow-memory/* repo — no `pr_watch` row required. The
GitHub calls (`ci_log.fetch_job_log`, `pr_voice.upsert_ci_red_comment`)
and the MCP client are fakes; nothing here reaches the network.
"""
from __future__ import annotations

import time

import pytest

from willow_bot import deposits
from willow_bot.steward import ci_log, tick


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


# ── log fetch 403 → receipt says actions:read missing, no comment ────────────

def test_log_fetch_403_names_actions_read_and_posts_no_comment(home, monkeypatch):
    _prime()
    c = _Client()
    comments = _use(monkeypatch, c, fetch_log=lambda repo, job_id: (
        None, {"status": "could-not-run", "missing_permission": "actions:read"}))
    deposits.append_local(_row(RAT, SHA, 1, "test", "failure", pr=48))
    r = tick.run_ci()
    entry = r["commented"][0]
    assert entry["action"] == "skipped"
    assert entry["reason"] == "missing permission: actions:read"
    assert comments.calls == []


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
    # first 10 lines only
    assert len(sends[0]["content"].splitlines()) <= 11


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
