"""Merged-release install receipt for a fleet checkout.

Gap ``1f6b033ffca7`` (bot half). ``sync_checkout`` in the legacy
``merge.py`` blindly checked out the default branch, pulled, and ran
``pip install -e .`` — so an operator working on a feature branch found
their tree checked out from under them, and a receipt only ever said
``ok`` or ``skip``. This module reads the checkout's state, refreshes
the editable install only when it is safe to do so, and returns a
receipt whose ``state`` names distinctly what it found:

- ``missing_checkout`` — the path is not a git tree
- ``dirty`` — the working tree carries uncommitted changes
- ``on_feature_branch`` — HEAD is not on the default branch (an agent
  is working there; the bot must not switch away behind their back)
- ``diverged`` — local and remote default have both moved (a rebase in
  flight, a manual pull with commits ahead)
- ``ahead`` — local default is ahead of remote (an unpushed commit; the
  bot does not push here)
- ``install_failed`` — refresh reached ``pip install -e .`` and pip
  returned non-zero
- ``ok`` — HEAD is on default, tree is clean, local matches or was
  fast-forwarded to remote, and ``pip install -e .`` succeeded; the
  receipt records the installed commit

The receipt also carries ``checkout_commit`` (HEAD after any pull) and
``installed_commit`` (the commit the ``pip install -e .`` marked into
a sidecar ``.willow-bot-installed.commit`` file). A future tick reading
this receipt tells drift by comparing the two.

Never opens a network socket outside a single bounded ``git fetch``,
never switches branches, never creates a venv. If a venv is present at
``.venv/`` we use its ``pip``; otherwise the receipt reports
``install_skipped`` (state stays ``on_default`` for the seat to read).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("willow-bot.install_receipt")


_GIT_TIMEOUT_S = 30
_FETCH_TIMEOUT_S = 60
_PIP_TIMEOUT_S = 300


def _git(root: Path, *args: str, timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    """One git call, bounded by timeout, capturing both streams. Never
    raises on non-zero — the caller inspects returncode and stderr."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False, timeout=timeout,
    )


def _current_branch(root: Path) -> str | None:
    proc = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if proc.returncode != 0:
        return None
    v = proc.stdout.strip()
    return v or None


def _head_sha(root: Path) -> str | None:
    proc = _git(root, "rev-parse", "HEAD")
    if proc.returncode != 0:
        return None
    v = proc.stdout.strip()
    return v or None


def _worktree_is_clean(root: Path) -> bool:
    proc = _git(root, "status", "--porcelain=v1")
    if proc.returncode != 0:
        return False  # cannot tell → treat as not-clean; refuse to refresh
    return proc.stdout.strip() == ""


def _fetch(root: Path, remote: str = "origin") -> tuple[bool, str]:
    proc = _git(root, "fetch", remote, timeout=_FETCH_TIMEOUT_S)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:300]
    return True, ""


def _left_right_count(root: Path, local_ref: str, remote_ref: str) -> tuple[int, int] | None:
    """``git rev-list --left-right --count local...remote`` returns
    ``"L\\tR\\n"``. L is commits on local not on remote; R is commits on
    remote not on local. (0, 0) means up-to-date, (0, N) means local
    behind (safe fast-forward), (N, 0) ahead (won't push here), (N, M)
    diverged."""
    proc = _git(root, "rev-list", "--left-right", "--count",
                f"{local_ref}...{remote_ref}")
    if proc.returncode != 0:
        return None
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _fast_forward(root: Path, remote_ref: str) -> tuple[bool, str]:
    """``git merge --ff-only <ref>`` fast-forwards or fails; never
    creates a merge commit. Not `git pull` — pull's default of `merge`
    can make a merge commit on a diverged checkout, which is what the
    steward is trying to AVOID."""
    proc = _git(root, "merge", "--ff-only", remote_ref)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:300]
    return True, ""


def _venv_pip(root: Path) -> Path | None:
    for candidate in (root / ".venv" / "bin" / "pip",
                      root / "venv" / "bin" / "pip"):
        if candidate.is_file():
            return candidate
    return None


def _run_editable_install(root: Path, pip: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(pip), "install", "-q", "-e", "."],
        cwd=str(root), capture_output=True, text=True, check=False,
        timeout=_PIP_TIMEOUT_S,
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[-500:]
    return True, ""


