"""integrations/fleet_bridge.py — the check_run inbox item is keyed on the check
id and carries the head sha, so a consumer (the Forge's PR-time deposit) can
file it under the question it answers.

Stdlib + pytest. Every path the bridge writes is redirected to tmp_path; nothing
touches $WILLOW_HOME.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations import fleet_bridge

REPO = "forge-play/Forge"
SHA = "cc9aab19ba2502e14e331e20e699f634fb4cb1a2"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_bridge, "_WILLOW_HOME", tmp_path)
    monkeypatch.setattr(fleet_bridge, "_EVENT_LOG", tmp_path / "willow-bot" / "event-log.jsonl")
    monkeypatch.setattr(fleet_bridge, "_INBOX", tmp_path / "upstream_steward" / "webhook_inbox")
    monkeypatch.setattr(fleet_bridge, "_GITSYNC_TRIGGERS", tmp_path / "gitsync")
    return tmp_path


def _check_run(check_id: int, name: str, conclusion: str, *, pr: int | None = 4,
               sender_type: str = "Bot", sha: str = SHA) -> dict:
    return {
        "action": "completed",
        "repository": {"full_name": REPO},
        "sender": {"login": "renamed-twice[bot]", "type": sender_type},
        "check_run": {
            "id": check_id, "name": name, "status": "completed", "conclusion": conclusion,
            "head_sha": sha, "html_url": f"https://example.invalid/runs/{check_id}",
            "pull_requests": [{"number": pr}] if pr is not None else [],
        },
    }


def _inbox(home: Path) -> list[dict]:
    items = []
    for p in sorted((home / "upstream_steward" / "webhook_inbox").glob("*.json")):
        items.append(json.loads(p.read_text(encoding="utf-8")))
    return items


def test_every_check_on_a_pr_is_filed_not_only_the_first(home):
    fleet_bridge.handle("check_run", _check_run(11, "Tests", "success"))
    fleet_bridge.handle("check_run", _check_run(12, "CodeQL", "success"))
    fleet_bridge.handle("check_run", _check_run(13, "Release Please", "cancelled"))
    items = _inbox(home)
    assert sorted(i["name"] for i in items) == ["CodeQL", "Release Please", "Tests"]
    assert {i["pr_number"] for i in items} == {4}
    assert {i["work_id"] for i in items} == {
        fleet_bridge._work_id(REPO, "check", 11),
        fleet_bridge._work_id(REPO, "check", 12),
        fleet_bridge._work_id(REPO, "check", 13),
    }


def test_the_item_carries_what_a_consumer_keys_on(home):
    fleet_bridge.handle("check_run", _check_run(11, "Tests", "failure"))
    (item,) = _inbox(home)
    assert item["kind"] == "check_run"
    assert item["head_sha"] == SHA
    assert item["check_id"] == 11
    assert item["status"] == "completed" and item["conclusion"] == "failure"
    assert item["sender_type"] == "Bot"
    assert "login" not in json.dumps(item), "an actor is a type, never a login"
    assert item["repo"] == REPO and item["event"] == "check_run"


def test_the_same_completion_delivered_twice_is_one_item(home):
    fleet_bridge.handle("check_run", _check_run(11, "Tests", "success"))
    fleet_bridge.handle("check_run", _check_run(11, "Tests", "success"))
    assert len(_inbox(home)) == 1


def test_a_check_with_no_pr_is_still_keyed_by_sha(home):
    fleet_bridge.handle("check_run", _check_run(21, "Tests", "success", pr=None))
    (item,) = _inbox(home)
    assert item["pr_number"] is None and item["head_sha"] == SHA
    assert item["work_id"] == fleet_bridge._work_id(REPO, "check", 21)


def test_not_completed_is_not_filed_but_is_logged(home):
    payload = _check_run(31, "Tests", "")
    payload["action"] = "created"
    payload["check_run"]["status"] = "in_progress"
    fleet_bridge.handle("check_run", payload)
    assert _inbox(home) == []
    log_lines = (home / "willow-bot" / "event-log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1 and json.loads(log_lines[0])["action"] == "created"


def test_sender_type_is_empty_not_guessed_when_absent(home):
    payload = _check_run(41, "Tests", "success")
    del payload["sender"]
    fleet_bridge.handle("check_run", payload)
    (item,) = _inbox(home)
    assert item["sender_type"] == ""


# ── pull_request: head_sha carried into the queued item ─────────────────


def _pull_request(number: int, action: str, *, sha: str = SHA) -> dict:
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": number,
            "title": "A PR",
            "state": "open",
            "merged": False,
            "user": {"login": "someone"},
            "html_url": f"https://example.invalid/pull/{number}",
            "head": {"sha": sha},
        },
    }


def test_pull_request_item_carries_head_sha(home):
    """Gap acfd27ae3259 (voice sub-part): the steward voice step needs a
    head_sha to key its status comment on, and this is where it starts —
    `payload['pull_request']['head']['sha']` copied straight through."""
    fleet_bridge.handle("pull_request", _pull_request(7, "opened"))
    (item,) = _inbox(home)
    assert item["kind"] == "pull_request"
    assert item["head_sha"] == SHA


def test_pull_request_head_sha_absent_is_empty_string_not_missing(home):
    payload = _pull_request(7, "opened")
    del payload["pull_request"]["head"]
    fleet_bridge.handle("pull_request", payload)
    (item,) = _inbox(home)
    assert item["head_sha"] == ""


def test_synchronize_carries_the_new_head_sha(home):
    """A force-push (`synchronize`) is a queued item too, and its head_sha
    is the NEW head — the voice step keys a fresh comment off this."""
    new_sha = "1111111111111111111111111111111111111111"
    fleet_bridge.handle("pull_request", _pull_request(7, "synchronize", sha=new_sha))
    (item,) = _inbox(home)
    assert item["head_sha"] == new_sha


# ── repository.full_name is validated before it touches a path ──────────────
# CodeQL py/path-injection on #28: the webhook's repo name became part of the
# gitsync trigger filename and the clone lookup. The HMAC already says the
# payload is GitHub's; these say the value is a repo name and nothing else.


@pytest.mark.parametrize("name", [
    "willow-memory/willow-bot",
    "Die-Namic-Systems/Nestor",
    "a/b",
    "rudi193-cmd/Willow",
    "org_1/repo.name-v2",
])
def test_valid_repo_full_names_pass(name):
    assert fleet_bridge._valid_repo_full_name(name) == name


@pytest.mark.parametrize("name", [
    "",
    "no-slash",
    "../../etc/passwd",
    "owner/../escape",
    "owner/.hidden",
    "owner/name/extra",
    "own er/name",
    "-owner/name",
    "owner/",
    "/name",
    None,
    42,
    "o" * 200 + "/name",
])
def test_invalid_repo_full_names_are_empty_not_cleaned(name):
    assert fleet_bridge._valid_repo_full_name(name) == ""


def test_push_with_a_malformed_repo_name_writes_no_trigger(home):
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "../../gitsync-escape"},
        "sender": {"type": "User"},
    }
    fleet_bridge.handle("push", payload)
    triggers = fleet_bridge._GITSYNC_TRIGGERS
    assert not triggers.exists() or list(triggers.iterdir()) == []


def test_trigger_flag_name_is_built_from_owner_and_name(home, monkeypatch, tmp_path):
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.setattr(fleet_bridge, "_local_clone_path_with_layout",
                        lambda _repo: (clone, "org"))
    fleet_bridge.handle("push", {
        "ref": "refs/heads/main",
        "repository": {"full_name": "willow-memory/willow-bot"},
        "sender": {"type": "User"},
    })
    (flag,) = list(fleet_bridge._GITSYNC_TRIGGERS.iterdir())
    assert flag.name == "trigger-willow-memory-willow-bot.flag"
    assert fleet_bridge._under(fleet_bridge._GITSYNC_TRIGGERS, flag)


def test_under_refuses_a_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert fleet_bridge._under(root, root / "x" / "y")
    assert not fleet_bridge._under(root, tmp_path / "elsewhere")
    assert not fleet_bridge._under(root, root / ".." / "elsewhere")
