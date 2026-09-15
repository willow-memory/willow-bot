"""The install step (2026-09-15): after the sweep brings a merge home,
the tick refreshes each pulled checkout's editable install.

Gap ``1f6b033ffca7`` (bot half). The read of the checkout's ``origin/HEAD``
for the default branch is real (a tmp git repo); the pip refresh is
short-circuited by seeding a checkout with no ``.venv``, which
``install_receipt.refresh_editable`` reports as ``install=skipped``,
state ``ok`` — the outcome we want to prove is wired end-to-end without
paying to run pip in a test.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from willow_bot.steward import tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    monkeypatch.delenv("LOKI_PR_WATCH_STATE", raising=False)
    return tmp_path


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def clone(tmp_path):
    """A real git checkout, initialised on ``main``, with an ``origin`` remote
    pointing at a bare repo so ``origin/HEAD`` resolves. No ``.venv`` — the
    install refresh reports ``install=skipped``, state ``ok``."""
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main", str(upstream)],
                   check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "README").write_text("hi\n")
    (seed / ".gitignore").write_text(".venv/\nvenv/\n.willow-bot-installed.commit\n")
    _git(seed, "add", "README", ".gitignore")
    _git(seed, "commit", "-q", "-m", "seed\n\nPersona: willow")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "-q", "origin", "main")
    clone_dir = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(upstream), str(clone_dir)],
                   check=True, capture_output=True)
    _git(clone_dir, "config", "user.email", "t@t")
    _git(clone_dir, "config", "user.name", "t")
    head = _git(clone_dir, "rev-parse", "HEAD")
    return {"repo": "o/r", "checkout": str(clone_dir),
            "before": head, "after": head}


def _sweep(*ranges):
    return {"event": "steward_sweep", "status": "ok", "ranges": list(ranges)}


def test_nothing_came_home_is_an_honest_ok(home, capsys):
    r = tick.run_install_receipts(sweep={"ranges": []})
    assert r["status"] == "ok" and r["receipts"] == []
    assert r["detail"] == "nothing came home"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "steward_install" and line["ranges"] == 0


def test_no_sweep_at_all_is_the_same_honest_ok(home):
    """A missing sweep dict is not a crash — the loop can call this even when
    the sweep failed to run at all."""
    r = tick.run_install_receipts(sweep=None)
    assert r["status"] == "ok" and r["receipts"] == [] and r["ranges"] == 0


def test_each_pulled_range_yields_a_receipt(home, clone):
    r = tick.run_install_receipts(sweep=_sweep(clone))
    assert r["status"] == "ok" and r["ranges"] == 1
    (entry,) = r["receipts"]
    assert entry["repo"] == "o/r"
    assert entry["checkout"] == clone["checkout"]
    assert entry["state"] == "ok"
    # No .venv, so pip did not run — install=skipped, state=ok.
    assert entry.get("install") == "skipped"
    assert entry["branch"] == "main"
    assert entry["default_branch"] == "main"


def test_default_branch_is_read_from_origin_head_when_the_sweep_did_not_supply_it(home, clone):
    """The sweep receipt does not include a range's default branch. This step
    reads it from the checkout's ``origin/HEAD`` symref — which git set at
    clone time — so a repo with ``master`` still lands on the right branch."""
    r = tick.run_install_receipts(sweep=_sweep(clone))
    (entry,) = r["receipts"]
    assert entry["default_branch"] == "main"


def test_a_range_that_carries_default_branch_uses_it_directly(home, clone):
    """If a sweep is ever taught to include the default branch per range,
    this step honours it without falling back to reading the checkout."""
    rng = {**clone, "default_branch": "main"}
    r = tick.run_install_receipts(sweep=_sweep(rng))
    (entry,) = r["receipts"]
    assert entry["state"] == "ok" and entry["default_branch"] == "main"


def test_a_missing_checkout_is_a_line_not_a_dead_step(home, tmp_path):
    rng = {"repo": "o/r", "checkout": str(tmp_path / "does_not_exist"),
           "before": "x", "after": "y"}
    r = tick.run_install_receipts(sweep=_sweep(rng))
    (entry,) = r["receipts"]
    # No .git means the discovery of default_branch fails first, which is the
    # correct refusal: we cannot tell what branch was pulled.
    assert entry["state"] == "unknown_default_branch"
    assert entry["repo"] == "o/r"
    assert "origin/HEAD" in entry["detail"]


def test_a_range_where_origin_head_is_absent_is_refused_not_guessed(home, tmp_path):
    """A checkout initialised without an ``origin/HEAD`` symref (a fresh
    ``git init`` with a remote added later) has no default branch to read.
    The step refuses rather than guess ``main`` and risk switching an agent
    off ``master``."""
    r = tmp_path / "no-origin-head"
    r.mkdir()
    _git(r, "init", "-q", "-b", "trunk")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a").write_text("0\n")
    _git(r, "add", "a")
    _git(r, "commit", "-q", "-m", "base")
    # No origin remote at all — origin/HEAD cannot resolve.
    rng = {"repo": "o/r", "checkout": str(r), "before": "1", "after": "2"}
    out = tick.run_install_receipts(sweep=_sweep(rng))
    (entry,) = out["receipts"]
    assert entry["state"] == "unknown_default_branch"


def test_one_range_raising_does_not_kill_the_step(home, clone, monkeypatch):
    """A stray exception inside refresh_editable is reported per range so a
    second range in the same sweep still gets its receipt."""
    from willow_bot import install_receipt as install_mod

    original = install_mod.refresh_editable
    seen: list[str] = []

    def flaky(path, branch, **kw):
        seen.append(str(path))
        if str(path) == clone["checkout"]:
            raise RuntimeError("simulated pip crash inside test")
        return original(path, branch, **kw)

    monkeypatch.setattr(install_mod, "refresh_editable", flaky)

    other = {**clone, "repo": "o/r2"}
    r = tick.run_install_receipts(sweep=_sweep(clone, other))
    # Two receipts (both ranges pointed at the same clone, second one raised
    # too since the guard sees the same path). Both entries land — the step
    # keeps going.
    assert len(r["receipts"]) == 2
    assert all(e["state"] == "error" for e in r["receipts"])
    assert all("simulated pip crash" in e["detail"] for e in r["receipts"])


def test_the_receipt_lands_in_the_tick_log(home, clone):
    tick.run_install_receipts(sweep=_sweep(clone))
    log = home / "willow-bot" / "steward_ticks.jsonl"
    assert log.is_file()
    lines = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert any(ln["event"] == "steward_install" for ln in lines)


def test_the_cli_knows_install_receipts(monkeypatch, home):
    """Wiring only: with MCP off, ``run_sweep`` returns absent, so
    ``run_install_receipts`` sees no ranges and reports ok — the CLI itself
    exits 0."""
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    assert tick.main(["install-receipts"]) == 0


def test_the_loop_wires_install_after_resolve(monkeypatch):
    """Prove the step is called from ``run_loop`` between resolve and mirror."""
    order: list[str] = []

    monkeypatch.setattr(tick, "run_once", lambda *_a, **_k: order.append("once"))
    monkeypatch.setattr(tick, "run_sweep", lambda *_a, **_k: (order.append("sweep") or {"ranges": []}))
    monkeypatch.setattr(tick, "run_resolve", lambda *_a, **_k: order.append("resolve"))
    monkeypatch.setattr(tick, "run_install_receipts", lambda *_a, **_k: order.append("install"))
    monkeypatch.setattr(tick, "run_mirror", lambda *_a, **_k: order.append("mirror"))
    monkeypatch.setattr(tick, "run_ci", lambda *_a, **_k: order.append("ci"))
    monkeypatch.setattr(tick, "run_audit", lambda *_a, **_k: order.append("audit"))
    monkeypatch.setattr("willow_bot.steward.heartbeat.run_heartbeat",
                        lambda *_a, **_k: order.append("heartbeat"))

    # Break the loop after one iteration.
    class _Stop(Exception):
        pass

    def sleep_once(_):
        if "sweep" in order:
            raise _Stop()

    monkeypatch.setattr(tick.time, "sleep", sleep_once)
    with pytest.raises(_Stop):
        tick.run_loop(0.0)

    assert order.index("resolve") < order.index("install") < order.index("mirror")
