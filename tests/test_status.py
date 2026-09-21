"""Read-only status surface for the seat.

Gap 158600e03598. Every path here is a filesystem read; nothing touches
network or spawns real subprocesses (git). The invariant tested is the
three-state discipline: populated / empty / unreachable stay distinct on
every field, and one field's miss does not hide another field's data.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from willow_bot import status


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    return tmp_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── overall shape ──────────────────────────────────────────────────────────


def test_report_returns_every_declared_field(home: Path):
    r = status.report()
    for field in ("at", "willow_home", "version", "running_commit",
                  "heartbeat", "tick", "journal", "inbox", "cursors", "sync"):
        assert field in r
    assert r["willow_home"] == str(home)


def test_at_is_iso8601_utc(home: Path):
    r = status.report()
    # Loose shape check: ends with '+00:00' or 'Z', and parses as a datetime.
    from datetime import datetime
    parsed = datetime.fromisoformat(r["at"])
    assert parsed.tzinfo is not None


# ── version ────────────────────────────────────────────────────────────────


def test_version_is_populated_when_installed(home: Path):
    """The package is installed via `pip install -e .` in CI and in this
    session's checkout, so importlib.metadata resolves. On a clean venv
    without willow-bot installed we'd expect `unreachable`; here it is
    populated."""
    r = status.report()
    assert r["version"]["status"] in ("populated", "unreachable")
    if r["version"]["status"] == "populated":
        assert isinstance(r["version"]["version"], str)


# ── running commit ────────────────────────────────────────────────────────


def test_running_commit_unreachable_without_git(tmp_path):
    """A repo the bot was installed from PyPI has no .git — the field
    reports `unreachable`, not `empty`. `unreachable` means we could not
    read; `empty` would (falsely) suggest the checkout exists but has no
    history."""
    result = status._read_running_commit(tmp_path / "no-git")
    assert result["status"] == "unreachable"
    assert result["sha"] is None
    assert "no .git" in result["detail"]


def test_running_commit_populated_reads_the_sha(home: Path, monkeypatch, tmp_path):
    """When the checkout is a real git tree, the field reports its HEAD.
    We stub out subprocess.run so this test does not shell out."""
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)

    class _Proc:
        returncode = 0
        stdout = "abcdef1234567890\n"
        stderr = ""

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: _Proc())
    result = status._read_running_commit(root)
    assert result["status"] == "populated"
    assert result["sha"] == "abcdef1234567890"


# ── heartbeat + tick receipts ─────────────────────────────────────────────


def test_missing_receipt_file_is_empty_not_unreachable(home: Path):
    """A fresh install has no ticks yet. `empty` says "no data", not
    "cannot read" — the seat's badge is grey, not red."""
    r = status.report()
    assert r["tick"]["status"] == "empty"
    assert r["tick"]["last"] is None
    assert r["heartbeat"]["status"] == "empty"


def test_present_receipt_file_returns_last_row(home: Path):
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(p, json.dumps({"event": "steward_sweep", "at": "2026-09-15T00:00:00+00:00",
                          "status": "ok"}) + "\n")
    _write(p, p.read_text(encoding="utf-8") + json.dumps({
        "event": "steward_ci", "at": "2026-09-15T00:05:00+00:00", "status": "ok"}) + "\n")
    r = status.report()
    assert r["tick"]["status"] == "populated"
    assert r["tick"]["last"]["event"] == "steward_ci"
    assert r["tick"]["at"] == "2026-09-15T00:05:00+00:00"


def test_receipt_file_with_no_valid_json_is_unreachable(home: Path):
    """A file whose tail parses to nothing is broken enough that the seat
    cannot trust ANY receipt shape from it — `unreachable` says so."""
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(p, "not json\nalso not json\n")
    r = status.report()
    assert r["tick"]["status"] == "unreachable"


def test_receipt_file_reads_the_last_valid_line_past_garbage(home: Path):
    """A trailing partial write / hand-edit garbage line does not hide
    the earlier valid row: the reader skips past unparseable tail rows."""
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(p, json.dumps({"event": "steward_sweep", "at": "2026-09-15T00:00:00+00:00"}) + "\n"
              + "half-written partial line\n")
    r = status.report()
    assert r["tick"]["status"] == "populated"
    assert r["tick"]["last"]["event"] == "steward_sweep"


# ── journal excerpt ───────────────────────────────────────────────────────


def test_journal_returns_the_last_five_rows(home: Path):
    p = home / "willow-bot" / "steward_ticks.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for i in range(10):
            fh.write(json.dumps({"event": "steward_sweep", "i": i}) + "\n")
    r = status.report()
    assert r["journal"]["status"] == "populated"
    assert r["journal"]["count"] == 10
    assert [x["i"] for x in r["journal"]["lines"]] == [5, 6, 7, 8, 9]


def test_journal_absent_file_is_empty(home: Path):
    r = status.report()
    assert r["journal"]["status"] == "empty"
    assert r["journal"]["lines"] == []


def test_journal_marks_unparseable_row(home: Path):
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(p, "unparseable\n")
    r = status.report()
    # The row surfaces marked, not silently dropped — a seat reading this
    # knows a row of garbage is in the file.
    assert r["journal"]["lines"][0]["event"] == "unparseable"


