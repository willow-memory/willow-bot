"""Draft CI deposits — local JSONL, no MCP required."""
from __future__ import annotations

import json
from pathlib import Path

from willow_bot.deposits import (
    ci_outcome_record,
    deposit_ci_outcome,
    deposit_from_check_run_payload,
    deposits_jsonl,
)


def test_append_failure_and_success(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "willow"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)

    fail = ci_outcome_record(
        repo="willow-memory/willow-mcp",
        head_sha="abc123deadbeef",
        check_run_id=42,
        check_name="tests",
        conclusion="failure",
        sender_type="Bot",
    )
    ok = deposit_ci_outcome(fail)
    assert ok["mcp"]["status"] == "skipped"
    path = deposits_jsonl()
    assert path.is_file()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["conclusion"] == "failure"
    assert row["lane"] == "draft"
    assert row["deposited_by"] == "willows-bot"
    assert row["actor_type"] == "Bot"

    success = ci_outcome_record(
        repo="willow-memory/willow-mcp",
        head_sha="abc123deadbeef",
        check_run_id=43,
        check_name="tests",
        conclusion="success",
        sender_type="Bot",
    )
    deposit_ci_outcome(success)
    assert len(path.read_text().strip().splitlines()) == 2


def test_from_webhook_payload(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "willow"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    payload = {
        "action": "completed",
        "sender": {"type": "Bot"},
        "repository": {"full_name": "forge-play/Forge"},
        "check_run": {
            "id": 99,
            "name": "CI",
            "head_sha": "ffff",
            "status": "completed",
            "conclusion": "timed_out",
            "html_url": "https://example/check/99",
            "pull_requests": [{"number": 7}],
        },
    }
    out = deposit_from_check_run_payload(payload)
    assert out is not None
    row = json.loads(deposits_jsonl().read_text().strip().splitlines()[-1])
    assert row["conclusion"] == "timed_out"
    assert row["pr_number"] == 7


def test_heartbeat_absent_without_mcp(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "w"))
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    from willow_bot.steward.heartbeat import run_heartbeat

    r = run_heartbeat()
    assert r["status"] == "absent"
    assert "dew" not in json.dumps(r).lower()
