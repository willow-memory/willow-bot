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
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
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


def test_ingest_pull_request_carries_head_sha_through(tmp_path: Path, monkeypatch, capsys) -> None:
    """fleet_bridge now writes head_sha on a pull_request item (gap
    acfd27ae3259, voice sub-part); this step must carry it into both the
    emitted `webhook_pr` line and the stored `webhook_signals` entry, or
    the steward voice step has no sha to key its status comment on."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    item = {
        "source": "willow-bot",
        "received_at": "2026-09-16T00:00:00+00:00",
        "event": "pull_request",
        "repo": "willow-memory/willow-mcp",
        "action": "synchronize",
        "work_id": "wh-willow-memory-willow-mcp-pr-99-cafef00d",
        "kind": "pull_request",
        "number": 99,
        "title": "Test PR",
        "state": "open",
        "merged": False,
        "html_url": "https://github.com/willow-memory/willow-mcp/pull/99",
        "head_sha": "deadbeefcafe",
    }
    (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")
    state = tmp_path / "state.json"
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0]["head_sha"] == "deadbeefcafe"

    saved = json.loads(state.read_text())
    (signal,) = saved["webhook_signals"]
    assert signal["head_sha"] == "deadbeefcafe"


def test_ingest_pull_request_missing_head_sha_is_empty_string(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An older fleet_bridge item written before head_sha existed carries
    none — this step preserves the honest absence, not a raise."""
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    item = {
        "source": "willow-bot",
        "received_at": "2026-09-16T00:00:00+00:00",
        "event": "pull_request",
        "repo": "willow-memory/willow-mcp",
        "action": "opened",
        "work_id": "wh-willow-memory-willow-mcp-pr-100-cafef00e",
        "kind": "pull_request",
        "number": 100,
        "title": "Test PR",
        "state": "open",
        "merged": False,
        "html_url": "https://github.com/willow-memory/willow-mcp/pull/100",
    }
    (inbox / f"{item['work_id']}.json").write_text(json.dumps(item), encoding="utf-8")
    state = tmp_path / "state.json"
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert lines[0]["head_sha"] == ""


def test_call_watcher_flag_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    state = tmp_path / "state.json"
    (tmp_path / "empty").mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
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
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
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
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
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
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
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


