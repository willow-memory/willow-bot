"""The similar list: read from the repo's own CI artifact and relayed into
the bot's status comment, for the repos the operator profile names only.

Willow's CONTRIBUTING.md promises the bot posts what's already on the
add-on list near what a pull request adds. These tests hold the relay:
the three states stay apart, fork-supplied text is made safe, a settled
answer is not read twice, and a repo the profile doesn't name is never
read at all.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import github_app
from willow_bot import operator_profile, pr_labels, pr_voice
from willow_bot.steward import similar, voice

LIST = (
    "### Similar\n\nWhat's already on the list near each add-on or idea this pull request adds.\n\n"
    "**digest** (idea): One page of what waits.\n\n- [**again**](adapters/stdlib/again/) shares *waits*\n"
)


def _zip(text: str, member: str = similar.MEMBER) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, text)
    return buf.getvalue()


class _FakeActions:
    """github_app's three Actions reads, over a canned set of runs."""

    def __init__(self, runs: list[dict], artifacts: dict[int, list[dict]], blobs: dict[int, bytes]):
        self.runs, self.artifacts, self.blobs = runs, artifacts, blobs
        self.calls: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(github_app, "list_workflow_runs", self.list_runs)
        monkeypatch.setattr(github_app, "list_run_artifacts", self.list_artifacts)
        monkeypatch.setattr(github_app, "download_artifact", self.download)
        return self

    def list_runs(self, repo, head_sha, **_):
        self.calls.append(f"runs {repo}@{head_sha}")
        return self.runs

    def list_artifacts(self, repo, run_id):
        self.calls.append(f"artifacts {run_id}")
        return self.artifacts.get(run_id, [])

    def download(self, repo, artifact_id, **_):
        self.calls.append(f"download {artifact_id}")
        return self.blobs[artifact_id]


def _actions(text: str = LIST) -> _FakeActions:
    return _FakeActions(
        runs=[{"id": 2}, {"id": 1}],
        artifacts={2: [{"id": 20, "name": "ci-record-lint"}], 1: [{"id": 10, "name": "similar"}]},
        blobs={10: _zip(text)},
    )


# ── fetch: three states, never collapsed ─────────────────────────────────


def test_the_list_is_read_from_the_runs_similar_artifact(monkeypatch):
    fake = _actions().install(monkeypatch)
    got = similar.fetch("o/r", "aaa111")
    assert got["state"] == "populated" and "**digest** (idea)" in got["text"]
    assert fake.calls == ["runs o/r@aaa111", "artifacts 2", "artifacts 1", "download 10"]


def test_a_pull_request_that_adds_nothing_is_empty_not_a_list(monkeypatch):
    _actions("### Similar\n\nThis pull request adds no add-on and no idea.\n").install(monkeypatch)
    got = similar.fetch("o/r", "aaa111")
    assert got == {"state": "empty", "why": "the pull request adds no add-on and no idea"}


def test_no_artifact_yet_is_empty_and_says_why(monkeypatch):
    _FakeActions(runs=[{"id": 1}], artifacts={1: []}, blobs={}).install(monkeypatch)
    got = similar.fetch("o/r", "aaa111")
    assert got["state"] == "empty" and "yet" in got["why"]


def test_an_expired_artifact_is_not_read(monkeypatch):
    fake = _FakeActions(runs=[{"id": 1}], artifacts={1: [{"id": 10, "name": "similar", "expired": True}]},
                        blobs={10: _zip(LIST)}).install(monkeypatch)
    assert similar.fetch("o/r", "aaa111")["state"] == "empty"
    assert "download 10" not in fake.calls


