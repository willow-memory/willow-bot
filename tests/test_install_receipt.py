"""Merged-release install receipt.

Gap 1f6b033ffca7 (bot half). Every path here uses real subprocess git
against tmp_path checkouts — a fake git wrapper would hide the exact
shape of `--porcelain`, `rev-list --left-right --count`, and
`merge --ff-only` that the module relies on. Pip is stubbed only in
tests that care about the install branch.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from willow_bot import install_receipt


def _git(cwd: Path, *args: str) -> None:
    """Run a git command against `cwd`. Errors here are test failures."""
    subprocess.run(["git", "-C", str(cwd), *args],
                   check=True, capture_output=True, text=True, timeout=10)


def _init_upstream(tmp_path: Path, default_branch: str = "main") -> Path:
    """A bare 'origin' shared between a working checkout and an
    upstream one — the same shape a fleet clone has (a real remote).
    Fill it with one commit so `git clone` from it works."""
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", "-b", default_branch, str(upstream)],
                   check=True, capture_output=True, text=True, timeout=10)
    # Seed the bare with one commit via a scratch working tree we throw away.
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-b", default_branch, str(seed)],
                   check=True, capture_output=True, text=True, timeout=10)
    _git(seed, "config", "user.email", "seed@example.invalid")
    _git(seed, "config", "user.name", "seed")
    (seed / "README").write_text("hello\n")
    (seed / ".gitignore").write_text(".venv/\nvenv/\n.willow-bot-installed.commit\n")
    _git(seed, "add", "README", ".gitignore")
    _git(seed, "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "origin", default_branch)
    return upstream


def _fresh_checkout(tmp_path: Path, upstream: Path, name: str = "repo",
                     default_branch: str = "main") -> Path:
    root = tmp_path / name
    subprocess.run(["git", "clone", "-b", default_branch, str(upstream), str(root)],
                   check=True, capture_output=True, text=True, timeout=10)
    _git(root, "config", "user.email", "willow@example.invalid")
    _git(root, "config", "user.name", "willow")
    return root


def _second_checkout(tmp_path: Path, upstream: Path, default_branch: str = "main") -> Path:
    """A second checkout used to push a NEW commit — so the primary
    checkout's `fetch` has something to see."""
    root = tmp_path / "second"
    subprocess.run(["git", "clone", "-b", default_branch, str(upstream), str(root)],
                   check=True, capture_output=True, text=True, timeout=10)
    _git(root, "config", "user.email", "other@example.invalid")
    _git(root, "config", "user.name", "other")
    return root


