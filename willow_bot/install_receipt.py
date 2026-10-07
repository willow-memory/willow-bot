"""Merged-release install receipt for a fleet checkout.

Gap ``1f6b033ffca7`` (bot half). ``sync_checkout`` in the legacy
``merge.py`` blindly checked out the default branch, pulled, and ran
``pip install -e .`` — so an operator working on a feature branch found
their tree checked out from under them, and a receipt only ever said
``ok`` or ``skip``. This module reads the checkout's state, refreshes
the editable install only when it is safe to do so, and returns a
receipt whose ``state`` names distinctly what it found:

Gap ``f982a9be2eac``: the module used to write its own stamp,
``.willow-bot-installed.commit``, straight into the checkout root — an
untracked file the dirty check then read back through plain ``git
status --porcelain``, so the stamp made the checkout look dirty to
itself and a checkout that had ever refreshed once could never refresh
again. The stamp now lives under ``$WILLOW_HOME/willow-bot/installs/``
(one file per remote, named from ``origin``'s ``owner/repo``), and the
dirty check (``_worktree_is_clean``) looks at TRACKED files only
(``--untracked-files=no``) so an unrelated untracked file never blocks
a refresh either. A checkout that still carries the old in-checkout
stamp has it read once (``read_installed_commit``'s one-release
fallback) and removed the next time a refresh succeeds.

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

The receipt also carries ``checkout_commit`` (HEAD after any pull),
``installed_commit`` (the commit the ``pip install -e .`` marked into
the stamp), and ``marker_path`` (where that stamp lives). A future
tick reading this receipt tells drift by comparing the two commits.

Never opens a network socket outside a single bounded ``git fetch``,
never switches branches, never creates a venv. If a venv is present at
``.venv/`` we use its ``pip``; otherwise we ask the broker
``pip_sync_execute`` (ideas A.2) to install into the vault venv named by
the allowlist for this remote. If MCP is off or the remote is not on the
allowlist, the receipt reports ``install_skipped``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from willow_bot.paths import bot_dir, willow_home

log = logging.getLogger("willow-bot.install_receipt")


_GIT_TIMEOUT_S = 30
_FETCH_TIMEOUT_S = 60
_PIP_TIMEOUT_S = 300
_BROKER_PIP_TIMEOUT_S = 600

#: Legacy stamp name, once written straight into the checkout root
#: (gap f982a9be2eac). Kept as a constant so the writer's cleanup and
#: the reader's one-release fallback name the exact same file.
_LEGACY_MARKER_NAME = ".willow-bot-installed.commit"

#: Thin fallback when the broker allowlist file is absent (same seeds as
#: willow-mcp's bundled ``pip_sync_allowlist.json``).
_DEFAULT_BROKER_PAIRS: dict[str, dict[str, Any]] = {
    "hornbook-knowledge/Jeles": {"venv": "willow-mcp", "extras": ["connectors"]},
    "willow-memory/willow-bot": {"venv": "willow-bot", "extras": []},
    "willow-memory/willow-mcp": {"venv": "willow-mcp", "extras": []},
    "willow-memory/willow-gate": {"venv": "willow-mcp", "extras": []},
}


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
    """Tracked files only (gap f982a9be2eac): an untracked file — the
    bot's own former in-checkout install marker, a build artifact, a
    stray scratch file — must never itself refuse a refresh. A modified
    or staged TRACKED file still does."""
    proc = _git(root, "status", "--porcelain", "--untracked-files=no")
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


def _origin_slug(root: Path) -> str | None:
    """``owner/repo`` from origin (same shape as the broker allowlist keys)."""
    slug = _repo_slug(root)
    if not slug:
        return None
    return slug.replace("__", "/", 1)


def _broker_pair_for(root: Path) -> dict[str, Any] | None:
    slug = _origin_slug(root)
    if not slug:
        return None
    pairs: dict[str, Any] = dict(_DEFAULT_BROKER_PAIRS)
    allow = willow_home() / "constitutional" / "pip_sync_allowlist.json"
    if allow.is_file():
        try:
            doc = json.loads(allow.read_text(encoding="utf-8"))
            pairs.update(doc.get("pairs") or {})
        except (OSError, json.JSONDecodeError):
            pass
    if slug in pairs:
        return dict(pairs[slug])
    lowered = {k.lower(): v for k, v in pairs.items()}
    hit = lowered.get(slug.lower())
    return dict(hit) if hit else None


def _broker_pip_sync(root: Path) -> tuple[str, str, dict[str, Any]]:
    """Ask willow-mcp ``pip_sync_execute``. Returns
    ``(install_label, detail, extra_receipt_fields)``.
    ``install_label`` is ``broker_pip_sync`` | ``install_failed`` | ``skipped``.
    """
    pair = _broker_pair_for(root)
    if pair is None:
        return "skipped", f"no broker allowlist pair for {_origin_slug(root)!r}", {}
    # Same env gate as tick.mcp_enabled — do not import tick (circular).
    if os.environ.get("WILLOW_BOT_MCP", "").strip().lower() not in ("1", "true", "yes"):
        return "skipped", "WILLOW_BOT_MCP not enabled; broker pip_sync skipped", {}
    try:
        from willow_bot.steward import mcp_client
        from willow_bot.steward.config import app_id
    except Exception as exc:  # noqa: BLE001
        return "skipped", f"mcp client unavailable: {exc}", {}
    args: dict[str, Any] = {
        "app_id": app_id(),
        "checkout": str(root),
        "venv": pair.get("venv") or "",
    }
    extras = list(pair.get("extras") or [])
    if extras:
        args["extras"] = extras
    try:
        result = mcp_client.call("pip_sync_execute", args, timeout_s=_BROKER_PIP_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        return "install_failed", f"broker pip_sync: {exc}"[:400], {}
    if not isinstance(result, dict):
        return "install_failed", f"broker pip_sync: unexpected result {result!r}"[:400], {}
    if result.get("ok") and result.get("synced"):
        return "broker_pip_sync", "installed via broker pip_sync_execute", {
            "broker_venv": result.get("venv"),
            "broker_receipt_id": result.get("receipt_id"),
            "broker_after": result.get("after"),
        }
    errno = result.get("error") or "EINSTALL"
    reason = result.get("reason") or result.get("error") or "broker refused"
    return "install_failed", f"broker pip_sync {errno}: {reason}"[:400], {
        "broker_error": errno,
    }


def _legacy_marker_path(root: Path) -> Path:
    """Where the stamp used to live, straight in the checkout root."""
    return root / _LEGACY_MARKER_NAME


def _repo_slug(root: Path) -> str | None:
    """``<owner>__<repo>`` read from ``origin``'s remote URL, or None
    when it cannot be resolved (no remote, no git). Handles both
    ``git@host:owner/repo.git`` and ``https://host/owner/repo.git``
    shapes; a trailing ``.git`` is stripped."""
    proc = _git(root, "remote", "get-url", "origin")
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip()
    if not url:
        return None
    if url.endswith(".git"):
        url = url[:-4]
    parts = [p for p in re.split(r"[:/]", url) if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if not owner or not repo:
        return None
    return f"{owner}__{repo}"


def marker_path(root: Path) -> Path:
    """Where this checkout's install stamp lives now: one file per
    remote under ``$WILLOW_HOME/willow-bot/installs/``, so the stamp
    never touches — and never dirties — the checkout it describes
    (gap f982a9be2eac). Falls back to the legacy in-checkout path only
    when ``origin`` cannot be resolved (no remote configured) — the
    stamp still has to live somewhere."""
    slug = _repo_slug(root)
    if slug:
        return bot_dir() / "installs" / f"{slug}.commit"
    return _legacy_marker_path(root)


def _write_installed_stamp(root: Path, commit: str) -> Path:
    """Sidecar stamp with the commit sha ``pip install -e .`` was run
    on. A tick reading the stamp tells drift when HEAD moves without a
    reinstall (an editable install still resolves entrypoints from the
    source at import time, but a new dependency in pyproject.toml only
    takes effect after a re-run of pip). Lives under ``$WILLOW_HOME``
    now, never in the checkout (gap f982a9be2eac); a legacy in-checkout
    stamp left over from before that move is removed here, on the
    first successful write after the upgrade, so an old checkout
    cleans itself."""
    path = marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(commit + "\n", encoding="utf-8")
    legacy = _legacy_marker_path(root)
    if legacy != path and legacy.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass
    return path


def read_installed_commit(root: Path) -> str | None:
    """Return the recorded install commit for this checkout, or None if
    the stamp is missing (never installed, or installed by a step older
    than this module). Reads the ``$WILLOW_HOME`` marker first; a
    checkout upgraded from before the marker moved out of the tree
    still has its old in-checkout stamp, read as a one-release
    fallback."""
    path = marker_path(root)
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    legacy = _legacy_marker_path(root)
    if legacy != path and legacy.is_file():
        try:
            return legacy.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
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
    receipt["marker_path"] = str(marker_path(repo_dir))

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
    if pip is not None:
        ok, err = _run_editable_install(repo_dir, pip)
        if not ok:
            receipt.update(state="install_failed", detail=f"pip: {err}")
            return receipt
        install_label = "local_venv"
        detail = "installed via checkout .venv"
    else:
        label, detail, extra = _broker_pip_sync(repo_dir)
        receipt.update(extra)
        if label == "skipped":
            receipt.update(state="ok", install="skipped", detail=detail)
            return receipt
        if label == "install_failed":
            receipt.update(state="install_failed", detail=detail)
            return receipt
        install_label = label

    commit = receipt["checkout_commit"]
    if commit:
        _write_installed_stamp(repo_dir, commit)
        receipt["installed_commit"] = commit
    receipt["state"] = "ok"
    receipt["install"] = install_label
    receipt["detail"] = detail
    return receipt
