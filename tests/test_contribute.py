"""willow-bot-contribute: any user's projects, next steps and openings. No
network: requests.get is replaced."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from willow_bot import contribute

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _item(repo, number, title="t", **extra):
    return {"repository_url": f"https://api.github.com/repos/{repo}", "number": number,
            "title": title, "html_url": f"https://github.com/{repo}/issues/{number}",
            "user": {"login": "someone"}, **extra}


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise contribute.requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._data


@pytest.fixture
def github(monkeypatch):
    """Answer each search by the first matching substring of its query."""
    answers: dict[str, object] = {}
    seen: list[tuple[str, dict, dict]] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append((url, dict(params or {}), dict(headers or {})))
        if url.endswith("/user"):
            return _Resp({"login": "grace"})
        q = (params or {}).get("q", "")
        for key, ans in answers.items():
            if key in q:
                return ans if isinstance(ans, _Resp) else _Resp({"items": ans})
        return _Resp({"items": []})

    monkeypatch.setattr(contribute.requests, "get", fake_get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    return answers, seen


def test_any_user_gets_their_own_report(github):
    answers, seen = github
    answers["is:pr is:open archived:false author:ada"] = [_item("ada/tool", 1, draft=True)]
    answers["review-requested:ada"] = [_item("big/project", 7)]
    answers["involves:ada"] = [_item("big/project", 3), _item("big/project", 4), _item("ada/tool", 1),
                               _item("small/lib", 9)]
    answers["repo:big/project"] = [_item("big/project", 50, "help us")]

    rep = contribute.report("ada", token="", now=NOW)
    assert rep["user"] == "ada" and not rep["authenticated"]
    assert [p["repo"] for p in rep["projects"]["own"]] == ["ada/tool"]
    assert [p["repo"] for p in rep["projects"]["others"]] == ["big/project", "small/lib"]
    assert rep["next"]["my_open_prs"][0]["draft"] is True
    assert rep["next"]["review_requested"][0]["repo"] == "big/project"
    assert [o["number"] for o in rep["openings"]] == [50]
    # Openings are only looked for in other people's projects.
    assert not any("repo:ada/tool" in p.get("q", "") for _, p, _ in seen)
    # No token: no Authorization header goes out.
    assert all("Authorization" not in h for _, _, h in seen)


def test_the_token_names_the_user_when_none_is_given(github):
    _, seen = github
    rep = contribute.report(token="tok", now=NOW)
    assert rep["user"] == "grace" and rep["authenticated"]
    assert seen[0][2]["Authorization"] == "Bearer tok"


def test_no_user_and_no_token_is_refused(github):
    with pytest.raises(contribute.ContributeError):
        contribute.report(token="", now=NOW)


@pytest.mark.parametrize("bad", ["", "-ada", "a b", "ada repo:evil/x", "x" * 40, "a--b"])
def test_only_a_login_reaches_the_query(bad):
    with pytest.raises(contribute.ContributeError):
        contribute.valid_login(bad)


def test_one_failed_section_does_not_hide_the_rest(github):
    answers, _ = github
    answers["review-requested:ada"] = _Resp({"message": "rate limited"}, status=403)
    answers["assignee:ada"] = [_item("big/project", 2)]
    rep = contribute.report("ada", token="", now=NOW)
    assert "review_requested" in rep["errors"]
    assert rep["next"]["assigned"][0]["number"] == 2


def test_openings_are_capped_without_a_token(github):
    answers, seen = github
    answers["involves:ada"] = [_item(f"org{i}/r", i) for i in range(6)]
    contribute.report("ada", token="", now=NOW)
    assert sum("is:issue is:open no:assignee" in p.get("q", "") for _, p, _ in seen) == 3


def test_cli_text_and_json(github, capsys):
    answers, _ = github
    answers["involves:ada"] = [_item("big/project", 3)]
    assert contribute.main(["--user", "ada"]) == 0
    text = capsys.readouterr().out
    assert "Other people's projects you work on (1)" in text and "big/project (1)" in text
    assert contribute.main(["--user", "ada", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["user"] == "ada"
    assert contribute.main(["--user", "not a login"]) == 2
