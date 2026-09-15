"""The resolve step (2026-09-14): a merged commit names the gap it closed.

Gap e278ec952b9c — the backlog cannot record its own closures, so a seat
orienting on ``gap_list`` re-derives fixed problems. willows-grove
INVARIANTS §11 defines the ``Gap-Id: <12 hex>`` trailer; this step reads
it off the ranges the sweep brought home and calls ``gap_resolve``. The
git range is real (a synthetic repo); the MCP client is a fake.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from willow_bot.steward import tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    return tmp_path


class _Client:
    def __init__(self, *, result=None):
        self.calls = []
        self.result = result

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        if callable(self.result):
            return self.result(name, inputs)
        return self.result if self.result is not None else {"ok": True}


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A checkout with a base commit and three commits past it: one naming a
    gap, one naming an idea, one naming nothing — and one with a bad id."""
    r = tmp_path / "clone"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a").write_text("0")
    _git(r, "add", "a")
    _git(r, "commit", "-q", "-m", "base\n\nPersona: hanuman")
    before = _git(r, "rev-parse", "HEAD")
    msgs = [
        "fix: closes it\n\nPersona: hanuman\nGap-Id: e278ec952b9c",
        "feat: idea\n\nPersona: hanuman\nIdea-Id: willow-ideas-127\nGap-Id: 378c2e57c3d0",
        "chore: plain\n\nPersona: hanuman",
        "fix: bad id\n\nPersona: hanuman\nGap-Id: #74",
    ]
    for i, m in enumerate(msgs):
        (r / "a").write_text(str(i + 1))
        _git(r, "commit", "-q", "-am", m)
    after = _git(r, "rev-parse", "HEAD")
    return {"checkout": str(r), "before": before, "after": after, "repo": "o/r"}


def _sweep(*ranges):
    return {"event": "steward_sweep", "status": "ok", "ranges": list(ranges)}


def test_nothing_came_home_is_an_honest_ok(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep())
    assert r["status"] == "ok" and r["resolved"] == [] and r["ranges"] == 0
    assert c.calls == []
    assert tick.run_resolve(None)["status"] == "ok"


def test_absent_when_mcp_is_off_but_says_what_it_found(home, monkeypatch, repo):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(repo))
    assert r["status"] == "absent"
    assert {f["gap_id"] for f in r["found"]} == {"e278ec952b9c", "378c2e57c3d0"}
    assert c.calls == []


def test_resolves_each_well_formed_gap_with_where_it_merged(home, monkeypatch, repo):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(repo))
    assert r["status"] == "ok"
    assert {x["gap_id"] for x in r["resolved"]} == {"e278ec952b9c", "378c2e57c3d0"}
    gap_calls = [i for n, i in c.calls if n == "gap_resolve"]
    assert len(gap_calls) == 2
    for inputs in gap_calls:
        assert inputs["app_id"] == "willow"
        assert inputs["note"].startswith("merged o/r@")
        assert len(inputs["note"].split("@")[1]) == 40
    # the malformed `#74` never reached the tool
    assert not any(i.get("gap_id") == "#74" for _, i in c.calls)
    # the idea landed as a record, in the reconciler's id shape
    assert [i["idea_id"] for i in r["ideas"]] == ["willow-ideas-127"]
    puts = [i for n, i in c.calls if n == "store_put"]
    assert len(puts) == 1 and puts[0]["collection"] == tick.IDEA_LANDINGS
    sha = r["ideas"][0]["sha"]
    assert puts[0]["record_id"] == f"willow-ideas-127:{sha[:12]}"
    rec = puts[0]["record"]
    assert rec["idea_id"] == "willow-ideas-127" and rec["status"] == "landed"
    assert rec["repo"] == "o/r" and rec["sha"] == sha and rec["merged_at"] == r["at"]
    assert r["ideas_stored"] == [puts[0]["record_id"]]


# ── evidence rules match the reconciler's ────────────────────────────────────


def _one_commit_repo(tmp_path, message):
    r = tmp_path / "clone2"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a").write_text("0")
    _git(r, "add", "a")
    _git(r, "commit", "-q", "-m", "base")
    before = _git(r, "rev-parse", "HEAD")
    (r / "a").write_text("1")
    _git(r, "commit", "-q", "-am", message)
    return {"checkout": str(r), "before": before, "after": _git(r, "rev-parse", "HEAD"), "repo": "o/r"}


