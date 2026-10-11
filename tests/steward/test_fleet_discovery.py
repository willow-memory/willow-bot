"""fleet: a failed org lookup is not cached, a missing gh is a failed call,
and every open PR is read, not the first page."""
from __future__ import annotations

import subprocess

import pytest

from willow_bot import operator_profile
from willow_bot.steward import fleet


@pytest.fixture
def no_profile(monkeypatch, tmp_path):
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(tmp_path / "none.json"))
    operator_profile.reset()
    fleet.fleet_orgs.cache_clear()
    yield
    fleet.fleet_orgs.cache_clear()
    operator_profile.reset()


def test_a_failed_lookup_is_retried_not_cached(no_profile, monkeypatch):
    calls = []

    def gh(path):
        calls.append(path)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, "gh")
        return [{"organization": {"login": "b-org"}}, {"organization": {"login": "a-org"}}]

    monkeypatch.setattr(fleet, "gh_api", gh)
    assert fleet.fleet_orgs() == ()
    assert "orgs:memberships" in fleet.last_scan_errors
    assert fleet.fleet_orgs() == ("a-org", "b-org")
    assert fleet.fleet_orgs() == ("a-org", "b-org") and len(calls) == 2  # the answer is cached


def test_a_missing_gh_is_a_failed_call_not_a_crash(monkeypatch):
    def no_gh(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(fleet.subprocess, "check_output", no_gh)
    with pytest.raises(subprocess.CalledProcessError):
        fleet.gh_api("/user")


def test_every_open_pr_is_read(monkeypatch):
    monkeypatch.setattr(fleet, "fleet_repos", lambda: ["o/r"])

    def gh(path):
        page = int(path.rsplit("page=", 1)[1])
        n = 100 if page == 1 else 7
        return [{"number": (page - 1) * 100 + i, "title": "", "html_url": ""} for i in range(n)]

    monkeypatch.setattr(fleet, "gh_api", gh)
    assert len(list(fleet.iter_open_pulls())) == 107
