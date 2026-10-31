"""willow_bot.steward.keep_current — what's behind, and the person told once.

It writes gitsync triggers and files ``human_required``; it never installs,
restarts or copies. The git checks run on real repos; systemd, the trigger
writer and the MCP calls are stubbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from willow_bot.steward import keep_current as kc


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _commit(repo: Path, name: str, text: str = "x") -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "checkout"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, "app.py", "v1")
    return r


def _started_now() -> int:
    """A unit start time after everything so far, with the next commits
    landing in a later second (the reflog keeps whole seconds)."""
    started = int(time.time())
    time.sleep(1.1)
    return started


def test_docs_are_not_code():
    assert kc.is_doc("README.md") and kc.is_doc("docs/x.py") and kc.is_doc("pkg/docs/y.py")
    assert kc.is_doc("posts/day.md") and not kc.is_doc("src/app.py") and not kc.is_doc("pyproject.toml")


def test_a_pull_of_docs_after_a_start_owes_no_restart(repo):
    started = _started_now()
    _commit(repo, "README.md", "more words")
    assert kc.code_since(repo, started)["status"] == "empty"


def test_code_that_moved_after_a_start_is_named(repo):
    started = _started_now()
    _commit(repo, "README.md", "words")
    _commit(repo, "src/app.py", "v2")
    got = kc.code_since(repo, started)
    assert got["status"] == "populated" and got["paths"] == ["src/app.py"]


def test_a_start_older_than_the_reflog_is_unknown_not_clean(repo):
    assert kc.code_since(repo, 0)["status"] == "unreachable"


def test_each_unit_whose_code_moved_is_owed_a_restart(repo, monkeypatch):
    started = _started_now()
    _commit(repo, "src/app.py", "v2")
    other = repo.parent / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _commit(other, "lib.py")
    quiet_start = _started_now()
    inv = {"venvs": [
        {"path": "/v/a", "editables": [str(repo)], "runs": {"units": ["a.service"]}},
        {"path": "/v/b", "editables": [str(other)], "runs": {"units": ["b.service"]}},
        {"path": "/v/c", "editables": [str(repo)], "runs": {"units": []}},  # runs nothing
    ]}
    props = {"a.service": {"Id": "a.service", "ActiveEnterTimestamp": f"@{started}"},
             "b.service": {"Id": "b.service", "ActiveEnterTimestamp": f"@{quiet_start}", "WorkingDirectory": str(other)}}
    monkeypatch.setattr(kc, "_unit_props", lambda units: {u: props[u] for u in units})

    got = kc.restarts_owed(inv)

    assert [(o["unit"], o["paths"]) for o in got["owed"]] == [("a.service", ["src/app.py"])]


def test_when_systemd_cant_be_asked_restarts_are_unknown(monkeypatch):
    def no_bus(units):
        raise OSError("Failed to connect to bus")

    monkeypatch.setattr(kc, "_unit_props", no_bus)
    inv = {"venvs": [{"path": "/v", "editables": [], "runs": {"units": ["a.service"]}}]}
    assert kc.restarts_owed(inv)["status"] == "unreachable"


def _editable(venv: Path, checkout: Path, installed: float) -> None:
    dist = venv / "lib" / "python3.12" / "site-packages" / "thing-1.0.dist-info"
    dist.mkdir(parents=True)
    direct = dist / "direct_url.json"
    direct.write_text(json.dumps({"url": checkout.as_uri(), "dir_info": {"editable": True}}))
    os.utime(direct, (installed, installed))


def test_an_install_older_than_its_pyproject_is_stale_once_per_checkout(repo, tmp_path):
    _commit(repo, "pyproject.toml", "[project]\ndependencies = ['PyJWT==2.15.1']\n")
    old = tmp_path / "old-venv"
    fresh = tmp_path / "fresh-venv"
    _editable(old, repo, installed=1_000_000)
    _editable(fresh, repo, installed=time.time() + 60)
    inv = {"venvs": [
        {"path": str(old), "editables": [str(repo), str(repo)], "runs": {"units": ["a.service"]}},
        {"path": str(fresh), "editables": [str(repo)], "runs": {"units": ["b.service"]}},
        {"path": str(old), "editables": [str(repo)], "runs": {"units": []}},  # runs nothing: not checked
    ]}

    got = kc.stale_installs(inv)

    assert [(s["venv"], s["checkout"]) for s in got["stale"]] == [(str(old), str(repo))]


def test_stale_installs_are_one_item_per_venv():
    stale = [{"venv": "/v", "checkout": f"/c/{n}", "commit": "abc", "units": ["u.service"]} for n in ("a", "b")]
    found = kc.findings({"install": {"stale": stale}})
    assert list(found) == ["install:/v"]
    assert "a (abc), b (abc)" in found["install:/v"]["summary"]


def test_behind_asks_a_pull_only_for_a_clean_checkout(monkeypatch):
    rows = [{"repo": "o/clean", "state": "behind", "dirty": 0}, {"repo": "o/mine", "state": "behind", "dirty": 2},
            {"repo": "o/ok", "state": "current", "dirty": 0}, {"repo": "o/away", "state": "off-default", "dirty": 0}]
    monkeypatch.setattr(kc.current, "checkouts", lambda: {"checkouts": rows})
    asked = []
    monkeypatch.setattr(kc, "_trigger", lambda repo: asked.append(repo) or "requested")

    got = kc.behind()

    assert asked == ["o/clean"]
    assert [b["pull"] for b in got["behind"]] == ["requested", "not asked: the checkout has changes"]


class _Desk:
    """A stand-in for the MCP calls: files, resolves, and can refuse."""

    def __init__(self, refuse: bool = False):
        self.filed: list[dict] = []
        self.resolved: list[str] = []
        self.notes: dict[str, str] = {}
        self.refuse = refuse

    def __call__(self, tool: str, args: dict):
        if self.refuse:
            return {"error": "gate denied"}
        if tool == "human_required_enqueue":
            self.filed.append(args)
            return {"id": f"hr-{len(self.filed)}"}
        self.resolved.append(args["item_id"])
        self.notes[args["item_id"]] = args.get("note", "")
        return {"ok": True}


@pytest.fixture
def quiet(monkeypatch):
    """Nothing real: an inventory with nothing in it, the slow checks
    stubbed. Tests set ``owed`` to what restarts_owed should find."""
    monkeypatch.setattr(kc.venvs, "inventory", lambda: {"venvs": []})
    state = {"owed": [], "copies": {"status": "populated", "bytes": [], "pin": [], "engine": [], "workflow": []}}
    monkeypatch.setattr(kc, "restarts_owed", lambda inv: {"status": "populated" if state["owed"] else "empty",
                                                         "owed": state["owed"]})  # fmt: skip
    monkeypatch.setattr(kc, "behind", lambda: {"status": "empty", "behind": []})
    monkeypatch.setattr(kc.current, "copies", lambda: state["copies"])
    state["pypi"] = {"status": "populated", "venvs": []}
    monkeypatch.setattr(kc.pypi_fresh, "report", lambda: state["pypi"])
    return state


_OWED = {"unit": "a.service", "checkout": "/c", "head": "b" * 40, "then": "a" * 40, "paths": ["x.py"], "started": 1}


def test_a_finding_is_filed_once_and_resolved_when_it_clears(quiet):
    desk = _Desk()
    quiet["owed"] = [_OWED]

    first = kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1000)
    second = kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1300)
    quiet["owed"] = []
    third = kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1600)

    assert [i["state"] for i in first["items"]] == ["filed"]
    assert [i["state"] for i in second["items"]] == ["open"] and len(desk.filed) == 1
    assert [i["state"] for i in third["items"]] == ["resolved"] and desk.resolved == ["hr-1"]
    assert kc.load()["filed"] == {}


def test_a_refused_filing_is_retried_never_marked_filed(quiet):
    quiet["owed"] = [_OWED]
    got = kc.run(enable_mcp=True, call=_Desk(refuse=True), app="willow-bot", now=1000)
    assert got["items"][0]["state"] == "could-not-file" and kc.load()["filed"] == {}
    desk = _Desk()
    assert kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1300)["items"][0]["state"] == "filed"


def test_without_mcp_nothing_is_filed_and_it_says_so(quiet):
    quiet["owed"] = [_OWED]
    got = kc.run(enable_mcp=False, call=None, now=1000)
    assert got["items"][0]["state"] == "unfiled"


def test_a_section_that_didnt_run_resolves_nothing(quiet):
    desk = _Desk()
    quiet["copies"] = {"status": "populated", "bytes": [{"copy": "o/a:x.py", "source": "o/b:x.py",
                                                         "status": "populated", "current": False}],
                       "pin": [], "engine": [], "workflow": []}  # fmt: skip
    kc.run(enable_mcp=True, call=desk, now=100_000)
    assert len(desk.filed) == 1
    # An hour on: copies aren't due (daily), so their item stands though not looked at.
    quiet["copies"] = {"status": "populated", "bytes": [], "pin": [], "engine": [], "workflow": []}
    kc.run(enable_mcp=True, call=desk, now=103_600)
    assert desk.resolved == []
    # A day on, they run again; it's gone, so it's resolved.
    kc.run(enable_mcp=True, call=desk, now=100_000 + 86_400)
    assert desk.resolved == ["hr-1"]


def test_an_unreachable_check_never_resolves_what_it_filed(quiet):
    desk = _Desk()
    quiet["copies"] = {"status": "populated", "bytes": [{"copy": "o/a:x.py", "source": "o/b:x.py",
                                                         "status": "populated", "current": False}],
                       "pin": [], "engine": [], "workflow": []}  # fmt: skip
    kc.run(enable_mcp=True, call=desk, now=100_000)
    quiet["copies"] = {"status": "unreachable", "detail": "registry unreadable"}
    kc.run(enable_mcp=True, call=desk, now=100_000 + 86_400)
    assert desk.resolved == []


def test_the_slow_checks_keep_their_cadence(quiet, monkeypatch):
    calls = []
    monkeypatch.setattr(kc, "behind", lambda: calls.append("behind") or {"status": "empty", "behind": []})
    for now in (0, 300, 3600, 3900):
        kc.run(now=now)
    assert calls == ["behind", "behind"], "hourly: at 0 and 3600"


def test_an_engine_vertical_behind_is_its_own_item_and_an_override_never_one():
    copies = {"engine": [{"workspace": "almanac-data/almanac-data",
                          "behind": {"agriculture-almanac": {"update": ["AGENTS.md"], "add": []}},
                          "overrides": {"climate-almanac": ["AGENTS.md"]}}]}  # fmt: skip
    found = kc.findings({"copies": copies})
    assert list(found) == ["engine:almanac-data/almanac-data:agriculture-almanac"]


def test_a_vulnerable_venv_is_one_high_item_naming_each_fix_and_its_range(quiet):
    desk = _Desk()
    quiet["pypi"] = {"status": "populated", "venvs": [{"path": "/v", "packages": [
        {"name": "urllib3", "installed": "2.7.0", "kind": "vulnerable", "vulnerabilities": ["GHSA-1"],
         "fix": "2.8.0", "fix_in_range": True},
        {"name": "pillow", "installed": "12.3.0", "kind": "vulnerable", "vulnerabilities": ["GHSA-2"],
         "fix": "13.0.0", "fix_in_range": False},
        {"name": "uvicorn", "installed": "0.42.0", "kind": "pinned"},
        {"name": "lxml", "installed": "6.1.2", "kind": "patch", "to": "6.1.3"},
    ]}]}  # fmt: skip

    kc.run(enable_mcp=True, call=desk, now=100_000)

    [item] = desk.filed
    assert item["priority"] == "high" and item["source_ref"] == "keep-current:pypi:/v"
    assert "urllib3 2.7.0 -> 2.8.0 (inside its declared range" in item["summary"]
    assert "pillow 12.3.0 -> 13.0.0 (OUTSIDE its declared range" in item["summary"]
    assert "uvicorn" not in item["summary"] and "lxml" not in item["summary"], "pins and plain upgrades aren't filed"


def test_an_old_commit_pulled_after_a_start_is_still_owed(repo):
    # A commit dated 2020 waits on a branch; the unit starts; then it is
    # fast-forwarded in, as a late pull would. HEAD moved after the start.
    _git(repo, "checkout", "-q", "-b", "old")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "app.py").write_text("v2")
    _git(repo, "add", "-A")
    env = {**os.environ, "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z", "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z"}
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org",
                    "commit", "-q", "-m", "dated long ago"], check=True, env=env)  # fmt: skip
    _git(repo, "checkout", "-q", "main")
    started = _started_now()
    _git(repo, "merge", "-q", "--ff-only", "old")
    got = kc.code_since(repo, started)
    assert got["status"] == "populated" and got["paths"] == ["src/app.py"], "HEAD moved after the start"


def test_the_most_urgent_are_filed_first_and_only_a_few_a_tick(quiet):
    desk = _Desk()
    quiet["owed"] = [{**_OWED, "unit": f"u{i}.service"} for i in range(2)]
    quiet["copies"] = {"status": "populated", "bytes": [
        {"copy": f"o/a:{i}.py", "source": "o/b:x.py", "status": "populated", "current": False} for i in range(6)],
        "pin": [], "engine": [], "workflow": []}  # fmt: skip
    quiet["pypi"] = {"status": "populated", "venvs": [{"path": "/v", "packages": [
        {"name": "urllib3", "installed": "2.7.0", "kind": "vulnerable", "vulnerabilities": ["A"], "fix": "2.8.0",
         "fix_in_range": True}]}]}  # fmt: skip

    got = kc.run(enable_mcp=True, call=desk, now=100_000)

    assert [f["source_ref"].split(":")[1] for f in desk.filed] == ["pypi", "restart", "restart", "copy", "copy"]
    assert sum(i["state"] == "waiting" for i in got["items"]) == 4
    kc.run(enable_mcp=True, call=desk, now=100_300)
    assert len(desk.filed) == 9, "the rest on the next tick"


def test_a_rate_limit_stops_the_tick_and_retries_later(quiet):
    calls = []

    def limited(tool, args):
        calls.append(tool)
        return {"error": "rate_limited", "retry_after": 7}

    quiet["owed"] = [{**_OWED, "unit": f"u{i}.service"} for i in range(4)]
    got = kc.run(enable_mcp=True, call=limited, now=100_000)
    assert calls == ["human_required_enqueue"], "one refusal and the tick stops asking"
    assert [i["state"] for i in got["items"]].count("waiting") == 3


# ── refreshing a stale install, where the bot may ─────────────────────────


def _with_origin(repo: Path, tmp_path: Path) -> None:
    """Give ``repo`` a bare origin whose HEAD is main, as a clone has."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")