def test_a_mention_in_prose_is_not_a_trailer(home, monkeypatch, tmp_path):
    """The body explains the convention; only the trailer block carries it."""
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    msg = (
        "docs: explain the join key\n\n"
        "Write the gap in the trailer, like\nGap-Id: aaaaaaaaaaaa\nso the steward can read it.\n\n"
        "Persona: hanuman\nGap-Id: bbbbbbbbbbbb"
    )
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(_one_commit_repo(tmp_path, msg)))
    assert [x["gap_id"] for x in r["resolved"]] == ["bbbbbbbbbbbb"]


def test_trailer_above_the_co_authored_block_still_counts(home, monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    msg = (
        "fix: it\n\nProse.\n\nPersona: hanuman\nGap-Id: cccccccccccc\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>\nClaude-Session: https://x"
    )
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(_one_commit_repo(tmp_path, msg)))
    assert [x["gap_id"] for x in r["resolved"]] == ["cccccccccccc"]


def test_idea_status_partial_and_foreign_id_shapes(home, monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    msg = (
        "feat: half\n\nPersona: hanuman\nIdea-Id: willow-ideas-042\nIdea-Id: willow-ideas-042\n"
        "Idea-Id: willow-mcp-ideas-6.127\nIdea-Status: partial"
    )
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(_one_commit_repo(tmp_path, msg)))
    # one claim per id per commit; the non-reconciler shape is ignored
    assert [(i["idea_id"], i["status"]) for i in r["ideas"]] == [("willow-ideas-042", "partial")]
    assert [n for n, _ in c.calls] == ["store_put"]
    assert r["resolved"] == []


def test_a_refused_landing_is_a_line_and_the_gap_still_resolves(home, monkeypatch, repo):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")

    def answer(name, inputs):
        if name == "store_put":
            return {"error": "collection_denied"}
        return {"ok": True}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(repo))
    assert r["ideas_stored"] == [] and r["ideas_refused"][0]["error"] == "collection_denied"
    assert len(r["resolved"]) == 2


def test_second_run_over_the_same_range_skips(home, monkeypatch, repo):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    tick.run_resolve(_sweep(repo))
    r2 = tick.run_resolve(_sweep(repo))
    assert r2["resolved"] == [] and len(r2["skipped"]) == 2
    assert [n for n, _ in c.calls].count("gap_resolve") == 2
    # the landing record is re-put under the same id — an overwrite, not a duplicate
    puts = [i["record_id"] for n, i in c.calls if n == "store_put"]
    assert len(puts) == 2 and len(set(puts)) == 1
    state = json.loads(state_path().read_text())
    assert set(state["gaps_resolved"]) == {"e278ec952b9c", "378c2e57c3d0"}


def test_a_refusal_is_reported_and_not_marked_resolved(home, monkeypatch, repo):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")

    def answer(name, inputs):
        if inputs.get("gap_id") == "378c2e57c3d0":
            return {"error": "not_found"}
        return {"ok": True}

    c = _Client(result=answer)
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep(repo))
    assert [x["gap_id"] for x in r["resolved"]] == ["e278ec952b9c"]
    assert r["refused"] == [{"gap_id": "378c2e57c3d0",
                             "where": r["refused"][0]["where"], "error": "not_found"}]
    state = json.loads(state_path().read_text())
    assert "378c2e57c3d0" not in state["gaps_resolved"]


def test_a_fresh_branch_has_no_range(home, monkeypatch, repo):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    r = tick.run_resolve(_sweep({**repo, "before": ""}))
    assert r["status"] == "ok" and r["resolved"] == [] and c.calls == []


def test_an_unreadable_checkout_is_a_line_not_a_crash(home, monkeypatch, repo, tmp_path):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    c = _Client()
    _use(monkeypatch, c)
    gone = {**repo, "checkout": str(tmp_path / "nowhere")}
    r = tick.run_resolve(_sweep(gone, repo))
    assert r["unreadable"][0]["repo"] == "o/r"
    assert len(r["resolved"]) == 2  # the readable range still resolved


def test_sweep_receipt_carries_ranges_for_pulled_repos(home, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    swept = [
        {"ok": True, "pulled": True, "repo": "o/a", "checkout": "/x/a", "before": "1", "after": "2"},
        {"ok": True, "pulled": False, "repo": "o/b", "checkout": "/x/b", "before": "3", "after": "3"},
        {"ok": False, "flag": "trigger-o-c.flag", "error": "EBUSY"},
    ]
    c = _Client(result={"ok": True, "present": True, "swept": swept})
    _use(monkeypatch, c)
    r = tick.run_sweep()
    assert r["pulled"] == ["o/a"]
    assert r["ranges"] == [{"repo": "o/a", "checkout": "/x/a", "before": "1", "after": "2"}]
