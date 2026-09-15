"""The catch-up step (2026-09-15): a missed webhook is repaired next tick.

A lost webhook (Pangolin restart mid-delivery, systemd roll while a POST
was in flight, proxy drops the body) leaves the tick's ``open`` set
stale — the audit step then never sees the PR. This step polls
``/repos/{repo}/pulls`` under the App's install token, reconciles the
answer with ``state``, and promotes any missing PR to ``pending_audit``
under the same shape ``run_once`` writes. The GitHub call is stubbed;
the state file is real.
"""
from __future__ import annotations

import json
import sys

import pytest

from willow_bot.steward import tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    monkeypatch.delenv("WILLOW_BOT_CATCHUP_EXTRA_REPOS", raising=False)
    return tmp_path


class _FakeApp:
    """Stub replacement for ``github_app.list_open_pulls`` — the catch-up
    step imports the module locally with ``import github_app``, so setting
    an entry in ``sys.modules`` before the call routes to this fake."""

    def __init__(self, answer=None, raises=None):
        self.answer = answer or {}
        self.raises = raises or {}
        self.calls: list[str] = []

    def list_open_pulls(self, repo, **kw):
        self.calls.append(repo)
        if repo in self.raises:
            raise self.raises[repo]
        return list(self.answer.get(repo, []))


def _install_fake(monkeypatch, fake):
    """Register the fake as the ``github_app`` module for the catch-up
    step's local ``import github_app``."""
    monkeypatch.setitem(sys.modules, "github_app", fake)


def _write_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


def _read_state() -> dict:
    p = state_path()
    return json.loads(p.read_text()) if p.is_file() else {}


def test_absent_when_mcp_is_off(home, monkeypatch, capsys):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    r = tick.run_catchup()
    assert r["status"] == "absent"
    assert "WILLOW_BOT_MCP" in r["detail"]
    # Nothing was called, nothing was written.
    assert not state_path().is_file()
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "steward_catchup" and line["status"] == "absent"


def test_no_repos_is_an_honest_ok(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    fake = _FakeApp()
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert r["status"] == "ok" and r["polled"] == [] and r["repaired"] == []
    assert r["repos_known"] == 0
    assert "no repos to poll" in r["detail"]
    assert fake.calls == []


def test_repos_are_discovered_from_state(home, monkeypatch):
    """A repo that has ever appeared in state (open, seen, merged_synced)
    is polled by the catch-up step. Sorted so the cursor rotation is stable
    across process restarts."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": ["a/x#1"], "seen": ["b/y#2"], "merged_synced": ["c/z#3"]})
    fake = _FakeApp(answer={"a/x": [], "b/y": [], "c/z": []})
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert r["status"] == "ok"
    assert r["repos_known"] == 3
    # First tick polls the first _CATCHUP_PER_TICK sorted repos.
    assert fake.calls == ["a/x", "b/y", "c/z"]


def test_extras_env_adds_repos_the_state_has_not_seen(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_CATCHUP_EXTRA_REPOS", "willow-memory/willow-bot,x/y")
    _write_state({"open": [], "seen": [], "merged_synced": []})
    fake = _FakeApp(answer={"willow-memory/willow-bot": [], "x/y": []})
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert r["repos_known"] == 2
    assert set(fake.calls) == {"willow-memory/willow-bot", "x/y"}


def test_a_missing_pr_is_promoted_to_pending_audit(home, monkeypatch):
    """The whole point: an open PR the API knows and state does not gets
    promoted to pending_audit under the shape run_once writes."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": ["a/x#1"], "seen": ["a/x#1"], "merged_synced": [],
                  "pending_audit": [], "audit_dispatched": {}})
    fake = _FakeApp(answer={"a/x": [
        {"number": 1, "title": "known one", "html_url": "https://gh/a/x/1"},
        {"number": 5, "title": "MISSED", "html_url": "https://gh/a/x/5"},
    ]})
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert r["status"] == "ok"
    assert [rp["repo_pr"] for rp in r["repaired"]] == ["a/x#5"]
    st = _read_state()
    assert "a/x#5" in st["open"]
    pending = st["pending_audit"]
    assert any(p["repo_pr"] == "a/x#5" and p["title"] == "MISSED" for p in pending)


def test_a_pr_already_pending_is_not_duplicated(home, monkeypatch):
    """Idempotency: a repair that races with a webhook must not re-append
    an already-known PR to pending_audit."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": [], "seen": [], "merged_synced": [],
                  "pending_audit": [{"repo_pr": "a/x#5", "title": "prev", "url": "u"}],
                  "audit_dispatched": {}})
    fake = _FakeApp(answer={"a/x": [{"number": 5, "title": "MISSED", "html_url": "u2"}]})
    _install_fake(monkeypatch, fake)
    tick.run_catchup()
    st = _read_state()
    assert len([p for p in st["pending_audit"] if p["repo_pr"] == "a/x#5"]) == 1


def test_a_pr_already_dispatched_is_not_re_pended(home, monkeypatch):
    """A PR the audit step has already dispatched (in audit_dispatched)
    must not be re-pended by a catch-up poll that sees it as still-open."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": [], "seen": [], "merged_synced": [],
                  "pending_audit": [], "audit_dispatched": {"a/x#5": "did-1"}})
    fake = _FakeApp(answer={"a/x": [{"number": 5, "title": "MISSED", "html_url": "u"}]})
    _install_fake(monkeypatch, fake)
    tick.run_catchup()
    st = _read_state()
    assert st["pending_audit"] == []
    # open is still repaired — the PR is real, state just missed it.
    assert "a/x#5" in st["open"]


