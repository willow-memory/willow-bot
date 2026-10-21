"""The record has one writer at a time (CONST-VI: append-only, and the chain
holds). Two per-prompt hook calls are two processes on one box; without a lock
each reads the same tail and links to the same prev, which forks the chain.
This spawns several and attempts exactly that."""

from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript.record import Record, Verified  # noqa: E402

WRITERS, EACH = 6, 15
TOKEN = object()


def _write(args):
    box, w, go = args
    rec = Record(Path(box), lambda: "2026-01-01T00:00:00Z", "v", TOKEN)
    who = Verified("willow", "self-declared", TOKEN)
    go.wait()  # start together, so the tails really are read at once
    for i in range(EACH):
        rec.append("note", who, text=f"{w}:{i}")


@pytest.mark.skipif(sys.platform == "win32", reason="record: no fcntl here")
def test_concurrent_writers_keep_one_chain(tmp_path):
    ctx = mp.get_context("spawn")
    go = ctx.Manager().Event()
    with ctx.Pool(WRITERS) as pool:
        job = pool.map_async(_write, [(str(tmp_path), w, go) for w in range(WRITERS)])
        go.set()
        job.get(timeout=120)
    rec = Record(tmp_path, lambda: "", "v", TOKEN)
    assert rec.verify_chain() == []
    rows = rec.rows()
    assert len(rows) == WRITERS * EACH
    assert {r["text"] for r in rows} == {
        f"{w}:{i}" for w in range(WRITERS) for i in range(EACH)
    }