def test_ingest_unknown_kind_is_archived_unemitted(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An installation / issue_comment / other-kind row is not consumed
    here — this step names only the kinds it knows how to emit for. No
    step reads those kinds, so the row moves to unhandled/<YYYY-MM>/
    (kept, not emitted) and its work_id stays out of consumed."""
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
    (home).mkdir(parents=True, exist_ok=True)  # the box exists (no default, no bogus path)
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)

    assert ingest(state) == 0
    assert capsys.readouterr().out.strip() == ""
    saved = json.loads(state.read_text())
    assert item["work_id"] not in saved.get("inbox_consumed", [])
    assert not (inbox / f"{item['work_id']}.json").exists()
    assert [p.name for p in (inbox / "unhandled").glob("*/*.json")] == [f"{item['work_id']}.json"]


# ── consumed items leave the inbox ────────────────────────────────────────


def _box(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = tmp_path / "willow"
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("WILLOW_HOME", str(home))
    monkeypatch.delenv("LOKI_PR_WATCH_CALL_WATCHER", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_CALL_WATCHER", raising=False)
    return inbox, tmp_path / "state.json"


def _pr(n: int) -> dict:
    return {"kind": "pull_request", "work_id": f"wh-o-r-pr-{n}-x", "repo": "o/r", "number": n,
            "action": "opened", "received_at": "2026-10-11T00:00:00+00:00"}


def _check(cid: int) -> dict:
    return {"kind": "check_run", "work_id": f"wh-o-r-check-{cid}-x", "repo": "o/r", "check_id": cid,
            "conclusion": "success", "status": "completed", "head_sha": "abc"}


def _put(inbox: Path, item: dict) -> Path:
    p = inbox / f"{item['work_id']}.json"
    p.write_text(json.dumps(item), encoding="utf-8")
    return p


def _emitted(capsys) -> list[dict]:
    return [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]


def test_consumed_items_move_to_the_month_archive(tmp_path, monkeypatch, capsys):
    import time

    inbox, state = _box(tmp_path, monkeypatch)
    _put(inbox, _pr(1))
    _put(inbox, _check(7))
    other = _put(inbox, {"kind": "issue_comment", "work_id": "wh-o-r-comment-1-x"})
    assert ingest(state) == 0
    assert len(_emitted(capsys)) == 2
    stamp = time.strftime("%Y-%m", time.gmtime())
    month = inbox / "consumed" / stamp
    assert sorted(p.name for p in month.iterdir()) == ["wh-o-r-check-7-x.json", "wh-o-r-pr-1-x.json"]
    assert list(inbox.glob("*.json")) == []  # the inbox holds only what waits
    assert [p.name for p in (inbox / "unhandled" / stamp).iterdir()] == [other.name]


def test_a_later_webhook_for_a_consumed_work_id_is_archived_not_re_emitted(tmp_path, monkeypatch, capsys):
    """fleet_bridge keys a PR by its number. Once the first item leaves the
    inbox, a later action for the same PR writes the file again; emitting once
    per work_id is unchanged, and the file still leaves the inbox."""
    inbox, state = _box(tmp_path, monkeypatch)
    _put(inbox, _pr(1))
    ingest(state)
    capsys.readouterr()
    again = _put(inbox, {**_pr(1), "action": "closed"})
    ingest(state)
    assert _emitted(capsys) == []
    assert not again.exists()


def test_the_cap_keeps_the_most_recent_ids_and_nothing_replays(tmp_path, monkeypatch, capsys):
    """The old cap kept the alphabetically last ids and left files in place,
    so every evicted item was emitted again on the next tick."""
    import willow_bot.steward.inbox as inbox_mod

    monkeypatch.setattr(inbox_mod, "_MAX_CONSUMED", 3)
    inbox, state = _box(tmp_path, monkeypatch)
    for n in (5, 4, 3, 2, 1):  # names sort opposite to the order they arrive in
        _put(inbox, _pr(n))
        ingest(state)
    assert len(_emitted(capsys)) == 5
    saved = json.loads(state.read_text())["inbox_consumed"]
    assert saved == ["wh-o-r-pr-3-x", "wh-o-r-pr-2-x", "wh-o-r-pr-1-x"]  # newest three, in order
    for _ in range(3):
        ingest(state)
    assert _emitted(capsys) == []  # nothing left in the inbox to replay


def test_a_failed_move_is_not_emitted_twice(tmp_path, monkeypatch, capsys):
    import willow_bot.steward.inbox as inbox_mod

    inbox, state = _box(tmp_path, monkeypatch)
    stuck = _put(inbox, _pr(1))
    monkeypatch.setattr(inbox_mod, "_archive", lambda *a, **k: False)
    ingest(state)
    ingest(state)
    assert len(_emitted(capsys)) == 1 and stuck.exists()


def test_a_legacy_sorted_consumed_list_is_kept_and_ages_out_first(tmp_path, monkeypatch, capsys):
    """A box upgraded from the old cap: its sorted list is read as-is, those
    items are archived without being emitted, and new ids go after them."""
    inbox, state = _box(tmp_path, monkeypatch)
    old = _put(inbox, _pr(9))
    state.write_text(json.dumps({"inbox_consumed": ["wh-o-r-pr-9-x"]}))
    _put(inbox, _pr(1))
    ingest(state)
    assert [e["repo_pr"] for e in _emitted(capsys)] == ["o/r#1"]
    assert not old.exists()
    assert json.loads(state.read_text())["inbox_consumed"] == ["wh-o-r-pr-9-x", "wh-o-r-pr-1-x"]


def test_status_pending_counts_only_what_is_left(tmp_path, monkeypatch, capsys):
    from willow_bot import status

    inbox, _ = _box(tmp_path, monkeypatch)
    from willow_bot.steward.config import state_path

    _put(inbox, _pr(1))
    _put(inbox, {"kind": "issue", "work_id": "wh-o-r-issue-1-x"})
    ingest(state_path())
    r = status.report()["inbox"]
    assert r["total"] == 0 and r["pending"] == 0  # consumed and unhandled both left
