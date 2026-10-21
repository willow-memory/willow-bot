"""The store read lazily: the totals and the rows' check at open, a chunk's
row only when a bite touches it, and the answers the whole table, loaded,
gave. A bad row anywhere still makes the store unreadable."""

from __future__ import annotations

import pytest

from onescript import pile
from onescript.tests.test_store_trust import KEY, broken_copy, built

BITES = ("harbour boats", "the old nets", "gulls ferry dusk", "lantern", "zzqxj")


def whole(db):
    """The table, loaded whole, as the eager loader read it."""
    rows = db.execute(
        "SELECT id, sha, n, copies, repo, path, line FROM chunk"
    ).fetchall()
    n = {r[0]: r[2] for r in rows}
    where = [(pile._clean(r[4]), pile._clean(r[5]), r[6], r[1]) for r in sorted(rows)]
    total = sum(r[3] for r in rows)
    return len(n), sum(n.values()) / len(n), total - len(n), where


def test_the_totals_are_the_whole_tables(tmp_path):
    _, _, _, db = built(tmp_path)
    c = pile.Cached(db, KEY)
    n_all, avg, extra, where = whole(db)
    assert (c.n_all, c.avg, c.extra) == (n_all, avg, extra)
    assert list(c.where.values()) == where


def test_opening_reads_no_row_and_a_bite_reads_only_its_hits(tmp_path):
    _, _, _, db = built(tmp_path)
    c = pile.Cached(db, KEY)
    assert c._rows == {}
    c.rank("lantern")
    assert 0 < len(c._rows) < c.n_all


def test_a_bite_answers_the_same_whatever_was_read_before(tmp_path):
    _, _, path, db = built(tmp_path)
    cold = [pile.Cached(pile.connect_ro(path), KEY).rank(b) for b in BITES]
    warm = pile.Cached(db, KEY)
    list(warm.where.values())  # every row read first
    assert [warm.rank(b) for b in BITES] == cold
    assert [r["state"] for r in cold].count("ranked") >= 3


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE chunk SET sha = upper(sha) WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET sha = substr(sha, 2) WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET sha = 'g' || substr(sha, 2) WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET sha = NULL WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET n = 1.5 WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET copies = 'x2' WHERE id = (SELECT max(id) FROM chunk)",
        "UPDATE chunk SET line = NULL WHERE id = (SELECT max(id) FROM chunk)",
    ],
)
@pytest.mark.parametrize("unhex", [True, False])
def test_one_bad_row_anywhere_and_the_store_is_not_read(
    tmp_path, monkeypatch, sql, unhex
):
    if not unhex:  # an SQLite without unhex: the same test by GLOB
        monkeypatch.setattr(
            pile, "_HEX", ("no_such_fn(sha) IS NOT NULL", pile._HEX[-1])
        )
    with pytest.raises(ValueError):
        broken_copy(tmp_path, sql)


def test_a_good_store_reads_by_glob_too(tmp_path, monkeypatch):
    monkeypatch.setattr(pile, "_HEX", ("no_such_fn(sha) IS NOT NULL", pile._HEX[-1]))
    _, _, _, db = built(tmp_path)
    assert pile.Cached(db, KEY).rank("harbour boats")["state"] == "ranked"


def test_a_posting_past_the_ids_names_no_chunk(tmp_path):
    c = broken_copy(
        tmp_path, "DELETE FROM chunk WHERE id = (SELECT max(id) FROM chunk)"
    )
    assert c._span is not None
    assert [c.rank(b)["state"] for b in BITES].count("unreachable") >= 1


def test_ids_with_a_gap_are_checked_one_by_one(tmp_path):
    c = broken_copy(
        tmp_path,
        "UPDATE chunk SET id = id + 1000 WHERE id = (SELECT max(id) FROM chunk)",
    )
    assert c._span is None
    lo, hi = c.db.execute("SELECT min(id), max(id) FROM chunk").fetchone()
    assert hi in c.where and hi - 1000 not in c.where  # the moved row, by its id
    assert [c.rank(b)["state"] for b in BITES].count("unreachable") >= 1
