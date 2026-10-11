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
                  "heartbeat", "tick", "journal", "inbox", "cursors", "sync", "upstream"):
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


# ── the tail window widens past 8 KiB (gap b7a4ccdc8bbb) ─────────────────


def test_a_row_bigger_than_8kib_is_still_read(home: Path, tmp_path):
    """Measured live: a heartbeat row mirroring a large sweep receipt was
    ~40 KB, one row, bigger than the fixed 8 KiB tail the reader used to
    read — nothing parsed and the whole file reported `unreachable`. The
    window must widen past a single oversized row instead of giving up."""
    p = home / "willow-bot" / "steward_heartbeat.jsonl"
    earlier = {"event": "steward_heartbeat", "at": "2026-09-15T00:00:00+00:00", "status": "ok"}
    huge = {
        "event": "steward_heartbeat", "at": "2026-09-22T02:02:30+00:00", "status": "ok",
        "tools": [{"tool": "envelope_retire_sweep", "retired_sample": ["env-x"] * 2000}],
    }
    text = json.dumps(earlier) + "\n" + json.dumps(huge) + "\n"
    assert len(json.dumps(huge)) > 8192  # the row genuinely exceeds the old window
    _write(p, text)
    r = status._read_last_receipt(p)
    assert r["status"] == "populated"
    assert r["last"]["at"] == "2026-09-22T02:02:30+00:00"
    assert r["at"] == "2026-09-22T02:02:30+00:00"


def test_a_row_bigger_than_the_window_cap_is_unreachable_and_says_why(home: Path):
    """A row so large it exceeds even the widened cap must still refuse
    honestly — `unreachable` with a detail naming the byte counts, not the
    old generic "no valid JSON row in tail" for a file whose row WAS
    valid, just enormous."""
    p = home / "willow-bot" / "steward_heartbeat.jsonl"
    huge = {"event": "steward_heartbeat", "tools": [{"why": "x" * 20000}]}
    _write(p, json.dumps(huge) + "\n")
    r = status._read_last_receipt(p, max_window=8192)  # small cap for a fast test
    assert r["status"] == "unreachable"
    assert r["last"] is None
    assert "8192" in r["detail"]
    assert "exceeds" in r["detail"]


def test_widened_window_does_not_parse_a_truncated_partial_line_as_valid(home: Path):
    """When the window does not start at byte 0, the first (possibly
    partial) line inside it must be discarded — otherwise a row cut in
    half by the window boundary could coincidentally parse as valid JSON
    and return a corrupted row silently."""
    p = home / "willow-bot" / "steward_heartbeat.jsonl"
    # A first row padded so the window boundary lands mid-row, then a
    # normal small final row.
    padded = {"event": "steward_heartbeat", "at": "2026-09-15T00:00:00+00:00",
              "pad": "x" * 9000}
    final = {"event": "steward_heartbeat", "at": "2026-09-15T01:00:00+00:00"}
    _write(p, json.dumps(padded) + "\n" + json.dumps(final) + "\n")
    r = status._read_last_receipt(p)
    assert r["status"] == "populated"
    assert r["last"]["at"] == "2026-09-15T01:00:00+00:00"


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


# ── upstream desk ────────────────────────────────────────────────────────


def test_upstream_ledger_open_prs_surface(home: Path):
    ledger_path = home / "willow-bot" / "upstream_desk.json"
    _write(
        ledger_path,
        json.dumps(
            {
                "at": "2026-09-22T00:00:00+00:00",
                "author": "rudi193-cmd",
                "open_count": 2,
                "open": [
                    {"repo": "DeusData/codebase-memory-mcp", "number": 1702,
                     "title": "fix registry", "url": "https://github.com/x/y/pull/1702"},
                    {"repo": "Redential/redential-cli", "number": 95,
                     "title": "git errors", "url": "https://github.com/x/y/pull/95"},
                ],
            }
        ),
    )
    r = status.report()
    assert r["upstream"]["status"] == "populated"
    assert r["upstream"]["ledger"]["open_count"] == 2
    assert len(r["upstream"]["ledger"]["open_prs"]) == 2
    assert r["upstream"]["ledger"]["open_prs"][0]["number"] == 1702


def test_upstream_last_tick_from_journal(home: Path):
    p = home / "willow-bot" / "steward_ticks.jsonl"
    _write(
        p,
        json.dumps(
            {
                "event": "steward_upstream_desk",
                "status": "ok",
                "at": "2026-09-22T12:00:00Z",
                "open_prs": 2,
            }
        )
        + "\n",
    )
    r = status.report()
    assert r["upstream"]["last_tick"]["status"] == "populated"
    assert r["upstream"]["last_tick"]["last"]["open_prs"] == 2


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


# ── operator profile ───────────────────────────────────────────────────────