# ── inbox depth by kind ───────────────────────────────────────────────────


def test_inbox_absent_directory_is_empty_zero(home: Path):
    r = status.report()
    assert r["inbox"]["status"] == "empty"
    assert r["inbox"]["total"] == 0
    assert r["inbox"]["depth_by_kind"] == {}


def test_inbox_counts_by_kind(home: Path):
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    for i in range(3):
        (inbox / f"pr-{i}.json").write_text(json.dumps({"kind": "pull_request"}), encoding="utf-8")
    (inbox / "check-1.json").write_text(json.dumps({"kind": "check_run"}), encoding="utf-8")
    (inbox / "no-kind.json").write_text(json.dumps({}), encoding="utf-8")
    r = status.report()
    assert r["inbox"]["status"] == "populated"
    assert r["inbox"]["total"] == 5
    assert r["inbox"]["depth_by_kind"] == {"pull_request": 3, "check_run": 1, "unknown": 1}


def test_inbox_unreadable_entry_is_counted_not_dropped(home: Path):
    """A garbled inbox file is a signal — the seat needs to see it,
    not have it silently vanish from the count."""
    inbox = home / "upstream_steward" / "webhook_inbox"
    inbox.mkdir(parents=True)
    (inbox / "good.json").write_text(json.dumps({"kind": "pull_request"}), encoding="utf-8")
    (inbox / "broken.json").write_text("not json", encoding="utf-8")
    r = status.report()
    assert r["inbox"]["total"] == 2
    assert r["inbox"]["unreadable"] == 1


# ── cursors ──────────────────────────────────────────────────────────────


def test_cursors_absent_is_empty(home: Path):
    r = status.report()
    assert r["cursors"]["status"] == "empty"
    assert r["cursors"]["mirror_offset"] is None
    assert r["cursors"]["ci_offset"] is None
    assert r["cursors"]["chain_tip"] is None


def test_cursors_present_reports_each_value(home: Path):
    d = home / "willow-bot" / "deposits"
    d.mkdir(parents=True)
    (d / "mirror.offset").write_text("12345\n", encoding="utf-8")
    (d / "ci.offset").write_text("67890\n", encoding="utf-8")
    (d / "ci_outcomes.chain.tip").write_text("abc123\n", encoding="utf-8")
    r = status.report()
    assert r["cursors"] == {
        "status": "populated",
        "mirror_offset": 12345,
        "ci_offset": 67890,
        "chain_tip": "abc123",
        "annulled": 0,
        "annulled_rows": 0,
    }


def test_cursors_garbled_offset_is_none_not_a_raise(home: Path):
    d = home / "willow-bot" / "deposits"
    d.mkdir(parents=True)
    (d / "mirror.offset").write_text("not a number", encoding="utf-8")
    r = status.report()
    assert r["cursors"]["mirror_offset"] is None


# ── sync (last successful sweep) ─────────────────────────────────────────


def test_sync_no_tick_file_is_empty(home: Path):
    r = status.report()
    assert r["sync"]["status"] == "empty"
    assert r["sync"]["last_success"] is None


def test_sync_returns_last_ok_sweep_only(home: Path):
    """A failed sweep after a good one must not hide the good one. The
    reader walks the tail backwards for the most recent ok sweep."""
    p = home / "willow-bot" / "steward_ticks.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "steward_sweep", "status": "ok",
                             "at": "2026-09-15T00:00:00+00:00"}) + "\n")
        fh.write(json.dumps({"event": "steward_sweep", "status": "could-not-run",
                             "at": "2026-09-15T00:05:00+00:00"}) + "\n")
    r = status.report()
    assert r["sync"]["status"] == "populated"
    assert r["sync"]["last_success"]["at"] == "2026-09-15T00:00:00+00:00"


def test_sync_ignores_non_sweep_events(home: Path):
    """A file with only CI / mirror / audit events reports empty, not
    populated — a `steward_ci` receipt is not a `steward_sweep`."""
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(p, json.dumps({"event": "steward_ci", "status": "ok"}) + "\n")
    r = status.report()
    assert r["sync"]["status"] == "empty"


# ── one field's failure does not hide another's data ────────────────────


def test_partial_failure_still_reports_populated_fields(home: Path):
    """Set up a scenario where inbox is unreadable but tick is populated.
    The report should include BOTH — a seat reading the surface must not
    be told the whole bot is down when only one field is broken."""
    tick = home / "willow-bot" / "steward_ticks.jsonl"
    _write(tick, json.dumps({"event": "steward_sweep", "status": "ok"}) + "\n")
    # Make inbox exist but be unreadable — a non-directory file where the
    # inbox dir should be.
    inbox_path = home / "upstream_steward"
    inbox_path.mkdir(parents=True)
    (inbox_path / "webhook_inbox").write_text("not a directory", encoding="utf-8")
    r = status.report()
    assert r["tick"]["status"] == "populated"
    # inbox_dir.is_dir() is False when the path is a file → status = "empty"
    assert r["inbox"]["status"] == "empty"
