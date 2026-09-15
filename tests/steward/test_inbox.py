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


def _check_run_item(*, cid: int, conclusion: str | None, pr_number: int | None = 76) -> dict:
    return {
        "source": "willow-bot",
        "received_at": "2026-09-15T00:00:00+00:00",
        "event": "check_run",
        "repo": "willow-memory/willow-mcp",
        "action": "completed",
        "work_id": f"wh-willow-memory-willow-mcp-check-{cid}-abcdef01",
        "kind": "check_run",
        "check_id": cid,
        "head_sha": f"deadbeef{cid:04d}",
        "check_name": f"Tests-{cid}",
        "name": f"Tests-{cid}",  # fleet_bridge writes both
        "conclusion": conclusion,
        "status": "completed",
        "pr_number": pr_number,
        "html_url": f"https://github.com/willow-memory/willow-mcp/actions/runs/{cid}",
        "sender_type": "Bot",
    }


def test_ingest_check_run_emits_and_consumes_every_terminal_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Gap 1d737ffa2595: check_run items were dropped. Every terminal state
    GitHub asserts must round-trip verbatim into a `webhook_check_run`
    line, and the work_id must land in `inbox_consumed` so a redelivery
    (or a re-tick reading the same inbox file) does not emit twice."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)

    terminals = [
        "success", "failure", "timed_out", "cancelled",
        "skipped", "stale", "neutral", "action_required",
    ]
    for i, concl in enumerate(terminals, start=1):
        item = _check_run_item(cid=i, conclusion=concl)
        (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")

    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]

    kinds = {ln["event"] for ln in lines}
    assert kinds == {"webhook_check_run"}, f"unexpected events: {kinds}"

    conclusions = sorted(ln["conclusion"] for ln in lines)
    assert conclusions == sorted(terminals), (
        f"one line per terminal state, verbatim: got {conclusions}"
    )

    # Fields the tick's journal needs to name the leg without a follow-up read.
    for ln in lines:
        assert ln["repo"] == "willow-memory/willow-mcp"
        assert ln["pr_number"] == 76
        assert ln["head_sha"].startswith("deadbeef")
        assert ln["check_name"].startswith("Tests-")
        assert ln["url"].startswith("https://github.com/")
        assert ln["work_id"].startswith("wh-willow-memory-willow-mcp-check-")

    saved = json.loads(state.read_text())
    for i in range(1, len(terminals) + 1):
        assert f"wh-willow-memory-willow-mcp-check-{i}-abcdef01" in saved["inbox_consumed"]

    # A re-run over the same inbox emits nothing new — the dedup key is the
    # work_id in inbox_consumed, same as the pull_request path.
    assert ingest(state) == 0
    assert capsys.readouterr().out.strip() == ""


def test_ingest_check_run_preserves_null_conclusion(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A completed check with no conclusion (rare but legal on GitHub) stays
    as `null`, not coerced to a string — the seat needs to tell "absent"
    apart from "neutral"."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    item = _check_run_item(cid=42, conclusion=None)
    (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")

    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "webhook_check_run"
    assert lines[0]["conclusion"] is None


def test_ingest_check_run_and_pull_request_share_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Both kinds consume from the same inbox and share `inbox_consumed`;
    a pull_request signal remains distinguishable from a check_run signal
    in webhook_signals (check_run entries carry `kind: check_run`)."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)

    pr_item = {
        "source": "willow-bot",
        "received_at": "2026-09-15T00:00:00+00:00",
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
    check_item = _check_run_item(cid=1, conclusion="failure", pr_number=99)
    (inbox / f"{pr_item['work_id']}.json").write_text(json.dumps(pr_item), encoding="utf-8")
    (inbox / f"{check_item['work_id']}.json").write_text(json.dumps(check_item), encoding="utf-8")

    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    events = sorted(ln["event"] for ln in lines)
    assert events == ["webhook_check_run", "webhook_pr"]

    saved = json.loads(state.read_text())
    assert pr_item["work_id"] in saved["inbox_consumed"]
    assert check_item["work_id"] in saved["inbox_consumed"]

    signals = saved["webhook_signals"]
    pr_signals = [s for s in signals if s.get("repo_pr")]
    check_signals = [s for s in signals if s.get("kind") == "check_run"]
    assert len(pr_signals) == 1
    assert len(check_signals) == 1
    assert check_signals[0]["conclusion"] == "failure"


def test_ingest_unknown_kind_stays_in_inbox(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An installation / issue_comment / other-kind row is not consumed
    here — this step names only the kinds it knows how to emit for. The
    row stays for a later step and its work_id stays out of consumed."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    item = {
        "source": "willow-bot",
        "received_at": "2026-09-15T00:00:00+00:00",
        "event": "installation",
        "repo": "",
        "action": "created",
        "work_id": "wh-app-installation-created-cafe0000",
        "kind": "installation",
    }
    (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")

    state = tmp_path / "state.json"
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    assert capsys.readouterr().out.strip() == ""
    saved = json.loads(state.read_text())
    assert item["work_id"] not in saved.get("inbox_consumed", [])
