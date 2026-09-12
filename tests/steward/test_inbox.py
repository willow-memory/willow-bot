"""Steward inbox — fleet_bridge → watch state (no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from willow_bot.steward.inbox import ingest


def test_ingest_pull_request_emits_once(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    item = {
        "source": "willow-bot",
        "received_at": "2026-09-12T00:00:00+00:00",
        "event": "pull_request",
        "repo": "willow-memory/willow-mcp",
        "action": "opened",
        "work_id": "wh-willow-memory-willow-mcp-pr-99-deadbeef",
        "kind": "pull_request",
        "number": 99,
        "title": "Test PR",
        "state": "open",
        "merged": False,
        "html_url": "https://github.com/willow-memory/willow-mcp/pull/99",
    }
    (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")
    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "webhook_pr"
    assert lines[0]["repo_pr"] == "willow-memory/willow-mcp#99"

    saved = json.loads(state.read_text())
    assert item["work_id"] in saved["inbox_consumed"]

    assert ingest(state) == 0
    assert capsys.readouterr().out.strip() == ""


def test_call_watcher_flag_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("LOKI_PR_WATCH_CALL_WATCHER", "1")
    assert ingest(state) == 1
    err = json.loads(capsys.readouterr().out.strip())
    assert err["event"] == "error"
    assert "CALL_WATCHER" in err["detail"]