def test_a_pyproject_change_pulled_after_the_install_is_stale_whatever_its_date(repo, tmp_path):
    # Committed (and dated) before the install, but it reached HEAD after it.
    _git(repo, "checkout", "-q", "-b", "incoming")
    _commit(repo, "pyproject.toml", "[project]\ndependencies = ['x==2']\n")
    _git(repo, "checkout", "-q", "main")
    venv = tmp_path / "venv"
    _editable(venv, repo, installed=time.time())
    time.sleep(1.1)
    _git(repo, "merge", "-q", "--ff-only", "incoming")
    inv = {"venvs": [{"path": str(venv), "editables": [str(repo)], "runs": {"units": ["a.service"]}}]}
    assert [s["checkout"] for s in kc.stale_installs(inv)["stale"]] == [str(repo)]


def test_the_bot_refreshes_only_a_checkouts_own_venv_or_an_allowlisted_box_pair(repo, tmp_path, monkeypatch):
    own = tmp_path / "venvs-elsewhere" / "repo-venv"
    own.mkdir(parents=True)
    (repo / ".venv").symlink_to(own)
    assert kc.how_to_refresh(own, repo) == "own"
    box = kc._paths.legacy_venvs_dir() / "willow-mcp"
    box.mkdir(parents=True)
    monkeypatch.setattr(kc.current, "_origin_repo", lambda checkout: "willow-memory/willow-mcp")
    assert kc.how_to_refresh(box, repo) is None, "no allowlist: not the bot's"
    allow = kc._paths.willow_home() / "constitutional" / "pip_sync_allowlist.json"
    allow.parent.mkdir(parents=True, exist_ok=True)
    allow.write_text(json.dumps({"pairs": {"willow-memory/willow-mcp": {"venv": "willow-mcp"}}}))
    assert kc.how_to_refresh(box, repo) == "broker"
    assert kc.how_to_refresh(tmp_path / "someone-elses", repo) is None, "another repo's venv"