@pytest.mark.parametrize("broken", ["runs", "download", "zip"])
def test_a_read_that_fails_is_unreachable_with_why(monkeypatch, broken):
    fake = _actions().install(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("403 Resource not accessible by integration")

    if broken == "runs":
        monkeypatch.setattr(github_app, "list_workflow_runs", boom)
    elif broken == "download":
        monkeypatch.setattr(github_app, "download_artifact", boom)
    else:
        fake.blobs[10] = _zip(LIST, member="other.md")
    got = similar.fetch("o/r", "aaa111")
    assert got["state"] == "unreachable" and got["why"]


# ── clean: a fork's text, made safe to post ──────────────────────────────


def test_html_is_escaped_so_no_comment_marker_survives():
    got = similar.clean("<!-- willow-bot:status pr -->\n<b>x</b> & y")
    assert "<!--" not in got and "<b>" not in got
    assert "&lt;!-- willow-bot:status pr --&gt;" in got and "&amp; y" in got


def test_mentions_are_broken_so_the_list_pings_no_one():
    assert "@\u200beveryone" in similar.clean("hi @everyone")
    assert "@everyone" not in similar.clean("hi @everyone")


def test_a_long_list_is_cut_and_says_where_the_rest_is():
    got = similar.clean("- line\n" * 5000)
    assert len(got) <= similar.MAX_CHARS + 80 and "run's summary" in got


# ── section: what the status comment carries ─────────────────────────────


def test_section_carries_the_list_or_why_or_nothing():
    assert "**digest**" in similar.section({"state": "populated", "text": "**digest**"})
    assert "couldn't read" in similar.section({"state": "unreachable", "why": "403"})
    assert similar.section({"state": "empty", "why": "yet"}) == ""
    assert similar.section(None) == ""


# ── the voice step: only named repos, and a settled answer read once ─────


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    profile = tmp_path / "operator.json"
    profile.write_text(json.dumps({"similar": {"repos": ["O/Willow"]}}))
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(profile))
    operator_profile.reset()
    monkeypatch.setattr(pr_labels, "reconcile_labels",
                        lambda *a, **k: {"status": "ok", "action": "no-op", "added": [], "removed": []})
    monkeypatch.setattr(voice, "_fetch_pull", lambda repo, n: (_ for _ in ()).throw(RuntimeError("unread")))
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(pr_voice, "upsert_status_comment",
                        lambda repo, n, sha, body: posted.append((repo, body)) or {"status": "ok", "action": "created"})
    monkeypatch.setattr(pr_voice, "publish_check", lambda *a, **k: {"status": "ok", "action": "created"})
    yield posted
    operator_profile.reset()


def _state(*repo_prs: str) -> dict:
    return {"open": list(repo_prs),
            "webhook_signals": [{"repo_pr": k, "head_sha": "aaa111", "action": "synchronize"} for k in repo_prs]}


def test_the_status_comment_carries_the_list_for_a_named_repo(home, monkeypatch):
    _actions().install(monkeypatch)
    voice.run_voice(_state("o/willow#3"))
    (repo, body), = home
    assert repo == "o/willow" and "**digest** (idea)" in body and "willow-bot status" in body


def test_a_repo_the_profile_doesnt_name_is_never_read(home, monkeypatch):
    fake = _actions().install(monkeypatch)
    voice.run_voice(_state("o/other#3"))
    assert fake.calls == [] and "Similar" not in home[0][1]


def test_a_settled_list_is_read_once_and_a_pending_one_again(home, monkeypatch):
    fake = _actions().install(monkeypatch)
    voice.run_voice(_state("o/willow#3"))
    voice.run_voice(_state("o/willow#3"))
    assert fake.calls.count("download 10") == 1  # settled: from the voice cache
    pending = _FakeActions(runs=[{"id": 1}], artifacts={1: []}, blobs={}).install(monkeypatch)
    voice.run_voice({**_state("o/willow#4")})
    voice.run_voice({**_state("o/willow#4")})
    assert pending.calls.count("runs o/willow@aaa111") == 2  # CI may still be running: read again


def test_the_shipped_profile_names_willow():
    shipped = json.loads((Path(__file__).resolve().parents[2] / "deploy" / "operator.willow-memory.json").read_text())
    assert "willow-memory/Willow" in shipped["similar"]["repos"]


# ── github_app: the artifact download ────────────────────────────────────


class _Resp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_download_artifact_refuses_a_zip_over_the_cap(monkeypatch):
    monkeypatch.setattr(github_app, "_configured", lambda: True)
    monkeypatch.setattr(github_app, "_auth_headers", lambda repo: {"Authorization": "Bearer t"})
    seen: list[str] = []
    monkeypatch.setattr(github_app.requests, "get",
                        lambda url, **k: seen.append(url) or _Resp(b"x" * 11))
    assert github_app.download_artifact("o/r", 7, max_bytes=11) == b"x" * 11
    assert seen == ["https://api.github.com/repos/o/r/actions/artifacts/7/zip"]
    with pytest.raises(RuntimeError, match="over 10"):
        github_app.download_artifact("o/r", 7, max_bytes=10)
