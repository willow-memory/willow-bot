"""tests/test_rebase_shame.py — force-push tracking and the shame header text."""
from __future__ import annotations

import rebase_shame

REPO = "forge-play/Forge"
REF = "feature/wobbly"


def test_increment_grows_the_count(tmp_path):
    db = tmp_path / "shame.db"
    assert rebase_shame.increment(REPO, REF, db) == 1
    assert rebase_shame.increment(REPO, REF, db) == 2
    assert rebase_shame.increment(REPO, REF, db) == 3


def test_get_unknown_and_known(tmp_path):
    db = tmp_path / "shame.db"
    assert rebase_shame.get(REPO, REF, db) == 0
    rebase_shame.increment(REPO, REF, db)
    rebase_shame.increment(REPO, REF, db)
    assert rebase_shame.get(REPO, REF, db) == 2


def test_header_below_threshold():
    assert rebase_shame.header(2) == ""


def test_header_rewritten_times():
    assert "rewritten three times" in rebase_shame.header(3)


def test_header_remembers_none():
    assert "remembers none of its former selves" in rebase_shame.header(7)


def test_header_no_longer_what_it_was():
    h = rebase_shame.header(12)
    assert "no longer what it was" in h
    assert "12" in h