def test_a_checkout_with_changes_or_on_a_branch_is_never_installed_from(repo, tmp_path):
    _with_origin(repo, tmp_path)
    assert kc._clean_on_default(repo) is None
    (repo / "app.py").write_text("edited")
    assert kc._clean_on_default(repo) == "the checkout has changes"
    _git(repo, "checkout", "-q", "--", "app.py")
    _git(repo, "checkout", "-q", "-b", "feature")
    assert "on feature" in kc._clean_on_default(repo)


class _FakePip:
    def __init__(self, *, dry=0, dry_err="", install=0, check=0, check_out=""):
        self.calls: list[tuple[str, ...]] = []
        self.answers = {"--dry-run": (dry, "", dry_err), "install": (install, "", ""), "check": (check, check_out, "")}

    def __call__(self, venv, *args):
        self.calls.append(args)
        key = "--dry-run" if "--dry-run" in args else args[0]
        code, out, err = self.answers[key]
        return subprocess.CompletedProcess(args, code, out, err)


@pytest.fixture
def own_stale(repo, tmp_path, monkeypatch):
    _with_origin(repo, tmp_path)
    venv = tmp_path / "v"
    venv.mkdir()
    (repo / ".venv").symlink_to(venv)
    monkeypatch.setattr(kc.venvs, "extras_of", lambda v, c: ["dev"])
    return {"venv": str(venv), "checkout": str(repo), "units": ["a.service"], "commit": "abc"}


