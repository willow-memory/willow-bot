"""A check run's `pull_requests` lists open PRs whose HEAD matches, so a
fork's check also lists the fork's PR into upstream. Only a PR whose BASE
is the check's own repo is a PR of that repo."""
from __future__ import annotations

import json

import pytest

import router
from integrations import fleet_bridge
from willow_bot import deposits
from willow_bot.check_prs import pr_numbers

FORK = "rudi193-cmd/codebase-memory-mcp"
UPSTREAM = "DeusData/codebase-memory-mcp"
API = "https://api.github.com/repos"


def _pr(number: int, base: str) -> dict:
    return {"number": number, "url": f"{API}/{base}/pulls/{number}",
            "base": {"ref": "main", "repo": {"url": f"{API}/{base}", "name": base.split("/")[1]}},
            "head": {"ref": "fix", "repo": {"url": f"{API}/{FORK}"}}}


def _check(*prs, conclusion="failure") -> dict:
    return {"action": "completed", "repository": {"full_name": FORK}, "sender": {"type": "Bot"},
            "check_run": {"id": 77, "name": "Tests", "status": "completed", "conclusion": conclusion,
                          "head_sha": "a" * 40, "html_url": "https://x/77", "pull_requests": list(prs)}}


def test_only_prs_based_on_the_checks_repo_count():
    check = _check(_pr(1702, UPSTREAM), _pr(3, FORK))["check_run"]
    assert pr_numbers(check, FORK) == [3]
    assert pr_numbers(check, FORK.upper()) == [3]  # GitHub names are case-insensitive


def test_a_fork_check_listing_only_its_upstream_pr_has_no_pr_here():
    assert pr_numbers(_check(_pr(1702, UPSTREAM))["check_run"], FORK) == []


def test_an_entry_naming_no_repo_is_kept():
    assert pr_numbers({"pull_requests": [{"number": 4}]}, FORK) == [4]


def test_the_deposit_files_no_foreign_pr(tmp_path):
    deposits.deposit_from_check_run_payload(_check(_pr(1702, UPSTREAM)))
    rows = [json.loads(ln) for ln in deposits.deposits_jsonl().read_text().splitlines() if ln.strip()]
    assert rows[-1]["repo"] == FORK and rows[-1]["pr_number"] is None


def test_the_bridge_inbox_item_names_no_foreign_pr(tmp_path, monkeypatch):
    monkeypatch.setattr(deposits, "deposit_from_check_run_payload", lambda p: None)
    fleet_bridge.handle("check_run", _check(_pr(1702, UPSTREAM), _pr(3, FORK)))
    inbox = tmp_path / "upstream_steward" / "webhook_inbox"
    [item] = [json.loads(p.read_text()) for p in inbox.glob("*.json")]
    assert item["pr_number"] == 3


@pytest.fixture
def posts(monkeypatch):
    monkeypatch.delenv("FRANK_MODE", raising=False)
    monkeypatch.delenv("PROPHET_MODE", raising=False)
    monkeypatch.setattr(router.fleet_bridge, "handle", lambda *a, **k: None)
    out = []
    return out, (lambda repo, number, body: out.append((repo, number, body)))


def test_the_router_never_counts_or_speaks_on_a_foreign_pr(posts, monkeypatch):
    out, post = posts
    bumped = []
    monkeypatch.setattr(router.sigh, "bump_fail", lambda repo, pr: bumped.append((repo, pr)) or 1)
    router.route("check_run", _check(_pr(1702, UPSTREAM)), post)
    assert bumped == [] and out == []
