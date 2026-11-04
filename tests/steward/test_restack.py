"""The restack step: a pull request main moved under, brought current by the
repo's own restack.py and pushed, and nothing else ever pushed.

A real origin (a bare repo here), a branch main has moved under, and a
restack.py in the repo that does what an env var says: merge, stop, or
misbehave. GitHub's reads are faked; git is real.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import github_app
from willow_bot import operator_profile
from willow_bot.steward import app_grant, restack, voice

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip

#: restack.py, as the repo would carry it, told what to do by FAKE_RESTACK
FAKE = r"""import os, subprocess, sys
from pathlib import Path
here = Path(__file__).resolve().parents[3]
log = os.environ.get("FAKE_RESTACK_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
onto, branch = sys.argv[2], sys.argv[3]
def git(*a):
    return subprocess.run(["git", *a], cwd=here, capture_output=True, text=True, check=True).stdout.strip()
how = os.environ.get("FAKE_RESTACK", "merge")
old = git("rev-parse", branch)
if how == "stop":
    print(f"{branch}: stopped: thing.py conflicts, and no rule settles it")
    sys.exit(2)
if how == "bad":
    git("branch", "-f", branch, onto)
    sys.exit(0)
tree = git("rev-parse", f"{onto}^{{tree}}")
new = git("commit-tree", tree, "-p", old, "-p", onto, "-m", f"Merge {onto} into {branch}")
git("branch", "-f", branch, new)
print(f"{branch}: {old[:8]} -> {new[:8]}, {onto} merged in; settled CHANGELOG.md")
"""


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, env={**os.environ, **ENV}, check=True,
                          capture_output=True, text=True).stdout.strip()  # fmt: skip


class _FakeGitHub:
    """github_app's reads, answering from the real origin: each open pull
    request as GitHub would show it, conflicting or not."""

    def __init__(self, origin: Path, prs: dict[int, dict]):
        self.origin, self.prs, self.calls = origin, prs, []

    def install(self, monkeypatch):
        monkeypatch.setattr(github_app, "list_open_pulls", self.list_open)
        monkeypatch.setattr(github_app, "get_pull", self.get_pull)
        monkeypatch.setattr(github_app, "installation_token", self.token)
        return self

    def _sha(self, ref: str) -> str:
        return git(self.origin, "rev-parse", f"refs/heads/{ref}")

    def get_pull(self, repo, number):
        self.calls.append(f"get {number}")
        p = self.prs[number]
        return {"number": number, "mergeable_state": p.get("state", "dirty"),
                "head": {"ref": p["ref"], "sha": self._sha(p["ref"]), "repo": {"full_name": p.get("repo", "o/r")}},
                "base": {"ref": "main", "sha": self._sha("main")}}  # fmt: skip

    def list_open(self, repo, **_):
        self.calls.append("list")
        return [self.get_pull(repo, n) for n in self.prs]

    def token(self, repo):
        raise RuntimeError("no App here")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """origin with main moved under `work` (#5); the steward's state under
    tmp_path, its clone of o/r made from origin."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    script = seed / "adapters/stdlib/restack/restack.py"
    script.parent.mkdir(parents=True)
    script.write_text(FAKE)
    (seed / "CHANGELOG.md").write_text("- old\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "the repo")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    git(seed, "checkout", "-q", "-b", "work")
    (seed / "thing.py").write_text("x = 1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "work")
    git(seed, "push", "-q", "origin", "work")
    git(seed, "checkout", "-q", "main")
    (seed / "CHANGELOG.md").write_text("- new\n- old\n")
    git(seed, "commit", "-q", "-am", "main moves")
    git(seed, "push", "-q", "origin", "main")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FAKE_RESTACK_LOG", str(tmp_path / "ran.log"))
    monkeypatch.setattr(restack, "remote_url", lambda repo: str(origin))
    monkeypatch.setattr(operator_profile, "restack_repos", lambda: ["o/r"])
    monkeypatch.setattr(operator_profile, "restack_python", lambda: sys.executable)
    gh = _FakeGitHub(origin, {5: {"ref": "work"}}).install(monkeypatch)
    return {"origin": origin, "gh": gh, "log": tmp_path / "ran.log"}


def ran(world) -> list[str]:
    return world["log"].read_text().splitlines() if world["log"].exists() else []


def test_no_repo_named_is_absent_and_asks_github_nothing(world, monkeypatch):
    monkeypatch.setattr(operator_profile, "restack_repos", list)
    got = restack.run()
    assert got["status"] == "absent" and world["gh"].calls == []


def test_a_conflicting_pull_request_gets_restacks_merge_pushed_and_said(world):
    before = git(world["origin"], "rev-parse", "refs/heads/work")
    got = restack.run()
    assert [d["outcome"] for d in got["done"]] == ["pushed"], got
    tip = git(world["origin"], "rev-parse", "refs/heads/work")
    assert git(world["origin"], "rev-parse", f"{tip}^1") == before
    assert git(world["origin"], "rev-parse", f"{tip}^2") == git(
        world["origin"], "rev-parse", "refs/heads/main"
    )
    assert ran(world) == ["--onto origin/main work"]
    assert "main merged in as" in restack.status_line("o/r", 5, tip)
    assert restack.status_line("o/r", 5, "0" * 40) == "", "said only of its own head"


def test_the_clone_stays_detached_so_no_branch_it_moves_is_checked_out(world):
    restack.run()
    got = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=restack.clone_dir("o/r"),
                         capture_output=True, text=True, check=False)  # fmt: skip
    assert got.returncode != 0, "HEAD names no branch"


def test_a_clean_pull_request_and_a_forks_are_left_alone(world, monkeypatch):
    world["gh"].prs = {
        5: {"ref": "work", "state": "clean"},
        6: {"ref": "work", "repo": "x/r"},
    }
    got = restack.run()
    assert got["done"] == [] and ran(world) == []
    assert not restack.clone_dir("o/r").exists(), "nothing cloned for nothing to do"


def test_one_restack_cant_settle_is_said_and_never_tried_again_at_that_head(
    world, monkeypatch
):
    monkeypatch.setenv("FAKE_RESTACK", "stop")
    before = git(world["origin"], "rev-parse", "refs/heads/work")
    got = restack.run()
    assert [d["outcome"] for d in got["done"]] == ["stopped"]
    assert git(world["origin"], "rev-parse", "refs/heads/work") == before, (
        "nothing pushed"
    )
    line = restack.status_line("o/r", 5, before)
    assert (
        line.startswith("- restack: couldn't, a person is needed:")
        and "thing.py conflicts" in line
    )
    again = restack.run()
    assert again["skipped"] == 1 and len(ran(world)) == 1


def test_a_tip_that_isnt_one_merge_onto_the_branch_is_never_pushed(world, monkeypatch):
    monkeypatch.setenv("FAKE_RESTACK", "bad")
    before = git(world["origin"], "rev-parse", "refs/heads/work")
    got = restack.run()
    assert [d["outcome"] for d in got["done"]] == ["refused"]
    assert git(world["origin"], "rev-parse", "refs/heads/work") == before


def test_a_plan_says_what_it_would_do_and_touches_nothing(world):
    got = restack.run(plan=True)
    assert [d["outcome"] for d in got["done"]] == ["would restack"]
    assert ran(world) == [] and not restack.clone_dir("o/r").exists()
    assert restack.load_state() == {}


def test_after_its_push_the_entry_stays_while_the_head_is_its_merge(world):
    restack.run()
    tip = git(world["origin"], "rev-parse", "refs/heads/work")
    world["gh"].prs[5]["state"] = "clean"
    restack.run()
    assert "main merged in" in restack.status_line("o/r", 5, tip)
    seed_moves = git(world["origin"], "rev-parse", "refs/heads/main")
    git(
        world["origin"], "update-ref", "refs/heads/work", seed_moves
    )  # someone moves it on
    restack.run()
    assert restack.load_state() == {}, "a head it didn't make is forgotten"


def test_the_token_reaches_git_by_the_environment_for_github_only(monkeypatch):
    monkeypatch.setattr(github_app, "installation_token", lambda repo: "tok123")
    env = restack.auth_env("o/r")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert (
        base64.b64encode(b"x-access-token:tok123").decode() in env["GIT_CONFIG_VALUE_0"]
    )
    assert "tok123" not in json.dumps(env), "never in plain text"
    monkeypatch.setattr(
        github_app,
        "installation_token",
        lambda repo: (_ for _ in ()).throw(RuntimeError),
    )
    assert restack.auth_env("o/r") == {}


def test_restacking_makes_contents_write_needed_not_broader_than_needed():
    perms = {"metadata": "read", "issues": "write", "pull_requests": "write",
             "checks": "read", "contents": "read"}  # fmt: skip
    app = {"permissions": perms, "events": sorted(app_grant.REQUIRED_EVENTS)}
    assert app_grant.assess(app)["missing_permissions"] == {}
    v = app_grant.assess(app, restacks=True)
    assert v["missing_permissions"] == {"contents": {"need": "write", "have": "read"}}
    w = app_grant.assess(
        {**app, "permissions": {**perms, "contents": "write"}}, restacks=True
    )
    assert w["missing_permissions"] == {} and "contents" not in w["broader_than_needed"]


def test_the_status_comment_carries_the_restack_line(world):
    restack.run()
    tip = git(world["origin"], "rev-parse", "refs/heads/work")
    body = voice._status_comment_body("o/r#5", tip, {})
    assert "- restack: main merged in as" in body
    assert "- restack:" not in voice._status_comment_body("o/r#6", tip, {})


def test_the_willow_memory_profile_restacks_willow():
    operator_profile.reset()
    assert operator_profile.restack_repos() == ["willow-memory/willow"]