def test_an_own_venv_refresh_dry_runs_installs_with_its_extras_and_checks(own_stale, monkeypatch):
    pip = _FakePip()
    monkeypatch.setattr(kc, "_pip", pip)
    got = kc.refresh(own_stale)
    assert got["state"] == "refreshed" and got["how"] == "own"
    target = f"{own_stale['checkout']}[dev]"
    assert pip.calls == [("install", "--dry-run", "-e", target), ("install", "-q", "-e", target), ("check",)]


def test_a_dry_run_that_conflicts_installs_nothing(own_stale, monkeypatch):
    pip = _FakePip(dry=1, dry_err="ERROR: Cannot install x because these package versions have conflicting deps")
    monkeypatch.setattr(kc, "_pip", pip)
    got = kc.refresh(own_stale)
    assert got["state"] == "refused" and "conflicting" in got["why"]
    assert len(pip.calls) == 1, "only the dry run"


def test_a_refresh_that_breaks_requirements_is_said_broken(own_stale, monkeypatch):
    monkeypatch.setattr(kc, "_pip", _FakePip(check=1, check_out="grove 1.0 has requirement cryptography<50"))
    got = kc.refresh(own_stale)
    assert got["state"] == "broken" and "cryptography<50" in got["why"]


def test_a_broker_refresh_goes_through_pip_sync_and_its_refusal_is_said(repo, tmp_path, monkeypatch):
    _with_origin(repo, tmp_path)
    box = kc._paths.legacy_venvs_dir() / "willow-mcp"
    box.mkdir(parents=True)
    monkeypatch.setattr(kc, "how_to_refresh", lambda v, c: "broker")
    monkeypatch.setattr(kc, "_pip", _FakePip())
    stale = {"venv": str(box), "checkout": str(repo), "units": ["w.service"], "commit": "abc"}
    asked = []
    ok = kc.refresh(stale, call=lambda tool, args: asked.append((tool, args)) or {"ok": True, "synced": True})
    assert ok["state"] == "refreshed" and asked == [("pip_sync_execute", {"checkout": str(repo), "venv": "willow-mcp"})]
    no = kc.refresh(stale, call=lambda tool, args: {"ok": False, "error": "ENOALLOW"})
    assert no["state"] == "failed" and "ENOALLOW" in no["why"]
    assert kc.refresh(stale, call=None)["state"] == "refused"


