"""The record survives a crash and a race: a torn last line is not yet written
(ignored, reported, never a chain break) while a garble anywhere else is a
break; an append refuses to land after a torn tail; write_file's pile pointer
is under the same lock as its row; a new record fsyncs its directory and an
fsync error raises, once."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import record as rec_mod  # noqa: E402
from onescript.record import Record, TornTail, Verified  # noqa: E402

TOKEN = object()
WHO = Verified("willow", "self-declared", TOKEN)


def make(box: Path) -> Record:
    return Record(box, lambda: "2026-01-01T00:00:00Z", "v", TOKEN)


def test_a_healthy_writer_leaves_the_same_bytes(tmp_path):
    r = make(tmp_path)
    row = r.append("note", WHO, text="a")
    assert r.path.read_bytes() == (rec_mod.canon(row) + "\n").encode()
    assert r.verify_chain() == [] and r.torn_tail() is None


def test_a_torn_last_line_is_ignored_and_reported_not_a_break(tmp_path):
    r = make(tmp_path)
    r.append("note", WHO, text="a")
    with r.path.open("ab") as f:
        f.write(b'{"n": 1, "kind": "no')
    assert [x["text"] for x in r.rows()] == ["a"]
    assert r.verify_chain() == []
    assert "line 2: unterminated" in r.torn_tail()


def test_a_complete_row_missing_only_its_newline_is_still_not_yet_written(tmp_path):
    r = make(tmp_path)
    r.append("note", WHO, text="a")
    r.append("note", WHO, text="b")
    r.path.write_bytes(r.path.read_bytes().rstrip(b"\n"))
    assert [x["text"] for x in r.rows()] == ["a"]
    assert r.torn_tail() is not None


def test_a_garbled_line_mid_file_is_a_break(tmp_path):
    r = make(tmp_path)
    r.append("note", WHO, text="a")
    with r.path.open("ab") as f:
        f.write(b"{garbled\n")
    assert r.verify_chain() == ["line 2: garbled, not a record row"]
    assert r.torn_tail() is None


def test_append_refuses_after_a_torn_tail_and_writes_nothing(tmp_path):
    r = make(tmp_path)
    r.append("note", WHO, text="a")
    with r.path.open("ab") as f:
        f.write(b'{"n": 1')
    before = r.path.read_bytes()
    with pytest.raises(TornTail, match="unterminated"):
        r.append("note", WHO, text="b")
    with pytest.raises(TornTail):
        r.write_file(WHO, "x.txt", b"hi")
    assert r.path.read_bytes() == before


def test_first_row_fsyncs_the_directory_and_later_rows_do_not(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(rec_mod, "_fsync_dir", lambda p: synced.append(p))
    r = make(tmp_path)
    r.append("note", WHO, text="a")
    r.append("note", WHO, text="b")
    assert synced == [tmp_path]


def test_an_fsync_error_raises_and_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def bad(fd):
        calls.append(fd)
        raise OSError(5, "EIO")

    monkeypatch.setattr(os, "fsync", bad)
    r = make(tmp_path)
    with pytest.raises(OSError):
        r.append("note", WHO, text="a")
    assert len(calls) == 1


def test_the_directory_fsync_error_raises_once(tmp_path, monkeypatch):
    real, calls = os.fsync, []

    def second_fails(fd):
        calls.append(fd)
        if len(calls) == 2:  # the file's fsync passed; the directory's fails
            raise OSError(5, "EIO")
        real(fd)

    monkeypatch.setattr(os, "fsync", second_fails)
    with pytest.raises(OSError):
        make(tmp_path).append("note", WHO, text="a")
    assert len(calls) == 2


def test_write_file_under_the_lock_does_not_deadlock(tmp_path):
    r = make(tmp_path)
    r.write_file(WHO, "a.txt", b"x")
    assert [i["where"] for i in r.pile()["items"]] == ["a.txt"]
    assert r.verify_chain() == []


def _writer(args):
    box, w, go = args
    r = make(Path(box))
    go.wait()
    for i in range(8):
        r.write_file(WHO, f"f{w}_{i}.txt", f"{w}:{i}".encode())


@pytest.mark.skipif(sys.platform == "win32", reason="record: no fcntl here")
def test_concurrent_write_file_loses_no_pointer(tmp_path):
    ctx = mp.get_context("spawn")
    go = ctx.Manager().Event()
    n = 5
    with ctx.Pool(n) as pool:
        job = pool.map_async(_writer, [(str(tmp_path), w, go) for w in range(n)])
        go.set()
        job.get(timeout=120)
    r = make(tmp_path)
    got = {
        i["where"] for i in json.loads((tmp_path / "pile.json").read_text())["items"]
    }
    assert got == {f"f{w}_{i}.txt" for w in range(n) for i in range(8)}
    assert r.verify_chain() == [] and len(r.rows()) == n * 8
