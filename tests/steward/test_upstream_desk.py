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