def test_run_refreshes_a_few_a_tick_files_broken_ones_and_can_be_switched_off(quiet, monkeypatch):
    stale = [{"venv": f"/v{i}", "checkout": f"/c{i}", "units": ["u.service"], "commit": "abc"} for i in range(4)]
    monkeypatch.setattr(kc, "stale_installs", lambda inv: {"status": "populated", "stale": stale})
    done = []

    def refresh(st, call=None):
        done.append(st["venv"])
        return {"venv": st["venv"], "checkout": st["checkout"], "state": "broken" if st["venv"] == "/v0" else "refreshed",
                "why": "x 1 has requirement y<2"}  # fmt: skip

    monkeypatch.setattr(kc, "refresh", refresh)
    desk = _Desk()
    kc.run(enable_mcp=True, call=desk, now=1000)
    assert done == ["/v0", "/v1"], "two refreshes a tick"
    broken = [f for f in desk.filed if f["source_ref"].startswith("keep-current:refresh-broken:")]
    assert len(broken) == 1 and broken[0]["priority"] == "high"
    monkeypatch.setenv("WILLOW_BOT_KEEP_CURRENT_REFRESH", "0")
    done.clear()
    kc.run(enable_mcp=True, call=desk, now=1300)
    assert done == [], "switched off"


def test_a_venv_reinstalled_after_its_unit_started_owes_a_restart(tmp_path, monkeypatch):
    venv = tmp_path / "v"
    dist = venv / "lib" / "python3.12" / "site-packages" / "x-1.0.dist-info"
    dist.mkdir(parents=True)
    started = int(time.time()) - 100
    os.utime(dist, (started + 50, started + 50))
    inv = {"venvs": [{"path": str(venv), "editables": [], "runs": {"units": ["a.service"]}}]}
    monkeypatch.setattr(kc, "_unit_props", lambda units: {"a.service": {"ActiveEnterTimestamp": f"@{started}"}})
    [owed] = kc.restarts_owed(inv)["owed"]
    assert owed["unit"] == "a.service" and owed["head"].startswith("install@")
    os.utime(dist, (started - 50, started - 50))
    assert kc.restarts_owed(inv)["owed"] == []