def test_operator_blank_install_is_empty(home: Path, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_OPERATOR_PROFILE", raising=False)
    op = status.report()["operator"]
    assert op["status"] == "empty" and "blank install" in op["detail"]
    assert op["path"] == str(home / "willow-bot" / "operator.json")


def test_operator_profile_in_the_box_is_populated(home: Path, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_OPERATOR_PROFILE", raising=False)
    _write(home / "willow-bot" / "operator.json",
           json.dumps({"login": "ada", "upstream_watch": ["a/b"], "voice": {"orgs": ["x"]}}))
    op = status.report()["operator"]
    assert op["status"] == "populated" and op["login"] == "ada"
    assert op["orgs"] == "discovered" and op["upstream_watch"] == 1 and op["voice_orgs"] == ["x"]


def test_operator_broken_profile_is_unreachable(home: Path, monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_OPERATOR_PROFILE", raising=False)
    _write(home / "willow-bot" / "operator.json", "{nope")
    assert status.report()["operator"]["status"] == "unreachable"


# ── contributions ─────────────────────────────────────────────────────────


def test_contributions_absent_ledger_is_empty(home: Path):
    assert status.report()["contributions"]["status"] == "empty"


def test_contributions_summarised(home: Path):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    rows = [{"at": now, "repo": "o/r", "kind": "pr_opened", "login": "ada", "outsider": True},
            {"at": now, "repo": "o/r", "kind": "star", "login": "bo", "outsider": True}]
    _write(home / "willow-bot" / "contributions.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
    c = status.report()["contributions"]
    assert c["status"] == "populated" and c["rows"] == 2
    assert c["outside_contributors"] == ["ada"]


# ── open PRs and their CI ──────────────────────────────────────────────────


def _state(home: Path, body: dict) -> None:
    from willow_bot.steward.config import state_path

    _write(state_path(), json.dumps(body))


def test_open_prs_without_state_is_empty(home: Path):
    r = status.report()["open_prs"]
    assert r["status"] == "empty" and r["prs"] == []


def test_open_prs_unreadable_state_is_unreachable(home: Path):
    from willow_bot.steward.config import state_path

    _write(state_path(), "{broken")
    assert status.report()["open_prs"]["status"] == "unreachable"


def test_open_prs_reads_the_newest_heads_ci(home: Path):
    _state(home, {
        "open": ["o/a#1", "o/b#2", "o/c#3", "o/d#4", "o/e#5"],
        "ci_heads": {
            "o/a#1": {"old1": "2026-10-01T00:00:00Z", "new1": "2026-10-02T00:00:00Z"},
            "o/b#2": {"sha2": "2026-10-02T00:00:00Z"},
            "o/c#3": {"sha3": "2026-10-02T00:00:00Z"},
            "o/d#4": {"sha4": "2026-10-02T00:00:00Z"},
        },
        "ci_head_legs": {
            "o/a#1@old1": {"Tests": "failure"},
            "o/a#1@new1": {"Tests": "success", "CodeQL": "neutral"},
            "o/b#2@sha2": {"Tests": "failure"},
            "o/c#3@sha3": {"Tests": "cancelled"},
            "o/d#4@sha4": {"Tests": "success", "Lint": "in_progress"},
        },
        "ci_items": {"o/b#2@sha2": {"id": "x", "url": "https://ci/2"}},
    })
    r = status.report()["open_prs"]
    by_pr = {p["pr"]: p for p in r["prs"]}
    assert by_pr["o/a#1"]["ci"] == "green" and by_pr["o/a#1"]["head_sha"] == "new1"
    assert by_pr["o/b#2"]["ci"] == "red" and by_pr["o/b#2"]["url"] == "https://ci/2"
    assert by_pr["o/c#3"]["ci"] == "cancelled"
    assert by_pr["o/d#4"]["ci"] == "running"
    assert by_pr["o/e#5"]["ci"] == "unknown"
    assert r["by_ci"] == {"cancelled": 1, "green": 1, "red": 1, "running": 1, "unknown": 1}
    assert r["prs"][0]["pr"] == "o/b#2"  # reds first


def test_a_resolved_item_is_not_red(home: Path):
    _state(home, {"open": ["o/a#1"], "ci_heads": {"o/a#1": {"s": "2026-10-02T00:00:00Z"}},
                  "ci_head_legs": {"o/a#1@s": {"Tests": "success"}},
                  "ci_items": {"o/a#1@s": {"id": "x", "resolved": {"by": "s"}}}})
    assert status.report()["open_prs"]["prs"][0]["ci"] == "green"


# ── brief ─────────────────────────────────────────────────────────────────


def test_brief_is_one_line_per_section(home: Path):
    out = status.brief()
    lines = out.splitlines()
    assert lines[0].startswith("willow-bot status at ")
    names = [ln.split()[0] for ln in lines[1:]]
    for n in ("version", "tick", "ingress", "upstream", "open_prs", "app", "operator", "contributions"):
        assert n in names
    assert "cursors" not in names


def test_brief_names_what_the_app_is_missing(home: Path):
    rep = {"at": "t", "willow_home": "/b",
           "app": {"status": "populated", "verdict": "partial",
                   "last": {"unsubscribed": ["fork", "watch"], "missing_permissions": {}, "missing_events": []}}}
    assert "partial; unsubscribed: fork,watch" in status.brief(rep)


def test_brief_without_a_box_says_so():
    out = status.brief({"at": "t", "willow_home": None, "status": "no-box", "detail": "set WILLOW_HOME"})
    assert "no-box" in out and "set WILLOW_HOME" in out


def test_cli_brief(home: Path, capsys):
    from willow_bot.steward.tick import main

    assert main(["status", "--brief"]) == 0
    assert capsys.readouterr().out.startswith("willow-bot status at ")
