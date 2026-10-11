import json
import os
from pathlib import Path

import pytest

from willow_bot.steward import upstream_desk


def test_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WILLOW_BOT_UPSTREAM_DESK", raising=False)
    r = upstream_desk.run_upstream_desk()
    assert r["status"] == "absent"


def test_notifications_classified_and_pending_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "willow"
    home.mkdir()
    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", str(state))
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_DESK", "1")
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_WATCH_REPOS", "DeusData/codebase-memory-mcp")

    def fake_gh(path: str):
        assert path.startswith("/notifications")
        return [
            {
                "id": "n1",
                "reason": "review_requested",
                "updated_at": "2026-09-01T12:00:00Z",
                "unread": True,
                "repository": {"full_name": "DeusData/codebase-memory-mcp"},
                "subject": {
                    "type": "PullRequest",
                    "title": "fix registry",
                    "url": "https://api.github.com/repos/DeusData/codebase-memory-mcp/pulls/99",
                },
            }
        ]

    monkeypatch.setattr(upstream_desk, "gh_api", fake_gh)
    monkeypatch.setattr(upstream_desk, "_tracker_every_ticks", lambda: 999)

    r = upstream_desk.run_upstream_desk()
    assert r["status"] == "ok"
    assert r["counts"]["urgent"] == 1
    pending = list((home / "upstream_steward" / "pending").glob("*.json"))
    assert len(pending) == 1
    body = json.loads(pending[0].read_text())
    assert body["lane"] == "urgent"
    assert body["repo"] == "DeusData/codebase-memory-mcp"


def _desk_env(monkeypatch, tmp_path):
    home = tmp_path / "willow"
    home.mkdir()
    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", str(state))
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_DESK", "1")
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_WATCH_REPOS", "DeusData/codebase-memory-mcp")
    monkeypatch.setattr(upstream_desk, "_tracker_every_ticks", lambda: 999)
    return home, state


def _note(i, reason="review_requested"):
    return {"id": f"n{i}", "reason": reason, "updated_at": "2026-09-01T12:00:00Z", "unread": True,
            "repository": {"full_name": "DeusData/codebase-memory-mcp"},
            "subject": {"type": "PullRequest", "title": f"t{i}",
                        "url": f"https://api.github.com/repos/DeusData/codebase-memory-mcp/pulls/{i}"}}


def test_a_failed_poll_does_not_move_the_cursor(monkeypatch, tmp_path):
    import subprocess

    _, state = _desk_env(monkeypatch, tmp_path)

    def broken(path):
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(upstream_desk, "gh_api", broken)
    r = upstream_desk.run_upstream_desk()
    assert r["status"] == "degraded"
    cursor = json.loads(state.read_text())["upstream_desk"]["cursor"]
    assert "last_poll" not in cursor  # the unread window is read again next tick


def test_notifications_are_paged_past_fifty(monkeypatch, tmp_path):
    _desk_env(monkeypatch, tmp_path)

    def paged(path):
        page = int(path.rsplit("page=", 1)[1])
        return [_note(page * 100 + i) for i in range(50 if page == 1 else 3)]

    monkeypatch.setattr(upstream_desk, "gh_api", paged)
    r = upstream_desk.run_upstream_desk()
    assert r["notifications"] == 53


def test_a_failed_urgent_filing_is_retried(monkeypatch, tmp_path):
    from willow_bot.steward import mcp_client

    _desk_env(monkeypatch, tmp_path)
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_FILE_URGENT", "1")
    notes = [[_note(1)], []]
    monkeypatch.setattr(upstream_desk, "gh_api", lambda path: notes.pop(0) if notes else [])
    answers = [{"error": "broker down"}, {"id": "item-9"}]
    monkeypatch.setattr(mcp_client, "call", lambda tool, args: answers.pop(0))
    first = upstream_desk.run_upstream_desk(enable_mcp=True)
    assert first["filed"] == [] and first["file_errors"]
    second = upstream_desk.run_upstream_desk(enable_mcp=True)  # no new notification this tick
    assert [i["work_id"] for i in second["filed"]] == [first["new_items"][0]["work_id"]]


def test_a_failed_search_keeps_the_last_ledger(monkeypatch, tmp_path):
    from willow_bot import contribute

    _desk_env(monkeypatch, tmp_path)
    monkeypatch.setattr(contribute, "open_prs", lambda login, token: [
        {"repo": "DeusData/codebase-memory-mcp", "number": 7, "title": "x", "url": "u",
         "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z", "is_draft": False}])
    assert upstream_desk._refresh_tracker("ada", [])["open_count"] == 1

    def broken(login, token):
        raise RuntimeError("search down")

    monkeypatch.setattr(contribute, "open_prs", broken)
    body = upstream_desk._refresh_tracker("ada", [])
    assert body["open_count"] == 1 and "search down" in body["tracker_error"]
