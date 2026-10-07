"""pr_scan: a bare date is read as UTC, so it compares with git's dates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCAN = Path(__file__).resolve().parents[2] / "scripts" / "scan" / "pr_scan.py"


def git(repo: Path, *args: str, date: str = "2026-10-01T12:00:00+00:00") -> None:
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "PATH": "/usr/bin:/bin"}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        env=env,
    )


def repo_with_one_unmerged_pr(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    git(repo, "update-ref", "refs/remotes/pr/7", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    return repo


def scan(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCAN), *args], capture_output=True, text=True
    )


def test_a_bare_date_is_read_as_utc(tmp_path):
    repo = repo_with_one_unmerged_pr(tmp_path)
    out = scan("2026-09-01", str(repo))
    assert out.returncode == 0, out.stderr
    assert "| 2026-10-01 | not merged |" in out.stdout


def test_a_bare_date_and_its_utc_form_give_the_same_bytes(tmp_path):
    repo = repo_with_one_unmerged_pr(tmp_path)
    bare = scan("2026-09-01", str(repo)).stdout
    assert bare.replace("2026-09-01", "X") == scan(
        "2026-09-01T00:00:00+00:00", str(repo)
    ).stdout.replace("2026-09-01T00:00:00+00:00", "X")


def test_a_pr_older_than_the_date_is_left_out(tmp_path):
    repo = repo_with_one_unmerged_pr(tmp_path)
    assert "not merged" not in scan("2026-11-01", str(repo)).stdout


def test_git_writing_utc_as_z_reads_on_every_python():
    """git 2.55 writes `...T12:00:00Z`; Python 3.10's fromisoformat can't."""
    sys.path.insert(0, str(SCAN.parent))
    import pr_scan

    z = pr_scan.when("2026-10-01T12:00:00Z")
    assert z == pr_scan.when("2026-10-01T12:00:00+00:00")
    assert z.utcoffset().total_seconds() == 0