def _write_installed_stamp(root: Path, commit: str) -> Path:
    """Sidecar stamp with the commit sha ``pip install -e .`` was run
    on. A tick reading the stamp tells drift when HEAD moves without a
    reinstall (an editable install still resolves entrypoints from the
    source at import time, but a new dependency in pyproject.toml only
    takes effect after a re-run of pip)."""
    path = root / ".willow-bot-installed.commit"
    path.write_text(commit + "\n", encoding="utf-8")
    return path


def read_installed_commit(root: Path) -> str | None:
    """Return the recorded install commit for this checkout, or None if
    the stamp is missing (never installed, or installed by a step older
    than this module)."""
    path = root / ".willow-bot-installed.commit"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def refresh_editable(
    repo_dir: Path,
    default_branch: str,
    *,
    remote: str = "origin",
    do_install: bool = True,
) -> dict[str, Any]:
    """Bring a checkout to the current remote default and refresh its
    editable install, when it is safe to do so.

    Returns a receipt: ``{state, checkout_commit, installed_commit,
    installed_before, branch, ahead, behind, detail}``. Never switches
    branches. Never resolves a merge conflict. Never creates a venv.
    """
    receipt: dict[str, Any] = {
        "state": "ok",
        "path": str(repo_dir),
        "default_branch": default_branch,
        "branch": None,
        "checkout_commit": None,
        "installed_commit": None,
        "installed_before": None,
        "ahead": None,
        "behind": None,
    }
    if not (repo_dir / ".git").exists():
        receipt.update(state="missing_checkout", detail=f"no .git under {repo_dir}")
        return receipt

    branch = _current_branch(repo_dir)
    receipt["branch"] = branch
    receipt["checkout_commit"] = _head_sha(repo_dir)
    receipt["installed_before"] = read_installed_commit(repo_dir)

    if not _worktree_is_clean(repo_dir):
        receipt.update(state="dirty",
                       detail="working tree has uncommitted changes; refresh refused")
        return receipt

    if branch != default_branch:
        receipt.update(state="on_feature_branch",
                       detail=(f"HEAD is on {branch!r}, not the default {default_branch!r} — "
                               f"an agent may be working there; the bot must not switch away"))
        return receipt

    ok, err = _fetch(repo_dir, remote)
    if not ok:
        receipt.update(state="fetch_failed", detail=f"fetch: {err}")
        return receipt

    counts = _left_right_count(repo_dir, "HEAD", f"{remote}/{default_branch}")
    if counts is None:
        receipt.update(state="fetch_failed",
                       detail=f"could not count commits vs {remote}/{default_branch}")
        return receipt
    ahead, behind = counts
    receipt["ahead"] = ahead
    receipt["behind"] = behind

    if ahead > 0 and behind > 0:
        receipt.update(state="diverged",
                       detail=(f"local is {ahead} ahead and {behind} behind {remote}/"
                               f"{default_branch}; refresh refused"))
        return receipt
    if ahead > 0:
        receipt.update(state="ahead",
                       detail=(f"local is {ahead} ahead of {remote}/{default_branch}; "
                               f"the bot does not push here"))
        return receipt

    if behind > 0:
        ok, err = _fast_forward(repo_dir, f"{remote}/{default_branch}")
        if not ok:
            receipt.update(state="fetch_failed", detail=f"ff-only: {err}")
            return receipt
        receipt["checkout_commit"] = _head_sha(repo_dir)

    if not do_install:
        receipt.update(state="ok", detail="install skipped by caller")
        return receipt

    pip = _venv_pip(repo_dir)
    if pip is None:
        receipt.update(state="ok", install="skipped",
                       detail=f"no .venv/bin/pip under {repo_dir}; install skipped")
        return receipt

    ok, err = _run_editable_install(repo_dir, pip)
    if not ok:
        receipt.update(state="install_failed", detail=f"pip: {err}")
        return receipt

    commit = receipt["checkout_commit"]
    if commit:
        _write_installed_stamp(repo_dir, commit)
        receipt["installed_commit"] = commit
    receipt["state"] = "ok"
    receipt["install"] = "ok"
    return receipt