# ── one restart item per unit ────────────────────────────────────────────────

_INSTALL = {"unit": "a.service", "checkout": "/v", "head": "install@1700", "then": "", "paths": ["packages reinstalled in /v"],
            "started": 1}  # fmt: skip


def test_every_reason_a_unit_owes_its_restart_is_one_item(quiet):
    """grove-serve owed one restart to willows-grove, willow-mcp and a
    reinstalled venv at once, and was three items (2026-10-31)."""
    desk = _Desk()
    quiet["owed"] = [_OWED, {**_OWED, "checkout": "/d", "head": "c" * 40}, _INSTALL]
    kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1000)
    (filed,) = desk.filed
    assert filed["source_ref"] == "keep-current:restart:a.service"
    assert filed["title"] == "a.service runs code older than its checkout"
    assert filed["summary"] == (
        f"Since a.service started, /c moved from {'a' * 12} to {'b' * 12} (1 code files: x.py); "
        f"/d moved from {'a' * 12} to {'c' * 12} (1 code files: x.py); packages were reinstalled in /v. "
        "Restart it: systemctl --user restart a.service")


def test_a_new_commit_before_the_restart_files_nothing_new(quiet):
    desk = _Desk()
    quiet["owed"] = [_OWED]
    kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1000)
    quiet["owed"] = [{**_OWED, "head": "d" * 40}]  # the checkout moved again; still not restarted
    kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1300)
    assert len(desk.filed) == 1 and desk.resolved == []
    quiet["owed"] = []  # restarted
    kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1600)
    assert desk.resolved == ["hr-1"]


def test_items_filed_per_commit_fold_into_the_units_newest(quiet):
    """The build before keyed a restart item by (unit, commit). The newest
    becomes the unit's one item; the rest resolve saying where they went,
    and nothing is filed again."""
    desk = _Desk()
    kc._save({"ran": {}, "found": {}, "filed": {
        f"restart:a.service:{'a' * 40}": {"id": "hr-old", "since": 10, "section": "restart"},
        f"restart:a.service:{'b' * 40}": {"id": "hr-new", "since": 20, "section": "restart"},
        "restart:a.service:install@1700": {"id": "hr-mid", "since": 15, "section": "restart"},
        f"restart:gone.service:{'a' * 40}": {"id": "hr-gone", "since": 10, "section": "restart"},
    }})
    quiet["owed"] = [_OWED, _INSTALL]
    got = kc.run(enable_mcp=True, call=desk, app="willow-bot", now=1000)
    assert desk.filed == []
    assert kc.load()["filed"] == {"restart:a.service": {"id": "hr-new", "since": 20, "section": "restart"}}
    assert sorted(desk.resolved) == ["hr-gone", "hr-mid", "hr-old"]
    notes = {i["id"]: i for i in got["items"] if i["state"] == "resolved"}
    assert set(notes) == {"hr-gone", "hr-mid", "hr-old"}
    assert desk.notes["hr-old"] == "keep-current: folded into hr-new, one restart item per unit"
    assert desk.notes["hr-gone"] == "keep-current: no longer found"
