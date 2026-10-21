"""The record remembers its last parse, so one verb doesn't re-read every row
five times (a prompt's cost grew with every row ever written). What it may
never do is answer from memory when the file is not what it parsed. Each
guard is tested by the thing it forbids."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import record  # noqa: E402
from onescript.record import Record, Verified  # noqa: E402

TOKEN = object()
WHO = Verified("willow", "self-declared", TOKEN)


def rec(box: Path) -> Record:
    return Record(box, lambda: "2026-01-01T00:00:00Z", "v", TOKEN)


def counting(monkeypatch) -> list[int]:
    n = [0]
    real = Record._scan_file

    def counted(self):
        n[0] += 1
        return real(self)

    monkeypatch.setattr(Record, "_scan_file", counted)
    return n


def test_one_parse_serves_a_verbs_reads_and_its_own_appends(tmp_path, monkeypatch):
    r = rec(tmp_path)
    for i in range(3):
        r.append("note", WHO, text=str(i))
    parses = counting(monkeypatch)
    r2 = rec(tmp_path)  # a new process's view: it parses once
    r2.rows()
    r2.append("note", WHO, text="mine")
    r2.rows()
    r2.rows()
    assert parses[0] == 1
    assert [x["text"] for x in r2.rows()] == ["0", "1", "2", "mine"]


def test_another_writers_row_is_seen(tmp_path):
    a, b = rec(tmp_path), rec(tmp_path)
    a.append("note", WHO, text="a")
    assert len(b.rows()) == 1
    a.append("note", WHO, text="a2")
    assert [x["text"] for x in b.rows()] == ["a", "a2"]
    b.append("note", WHO, text="b")  # links to a2, not to the stale tail
    assert rec(tmp_path).verify_chain() == []


def test_a_rewrite_that_keeps_size_and_mtime_is_still_caught(tmp_path):
    r = rec(tmp_path)
    r.append("note", WHO, text="aaaa")
    r.append("note", WHO, text="bbbb")
    r.rows()  # remembered
    p = tmp_path / "record.jsonl"
    st = p.stat()
    p.write_bytes(p.read_bytes().replace(b'"aaaa"', b'"zzzz"'))  # same length
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert r.verify_chain(), "verify_chain read a remembered parse"


def test_a_callers_edit_never_reaches_the_remembered_rows(tmp_path):
    r = rec(tmp_path)
    r.append("note", WHO, text="x")
    rows = r.rows()
    rows[0]["text"] = "edited"
    rows.append({"forged": True})
    assert [x["text"] for x in r.rows()] == ["x"]


def test_a_torn_tail_is_read_from_the_file(tmp_path):
    r = rec(tmp_path)
    r.append("note", WHO, text="x")
    r.rows()
    with (tmp_path / "record.jsonl").open("a") as f:
        f.write('{"n": 1')
    assert r.torn_tail() is not None
    try:
        r.append("note", WHO, text="y")
    except record.TornTail:
        pass
    else:
        raise AssertionError("appended after a torn tail")
