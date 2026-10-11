"""ci_backfill: open PRs whose head has no CI recorded get GitHub's own check
runs as deposits, once per head, bounded per tick. No network."""
from __future__ import annotations

import json

from willow_bot import deposits
from willow_bot.steward import ci_backfill

SHA = "a" * 40


def _run(i, conclusion="success", status="completed"):
    return {"id": i, "name": f"job{i}", "status": status, "conclusion": conclusion,
            "html_url": f"https://x/{i}", "completed_at": "2026-10-11T04:00:00Z",
            "check_suite": {"head_branch": "feat/x"}}


def _rows():
    p = deposits.deposits_jsonl()
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


def test_a_head_with_nothing_recorded_is_filled_from_github(monkeypatch):
    monkeypatch.setattr(ci_backfill, "_get_pull", lambda repo, n: {"head": {"sha": SHA}})
    monkeypatch.setattr(ci_backfill, "_check_runs", lambda repo, sha: [_run(1), _run(2, "failure"),
                                                                        _run(3, None, "in_progress")])
    r = ci_backfill.run({"open": ["o/r#7"]})
    assert r["filled"] == [{"pr": "o/r#7", "head_sha": SHA[:12], "runs": 2}]
    rows = _rows()
    assert [(x["check_run_id"], x["conclusion"], x["pr_number"], x["source"]) for x in rows] == [
        (1, "success", 7, "backfill"), (2, "failure", 7, "backfill")]
    assert rows[0]["head_branch"] == "feat/x"


def test_a_head_is_done_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_backfill, "_get_pull", lambda repo, n: {"head": {"sha": SHA}})
    monkeypatch.setattr(ci_backfill, "_check_runs", lambda repo, sha: calls.append(sha) or [_run(1)])
    state = {"open": ["o/r#7"], "pr_heads": {"o/r#7": {"head_sha": SHA}}}
    ci_backfill.run(state)
    ci_backfill.run(state)
    assert calls == [SHA] and len(_rows()) == 1


def test_a_recorded_head_is_left_alone(monkeypatch):
    monkeypatch.setattr(ci_backfill, "_get_pull", lambda repo, n: (_ for _ in ()).throw(AssertionError))
    state = {"open": ["o/r#7"], "pr_heads": {"o/r#7": {"head_sha": SHA}},
             "ci_heads": {"o/r#7": {SHA: "2026-10-11T00:00:00Z"}}}
    assert ci_backfill.run(state)["filled"] == []


def test_nothing_finished_yet_is_tried_again(monkeypatch):
    runs = [[_run(1, None, "in_progress")], [_run(1)]]
    monkeypatch.setattr(ci_backfill, "_get_pull", lambda repo, n: {"head": {"sha": SHA}})
    monkeypatch.setattr(ci_backfill, "_check_runs", lambda repo, sha: runs.pop(0))
    assert ci_backfill.run({"open": ["o/r#7"]})["waiting"] == ["o/r#7"]
    assert ci_backfill.run({"open": ["o/r#7"]})["filled"]


def test_bounded_per_tick_and_an_unreadable_repo_is_skipped(monkeypatch):
    def pull(repo, n):
        if repo == "o/gone":
            raise RuntimeError("404")
        return {"head": {"sha": f"{n:040d}"}}

    monkeypatch.setattr(ci_backfill, "_get_pull", pull)
    monkeypatch.setattr(ci_backfill, "_check_runs", lambda repo, sha: [_run(1)])
    state = {"open": ["o/gone#1"] + [f"o/r#{i}" for i in range(2, 20)]}
    r = ci_backfill.run(state, limit=5)
    assert r["status"] == "partial" and r["unreadable"][0]["pr"] == "o/gone#1"
    assert len(r["filled"]) == 4


def test_the_tick_step_reads_the_steward_state(monkeypatch):
    from willow_bot.steward import tick
    from willow_bot.steward.config import state_path

    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps({"open": ["o/r#7"]}))
    monkeypatch.setattr(ci_backfill, "_get_pull", lambda repo, n: {"head": {"sha": SHA}})
    monkeypatch.setattr(ci_backfill, "_check_runs", lambda repo, sha: [_run(1)])
    assert tick.main(["ci-backfill"]) == 0
    assert len(_rows()) == 1
