"""The operator profile: a blank install carries no operator's setup, and a
different operator's profile is the one that is used."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import troll
from willow_bot import deposits, operator_profile
from willow_bot.steward import fleet, merge, upstream_config, upstream_desk
from willow_bot.steward import tick

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def blank(monkeypatch, tmp_path):
    """No profile at all, and no `gh` to discover anything from."""
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setattr(fleet, "gh_api", lambda path: (_ for _ in ()).throw(fleet.subprocess.CalledProcessError(1, "gh")))
    operator_profile.reset()
    yield
    operator_profile.reset()


@pytest.fixture
def ada(monkeypatch, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps({
        "login": "ada", "bot_name": "ada-bot", "orgs": ["analytical"],
        "upstream_watch": ["babbage/engine"], "core_repos": ["analytical/notes"],
        "github_root": str(tmp_path / "src"), "voice": {"orgs": ["analytical"]},
        "grove": {"ci_red_orgs": ["analytical"], "operator_sender": "ada"},
        "broker_pairs": {"analytical/notes": {"venv": "notes", "extras": []}},
    }))
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(path))
    operator_profile.reset()
    yield tmp_path
    operator_profile.reset()


def test_a_blank_install_has_no_operator(blank):
    assert operator_profile.login() == ""
    assert fleet.fleet_orgs.__wrapped__() == ()
    assert fleet.fleet_user() == ""
    assert upstream_config.watch_repos() == []
    assert operator_profile.core_repos() == []
    assert operator_profile.bot_name() == "willow-bot"
    assert operator_profile.grove_ci_red_orgs() == []
    assert operator_profile.grove_operator_sender() == ""
    assert operator_profile.broker_pairs() == {}


def test_a_blank_install_is_silent(blank):
    assert not troll.allowed("willow-memory/willow-bot", "OWNER")
    assert not tick._grove_ci_red("willow-memory/willow-bot")


def test_the_upstream_desk_with_no_one_to_track_says_so(blank, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_DESK", "1")
    receipt = upstream_desk.run_upstream_desk(enable_mcp=False)
    assert receipt["status"] == "absent" and "operator" in receipt["detail"]


def test_another_operator_gets_their_own_setup(ada):
    assert operator_profile.login() == "ada"
    assert fleet.fleet_orgs.__wrapped__() == ("analytical",)
    assert upstream_config.watch_repos() == ["babbage/engine"]
    assert troll.allowed("analytical/notes", "OWNER")
    assert not troll.allowed("willow-memory/willow-bot", "OWNER")
    assert tick._grove_ci_red("Analytical/notes") and not tick._grove_ci_red("willow-memory/x")
    assert deposits.ci_outcome_record(repo="analytical/notes", head_sha="a", check_run_id=1, check_name="t",
                                      conclusion="success", status="completed", pr_number=None,
                                      html_url="", sender_type="Bot")["deposited_by"] == "ada-bot"


def test_clones_resolve_under_the_operators_root(ada):
    clone = ada / "src" / "analytical" / "notes"
    (clone / ".git").mkdir(parents=True)
    assert merge.resolve_local("analytical/notes") == clone


def test_env_still_wins_over_the_profile(ada, monkeypatch):
    monkeypatch.setenv("LOKI_PR_WATCH_USER", "grace")
    monkeypatch.setenv("WILLOW_BOT_UPSTREAM_WATCH_REPOS", "x/y")
    monkeypatch.setenv("WILLOW_BOT_NAME", "grace-bot")
    assert operator_profile.login() == "grace"
    assert upstream_config.watch_repos() == ["x/y"]
    assert operator_profile.bot_name() == "grace-bot"


def test_a_broken_profile_is_no_profile(monkeypatch, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text("{not json")
    monkeypatch.setenv(operator_profile.PROFILE_ENV, str(path))
    operator_profile.reset()
    assert operator_profile.profile() == {}
    assert operator_profile.voice_orgs() is None


def test_the_shipped_config_names_no_operator():
    cfg = json.loads((ROOT / "willow-bot.json").read_text())
    assert cfg["troll_scope"]["orgs"] == []
    example = json.loads((ROOT / "deploy" / "operator.example.json").read_text())
    assert example["login"] == "your-github-login"


def test_no_operator_login_is_left_in_code():
    """The fleet's own identifiers live in deploy/operator.willow-memory.json,
    not in code that every install runs."""
    banned = ("rudi193", "sean-campbell", "/home/sean")
    hits = []
    for path in [*ROOT.glob("*.py"), *ROOT.glob("willow_bot/**/*.py"), *ROOT.glob("integrations/*.py"),
                 *ROOT.glob("loki/*.py"), *ROOT.glob("scripts/*")]:
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if any(b in line for b in banned):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert hits == []