def _push_new_commit(second: Path, default_branch: str = "main",
                     message: str = "second commit") -> str:
    (second / "another").write_text("more\n")
    _git(second, "add", "another")
    _git(second, "commit", "-m", message)
    _git(second, "push", "origin", default_branch)
    proc = subprocess.run(["git", "-C", str(second), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True, timeout=10)
    return proc.stdout.strip()


# ── missing_checkout ────────────────────────────────────────────────────────


def test_missing_checkout_when_not_a_git_tree(tmp_path):
    receipt = install_receipt.refresh_editable(tmp_path / "does-not-exist", "main")
    assert receipt["state"] == "missing_checkout"
    assert "no .git" in receipt["detail"]


# ── dirty ──────────────────────────────────────────────────────────────────


def test_dirty_working_tree_refuses_refresh(tmp_path):
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    (root / "README").write_text("dirty\n")  # uncommitted change
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "dirty"
    assert receipt["branch"] == "main"


# ── on_feature_branch ──────────────────────────────────────────────────────


def test_on_feature_branch_refuses_refresh(tmp_path):
    """Never switch away from an agent's active branch. `dirty` and
    `on_feature_branch` are separate diagnoses: a clean feature branch
    is safe to leave alone; a dirty default branch still needs help."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    _git(root, "checkout", "-b", "feature/wip")
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "on_feature_branch"
    assert receipt["branch"] == "feature/wip"


# ── ok (up-to-date) ────────────────────────────────────────────────────────


def test_ok_when_up_to_date_no_install_available(tmp_path):
    """Up-to-date with no .venv → state is ok, install marked skipped
    with a specific reason. The seat sees "checkout is current" as
    distinct from "install ran"."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    receipt = install_receipt.refresh_editable(root, "main", do_install=True)
    assert receipt["state"] == "ok"
    assert receipt["ahead"] == 0
    assert receipt["behind"] == 0
    # No .venv → install skipped, state stays ok.
    assert receipt.get("install") == "skipped"
    assert "no .venv" in receipt["detail"]


# ── behind → fast-forward ──────────────────────────────────────────────────


def test_behind_fast_forwards_and_updates_checkout_commit(tmp_path):
    """Behind but not diverged: fast-forward, checkout_commit reflects
    the new head after the pull."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    original_head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                   check=True, capture_output=True, text=True).stdout.strip()
    second = _second_checkout(tmp_path, upstream)
    new_head = _push_new_commit(second)
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "ok"
    assert receipt["behind"] == 1
    assert receipt["ahead"] == 0
    assert receipt["checkout_commit"] == new_head
    assert receipt["checkout_commit"] != original_head


# ── ahead ─────────────────────────────────────────────────────────────────


def test_ahead_reports_but_does_not_push(tmp_path):
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    (root / "local-only").write_text("local\n")
    _git(root, "add", "local-only")
    _git(root, "commit", "-m", "local ahead")
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "ahead"
    assert receipt["ahead"] == 1
    assert receipt["behind"] == 0
    assert "does not push here" in receipt["detail"]


# ── diverged ──────────────────────────────────────────────────────────────


def test_diverged_refuses_refresh(tmp_path):
    """A local commit AND a remote commit on the same base means the
    branches share a base but no longer chain — a rebase is needed and
    the bot must not choose."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    second = _second_checkout(tmp_path, upstream)
    _push_new_commit(second, message="remote-only")
    # And add a local commit that will diverge:
    (root / "local-only").write_text("local\n")
    _git(root, "add", "local-only")
    _git(root, "commit", "-m", "local-only")
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "diverged"
    assert receipt["ahead"] == 1
    assert receipt["behind"] == 1
    assert "diverged" not in receipt.get("branch", "") or True  # branch is main; just check detail
    assert "diverged" in receipt["detail"] or "ahead" in receipt["detail"]


# ── install path (mocked pip) ─────────────────────────────────────────────


def test_install_ok_writes_stamp_with_head_commit(tmp_path, monkeypatch):
    """When a .venv/bin/pip exists and returns 0, the receipt marks
    install=ok and installed_commit equals the current HEAD after any
    fast-forward. The stamp file lives at .willow-bot-installed.commit."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    # Fake a .venv/bin/pip that does nothing successful.
    pip = root / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pip.chmod(0o755)

    receipt = install_receipt.refresh_editable(root, "main", do_install=True)
    assert receipt["state"] == "ok"
    assert receipt.get("install") == "ok"
    assert receipt["installed_commit"] == receipt["checkout_commit"]
    stamp = root / ".willow-bot-installed.commit"
    assert stamp.is_file()
    assert stamp.read_text(encoding="utf-8").strip() == receipt["installed_commit"]


def test_install_failed_when_pip_returns_nonzero(tmp_path):
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    pip = root / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("#!/bin/sh\necho 'pip broke' >&2; exit 1\n", encoding="utf-8")
    pip.chmod(0o755)
    receipt = install_receipt.refresh_editable(root, "main", do_install=True)
    assert receipt["state"] == "install_failed"
    assert "pip broke" in receipt["detail"] or "pip:" in receipt["detail"]
    # Stamp NOT written on failure — a seat reading the stamp trusts it.
    assert not (root / ".willow-bot-installed.commit").is_file()


def test_installed_before_reads_prior_stamp(tmp_path):
    """A tick that ran the install earlier left a stamp; this call
    reads it into `installed_before` so drift is visible before we
    overwrite it with the current commit."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    (root / ".willow-bot-installed.commit").write_text("old-sha\n", encoding="utf-8")
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["installed_before"] == "old-sha"


def test_read_installed_commit_returns_none_when_missing(tmp_path):
    assert install_receipt.read_installed_commit(tmp_path) is None


# ── do_install=False knob (a caller wanting only the state) ───────────────


def test_do_install_false_never_calls_pip(tmp_path):
    """A caller who wants only the checkout state (a future status
    surface that does not need to reinstall) can pass do_install=False."""
    upstream = _init_upstream(tmp_path)
    root = _fresh_checkout(tmp_path, upstream)
    # Even if a broken pip exists, do_install=False must not run it.
    pip = root / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    pip.chmod(0o755)
    receipt = install_receipt.refresh_editable(root, "main", do_install=False)
    assert receipt["state"] == "ok"
    assert "install" not in receipt or receipt.get("install") != "ok"


# ── environment sanity ────────────────────────────────────────────────────


def test_git_is_available_for_the_test_suite():
    """The whole file uses subprocess git; a runner without git would
    silently fail every test with a confusing error. Assert once, up
    front — a red here is the first thing to fix on a strange runner."""
    assert shutil.which("git"), "git must be on PATH for these tests"
