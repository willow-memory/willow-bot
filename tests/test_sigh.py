from pathlib import Path

import sigh


def _db(tmp_path: Path) -> Path:
    return tmp_path / "sigh-test.db"


def test_bump_fail_grows_streak(tmp_path):
    path = _db(tmp_path)
    assert sigh.bump_fail("acme/repo", 1, path) == 1
    assert sigh.bump_fail("acme/repo", 1, path) == 2
    assert sigh.bump_fail("acme/repo", 1, path) == 3


def test_reset_zeros_streak(tmp_path):
    path = _db(tmp_path)
    sigh.bump_fail("acme/repo", 1, path)
    sigh.bump_fail("acme/repo", 1, path)
    sigh.reset("acme/repo", 1, path)
    assert sigh.bump_fail("acme/repo", 1, path) == 1


def test_sigh_line_none_below_three():
    assert sigh.sigh_line(0) is None
    assert sigh.sigh_line(1) is None
    assert sigh.sigh_line(2) is None


def test_sigh_line_three():
    assert sigh.sigh_line(3) == "sigh"


def test_sigh_line_four():
    assert sigh.sigh_line(4) == "sighh"


def test_sigh_line_fifteen():
    assert sigh.sigh_line(15) == "sigh" + "h" * 12
    assert sigh.sigh_line(15) == "sighhhhhhhhhhhhh"