def test_a_repo_that_raises_is_a_line_not_a_dead_step(home, monkeypatch):
    """One repo failing (an installation revoked, a 5xx from api.github.com)
    is a line with its reason and does not stop the batch."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": ["a/x#1", "b/y#1", "c/z#1"], "seen": [], "merged_synced": []})
    fake = _FakeApp(
        answer={"a/x": [{"number": 2, "title": "t", "html_url": "u"}],
                "c/z": [{"number": 3, "title": "u", "html_url": "v"}]},
        raises={"b/y": RuntimeError("HTTP 500: internal server error")},
    )
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert r["status"] == "ok"
    assert [e["repo"] for e in r["errors"]] == ["b/y"]
    assert "500" in r["errors"][0]["error"]
    # a/x and c/z still yielded their repair rows despite b/y failing.
    assert {rp["repo_pr"] for rp in r["repaired"]} == {"a/x#2", "c/z#3"}


def test_the_cursor_rotates_across_ticks(home, monkeypatch):
    """A fleet larger than _CATCHUP_PER_TICK spreads polls across ticks.
    Each call advances ``catchup_cursor.next_index`` by the batch size."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    repos = [f"o/r{i}" for i in range(5)]
    _write_state({"open": [f"{r}#1" for r in repos], "seen": [], "merged_synced": []})
    fake = _FakeApp(answer={r: [] for r in repos})
    _install_fake(monkeypatch, fake)

    tick.run_catchup()
    st1 = _read_state()
    assert st1["catchup_cursor"]["next_index"] == 3
    assert fake.calls == repos[:3]

    fake.calls.clear()
    tick.run_catchup()
    st2 = _read_state()
    # Wraps around from 3, picks r3, r4, r0.
    assert st2["catchup_cursor"]["next_index"] == (3 + 3) % 5
    assert fake.calls == [repos[3], repos[4], repos[0]]


def test_a_pr_with_no_number_is_skipped_not_crashed(home, monkeypatch):
    """The GitHub API is normally well-shaped, but a proxy that answers
    a partial page must not blow up the step."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _write_state({"open": [], "seen": ["a/x#1"], "merged_synced": []})
    fake = _FakeApp(answer={"a/x": [{"title": "no number"}, None,
                                     {"number": 2, "title": "ok", "html_url": "u"}]})
    _install_fake(monkeypatch, fake)
    r = tick.run_catchup()
    assert [rp["repo_pr"] for rp in r["repaired"]] == ["a/x#2"]


def test_the_receipt_lands_in_the_tick_log(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    fake = _FakeApp()
    _install_fake(monkeypatch, fake)
    tick.run_catchup()
    log = home / "willow-bot" / "steward_ticks.jsonl"
    assert log.is_file()
    lines = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert any(ln["event"] == "steward_catchup" for ln in lines)


def test_the_cli_knows_catchup(monkeypatch, home):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    assert tick.main(["catchup"]) == 0


def test_the_loop_wires_catchup_before_audit(monkeypatch):
    """Prove the step is called from ``run_loop`` and runs before audit
    so a newly-repaired PR is dispatched in the same tick."""
    order: list[str] = []

    monkeypatch.setattr(tick, "run_once", lambda *_a, **_k: order.append("once"))
    monkeypatch.setattr(tick, "run_sweep", lambda *_a, **_k: (order.append("sweep") or {"ranges": []}))
    monkeypatch.setattr(tick, "run_resolve", lambda *_a, **_k: order.append("resolve"))
    monkeypatch.setattr(tick, "run_mirror", lambda *_a, **_k: order.append("mirror"))
    monkeypatch.setattr(tick, "run_ci", lambda *_a, **_k: order.append("ci"))
    monkeypatch.setattr(tick, "run_catchup", lambda *_a, **_k: order.append("catchup"))
    monkeypatch.setattr(tick, "run_audit", lambda *_a, **_k: order.append("audit"))
    monkeypatch.setattr("willow_bot.steward.heartbeat.run_heartbeat",
                        lambda *_a, **_k: order.append("heartbeat"))

    class _Stop(Exception):
        pass

    def sleep_once(_):
        if "audit" in order:
            raise _Stop()

    monkeypatch.setattr(tick.time, "sleep", sleep_once)
    with pytest.raises(_Stop):
        tick.run_loop(0.0)

    assert order.index("catchup") < order.index("audit")
