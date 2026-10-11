"""The contributions ledger, the router events that feed it, and the App's
read helpers. No network: requests.get is replaced where it would be called."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import github_app
import router
from willow_bot import contributions

REPO = "willow-memory/willow-bot"


def _pr_event(action, *, login="ada", assoc="CONTRIBUTOR", typ="User", merged=False):
    return {"action": action, "repository": {"full_name": REPO},
            "pull_request": {"number": 5, "title": "Fix a thing", "html_url": "https://x/5",
                             "merged": merged, "author_association": assoc,
                             "user": {"login": login, "type": typ}}}


# ── extract ──────────────────────────────────────────────────────────────────

def test_an_outside_pr_is_an_outsider_row():
    row = contributions.extract("pull_request", _pr_event("opened"))
    assert row["kind"] == "pr_opened" and row["login"] == "ada"
    assert row["outsider"] and not row["bot"] and not row["first_timer"]


def test_a_merge_is_its_own_kind():
    assert contributions.extract("pull_request", _pr_event("closed", merged=True))["kind"] == "pr_merged"
    assert contributions.extract("pull_request", _pr_event("closed"))["kind"] == "pr_closed"


def test_insiders_and_bots_are_not_outsiders():
    assert not contributions.extract("pull_request", _pr_event("opened", assoc="OWNER"))["outsider"]
    bot = contributions.extract("pull_request", _pr_event("opened", login="x[bot]", assoc="NONE", typ="Bot"))
    assert bot["bot"] and not bot["outsider"]


def test_first_timers_are_marked():
    row = contributions.extract("pull_request", _pr_event("opened", assoc="FIRST_TIME_CONTRIBUTOR"))
    assert row["first_timer"]


def test_a_comment_on_a_pr_is_a_pr_comment_and_carries_no_body():
    payload = {"action": "created", "repository": {"full_name": REPO},
               "issue": {"number": 3, "title": "t", "pull_request": {}},
               "comment": {"body": "secret-ish text", "user": {"login": "bo", "type": "User"},
                           "author_association": "NONE", "html_url": "https://x/c"}}
    row = contributions.extract("issue_comment", payload)
    assert row["kind"] == "pr_comment"
    assert "secret-ish text" not in json.dumps(row)


@pytest.mark.parametrize("event,payload,kind", [
    ("fork", {"forkee": {"full_name": "bo/willow-bot"}, "sender": {"login": "bo", "type": "User"}}, "fork"),
    ("watch", {"action": "started", "sender": {"login": "bo", "type": "User"}}, "star"),
    ("release", {"action": "published", "release": {"tag_name": "v1", "author": {"login": "s"}}}, "release"),
    ("discussion", {"action": "created", "discussion": {"number": 1, "user": {"login": "bo"}}}, "discussion"),
])
def test_interest_signals_are_rows(event, payload, kind):
    payload = {**payload, "repository": {"full_name": REPO}}
    assert contributions.extract(event, payload)["kind"] == kind


def test_non_contributions_are_ignored():
    assert contributions.extract("push", {"repository": {"full_name": REPO}}) is None
    assert contributions.extract("pull_request", _pr_event("synchronize")) is None
    assert contributions.extract("watch", {"action": "deleted"}) is None


# ── record + summary ────────────────────────────────────────────────────────

def test_record_appends_and_summary_counts(tmp_path):
    contributions.record("pull_request", _pr_event("opened"))
    contributions.record("pull_request", _pr_event("closed", merged=True))
    contributions.record("pull_request", _pr_event("opened", login="sean", assoc="OWNER"))
    contributions.record("pull_request", _pr_event("opened", login="d[bot]", typ="Bot", assoc="NONE"))
    contributions.record("watch", {"action": "started", "repository": {"full_name": REPO},
                                   "sender": {"login": "eve", "type": "User"}})
    lines = contributions.ledger_path().read_text().splitlines()
    assert len(lines) == 5

    s = contributions.summary()
    assert s["rows"] == 5
    assert s["by_kind"] == {"pr_opened": 3, "pr_merged": 1, "star": 1}
    assert [p["login"] for p in s["people"]] == ["ada", "eve", "sean"]  # bot left out
    # A star is interest, not a contribution.
    assert [p["login"] for p in s["outside_contributors"]] == ["ada"]
    assert s["outside_contributors"][0]["kinds"] == {"pr_opened": 1, "pr_merged": 1}


def test_summary_window_and_repo_filter(tmp_path):
    path = tmp_path / "ledger.jsonl"
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    rows = [
        {"at": old, "repo": REPO, "kind": "pr_opened", "login": "a"},
        {"at": new, "repo": REPO, "kind": "pr_opened", "login": "b"},
        {"at": new, "repo": "other/repo", "kind": "pr_opened", "login": "c"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{torn")
    assert contributions.summary(path=path, now=now)["rows"] == 2
    assert contributions.summary(path=path, now=now, days=None)["rows"] == 3
    assert contributions.summary(path=path, now=now, repo=REPO)["rows"] == 1


def test_the_steward_subcommand_prints_json(capsys):
    from willow_bot.steward.tick import main

    contributions.record("pull_request", _pr_event("opened"))
    assert main(["contributions", "--all"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1


# ── router ───────────────────────────────────────────────────────────────────

@pytest.fixture
def posts(monkeypatch):
    monkeypatch.setattr(router.fleet_bridge, "handle", lambda *a, **k: None)
    out = []
    return out, (lambda repo, number, body: out.append((repo, number, body)))


def test_route_feeds_the_ledger(posts):
    _, post = posts
    router.route("watch", {"action": "started", "repository": {"full_name": REPO},
                           "sender": {"login": "eve", "type": "User"}}, post)
    assert contributions.summary()["by_kind"] == {"star": 1}


def test_a_broken_ledger_never_breaks_the_webhook(posts, monkeypatch):
    out, post = posts

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(router.contributions, "record", boom)
    router.route("release", {"action": "published", "repository": {"full_name": REPO},
                             "release": {"tag_name": "v1"}}, post)
    assert out == []


@pytest.mark.parametrize("event,payload", [
    ("fork", {"forkee": {"full_name": "bo/x"}, "sender": {"login": "bo"}}),
    ("issue_comment", {"action": "created", "issue": {"number": 1},
                       "comment": {"user": {"login": "bo", "type": "User"}, "author_association": "NONE"}}),
    ("pull_request_review_comment", {"action": "created", "pull_request": {"number": 1},
                                     "comment": {"user": {"login": "bo", "type": "User"}}}),
    ("workflow_run", {"action": "completed", "workflow_run": {"head_branch": "main", "event": "push",
                                                             "conclusion": "failure", "name": "Tests"}}),
    ("release", {"action": "published", "release": {"tag_name": "v1"}}),
    ("watch", {"action": "started", "sender": {"login": "bo"}}),
    ("discussion", {"action": "created", "discussion": {"number": 2, "user": {"login": "bo"}}}),
])
def test_new_events_are_handled_and_post_nothing(posts, event, payload, caplog):
    out, post = posts
    caplog.set_level("INFO", logger="willow-bot.router")
    router.route(event, {**payload, "repository": {"full_name": REPO, "default_branch": "main"}}, post)
    assert out == []
    assert not any("unhandled event" in r.message for r in caplog.records)
    assert any(REPO in r.getMessage() for r in caplog.records)


def test_a_red_workflow_off_the_default_branch_is_not_logged(posts, caplog):
    _, post = posts
    caplog.set_level("INFO", logger="willow-bot.router")
    router.route("workflow_run", {"action": "completed", "repository": {"full_name": REPO, "default_branch": "main"},
                                  "workflow_run": {"head_branch": "feat/x", "conclusion": "failure"}}, post)
    assert not any("failure" in r.getMessage() for r in caplog.records)


# ── github_app read helpers ─────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(github_app, "_configured", lambda: True)
    monkeypatch.setattr(github_app, "_auth_headers", lambda repo: {"Authorization": "Bearer t"})
    calls = []

    def install(pages):
        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append((url, dict(params or {})))
            return _Resp(pages[len(calls) - 1])

        monkeypatch.setattr(github_app.requests, "get", fake_get)
        return calls

    return install


def test_pages_until_a_short_page(app):
    calls = app([[{"id": i} for i in range(100)], [{"id": 100}]])
    assert len(github_app.list_pull_reviews(REPO, 5)) == 101
    assert [c[1]["page"] for c in calls] == [1, 2]
    assert calls[0][0].endswith(f"/repos/{REPO}/pulls/5/reviews")


def test_check_runs_unwrap_their_key(app):
    calls = app([{"total_count": 1, "check_runs": [{"name": "Tests", "conclusion": "success"}]}])
    assert github_app.list_check_runs(REPO, "abc123") == [{"name": "Tests", "conclusion": "success"}]
    assert calls[0][1]["filter"] == "latest"


def test_recent_issues_drop_pull_requests(app):
    app([[{"number": 1}, {"number": 2, "pull_request": {}}]])
    assert github_app.list_recent_issues(REPO) == [{"number": 1}]


def test_a_wrong_shape_raises(app):
    app([{"message": "Not Found"}])
    with pytest.raises(RuntimeError):
        github_app.list_contributors(REPO)


def test_an_unconfigured_app_raises(monkeypatch):
    monkeypatch.setattr(github_app, "_configured", lambda: False)
    with pytest.raises(RuntimeError):
        github_app.list_issue_comments(REPO, 1)


# ── backfill ──────────────────────────────────────────────────────────────

BF_NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _gh_pr(n, created, merged=None, closed=None, assoc="CONTRIBUTOR", login="ada"):
    return {"number": n, "title": f"pr {n}", "html_url": f"https://x/{n}", "created_at": created,
            "merged_at": merged, "closed_at": closed or merged, "author_association": assoc,
            "user": {"login": login, "type": "User"}}


def _fetch(pulls, issues):
    return lambda repo: (pulls, issues)


def test_backfill_stamps_rows_with_when_they_happened():
    pulls = [_gh_pr(1, "2026-10-01T00:00:00Z", merged="2026-10-02T00:00:00Z"),
             _gh_pr(2, "2026-10-03T00:00:00Z", closed="2026-10-04T00:00:00Z"),
             _gh_pr(3, "2026-01-01T00:00:00Z")]  # outside the window
    issues = [{"number": 9, "title": "bug", "created_at": "2026-10-05T00:00:00Z", "closed_at": None,
               "author_association": "NONE", "user": {"login": "bo", "type": "User"}}]
    out = contributions.backfill([REPO], days=30, now=BF_NOW, fetch=_fetch(pulls, issues))
    assert out["added"] == 5 and out["repos"] == {REPO: 5}
    rows = [json.loads(ln) for ln in contributions.ledger_path().read_text().splitlines()]
    assert [(r["kind"], r["number"]) for r in rows] == [
        ("pr_opened", 1), ("pr_merged", 1), ("pr_opened", 2), ("pr_closed", 2), ("issue_opened", 9)]
    assert rows[1]["at"].startswith("2026-10-02") and all(r["backfill"] for r in rows)
    assert rows[4]["outsider"] is True


def test_backfill_twice_adds_nothing_and_skips_live_rows():
    contributions.record("pull_request", _pr_event("opened"))  # live row: pr_opened #5
    pulls = [_gh_pr(5, "2026-10-09T00:00:00Z")]
    first = contributions.backfill([REPO], days=30, now=BF_NOW, fetch=_fetch(pulls, []))
    assert first["added"] == 0
    pulls.append(_gh_pr(6, "2026-10-09T00:00:00Z"))
    assert contributions.backfill([REPO], days=30, now=BF_NOW, fetch=_fetch(pulls, []))["added"] == 1
    assert contributions.backfill([REPO], days=30, now=BF_NOW, fetch=_fetch(pulls, []))["added"] == 0


def test_one_unreadable_repo_does_not_stop_the_rest():
    def fetch(repo):
        if repo == "bad/repo":
            raise RuntimeError("404")
        return [_gh_pr(1, "2026-10-09T00:00:00Z")], []

    out = contributions.backfill(["bad/repo", REPO], days=30, now=BF_NOW, fetch=fetch)
    assert "bad/repo" in out["errors"] and out["repos"] == {REPO: 1}


def test_backfill_cli_defaults_to_the_profiles_core_repos(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(contributions, "backfill", lambda repos, days: seen.append((repos, days)) or {"added": 0})
    assert contributions.main(["--backfill"]) == 0
    assert seen[0][1] == 90 and "willow-memory/willow-mcp" in seen[0][0]
    assert contributions.main(["--backfill", "--repo", "a/b", "--days", "7"]) == 0
    assert seen[1] == (["a/b"], 7)


def test_backfill_cli_with_no_repos_says_so(monkeypatch, tmp_path, capsys):
    from willow_bot import operator_profile

    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(tmp_path / "none.json"))
    operator_profile.reset()
    assert contributions.main(["--backfill"]) == 2
    assert "no repos" in capsys.readouterr().err


# ── first-timer welcome ───────────────────────────────────────────────────


@pytest.fixture
def welcoming(monkeypatch, tmp_path):
    from willow_bot import operator_profile

    path = tmp_path / "operator.json"
    path.write_text(json.dumps({"welcome": {"orgs": ["willow-memory"]}}))
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(path))
    operator_profile.reset()
    return path


def test_a_first_timers_pr_gets_one_welcome(posts, welcoming):
    out, post = posts
    router.route("pull_request", _pr_event("opened", assoc="FIRST_TIME_CONTRIBUTOR"), post)
    welcomes = [b for _, _, b in out if "first contribution" in b]
    assert welcomes == [f"Thanks for your first contribution to {REPO}, @ada! A maintainer will take a look soon."]
    router.route("pull_request", _pr_event("opened", assoc="FIRST_TIME_CONTRIBUTOR"), post)
    assert len([b for _, _, b in out if "first contribution" in b]) == 1  # once per person per repo


def test_a_first_timers_issue_gets_the_welcome_instead_of_a_quip(posts, welcoming):
    out, post = posts
    payload = {"action": "opened", "repository": {"full_name": REPO},
               "issue": {"number": 4, "author_association": "FIRST_TIMER", "user": {"login": "bo", "type": "User"}}}
    router.route("issues", payload, post)
    assert len(out) == 1 and "@bo" in out[0][2]


@pytest.mark.parametrize("assoc,typ,repo", [
    ("CONTRIBUTOR", "User", REPO),               # not a first-timer
    ("FIRST_TIME_CONTRIBUTOR", "Bot", REPO),     # a bot
    ("FIRST_TIME_CONTRIBUTOR", "User", "other-org/x"),  # outside welcome.orgs
])
def test_no_welcome(posts, welcoming, assoc, typ, repo):
    from willow_bot import welcome

    assert welcome.line_for(repo, {"login": "ada", "type": typ}, assoc) == ""


def test_off_unless_the_profile_turns_it_on(posts):
    from willow_bot import welcome

    # The suite's profile (willow-memory) has no welcome section.
    assert welcome.line_for(REPO, {"login": "ada", "type": "User"}, "FIRST_TIMER") == ""


def test_a_custom_message_and_a_broken_one(posts, welcoming):
    from willow_bot import operator_profile, welcome

    welcoming.write_text(json.dumps({"welcome": {"orgs": ["willow-memory"], "message": "Hi {login} in {repo}"}}))
    operator_profile.reset()
    assert welcome.line_for(REPO, {"login": "ada", "type": "User"}, "FIRST_TIMER") == f"Hi ada in {REPO}"
    welcoming.write_text(json.dumps({"welcome": {"orgs": ["willow-memory"], "message": "Hi {nope}"}}))
    operator_profile.reset()
    assert "first contribution" in welcome.line_for(REPO, {"login": "cy", "type": "User"}, "FIRST_TIMER")
