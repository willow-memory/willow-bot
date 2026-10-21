"""Reading from the human's sealed tip (the operator, 2026-10-21: "1 yes" to
using the sealed anchor as a reader's resume point). The tip readers must
give exactly what a whole-file parse gives, on every shape of record, and
fall back to the whole file whenever the tip doesn't check out. A prompt on a
box with a sealed tip writes the same bytes as on the same box without one,
and parses no whole file."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import opening, prompt_path, record  # noqa: E402
from onescript.record import Record, Verified  # noqa: E402
from onescript.tests.test_api import cfg  # noqa: E402,F401
from onescript.tests.test_store import BITE, box  # noqa: E402,F401

TOKEN = object()
WHO = Verified("run", "python", TOKEN)


def rec(tmp_path: Path) -> Record:
    return Record(
        tmp_path / "box",
        lambda: "2026-01-01T00:00:00Z",
        "v",
        TOKEN,
        tmp_path / "anchor.json",
    )


def seal(r: Record, n: int) -> None:
    row = r.rows()[n]
    r.set_anchor({"n": row["n"], "hash": row["hash"]}, "proof")


def truth(r: Record):
    """What a whole-file parse says, from a fresh Record (no memory)."""
    f = Record(r.box, r.clock, r.version, TOKEN, r.anchor_path)
    rows = f._scan_file()[0]
    torn = f._scan_file()[2] is not None
    i = max((k for k, x in enumerate(rows) if x["kind"] == "boot"), default=-1)
    return (
        (len(rows), rows[-1]["hash"] if rows else record.GENESIS, torn),
        ((rows[i] if i >= 0 else None), rows[i + 1 :]),
        any(x["kind"] == "pin" for x in rows),
    )


def asked(r: Record):
    f = Record(r.box, r.clock, r.version, TOKEN, r.anchor_path)
    return f.tail_state(), f.since_last("boot"), f.has_kind("pin")


def build(r: Record, kinds: list[str]) -> None:
    for k in kinds:
        extra = {"hard_close": False} if k == "boot" else {}
        r.append(k, WHO, **extra)


SHAPES = {
    "boot after the tip": (["pin", "note", "note", "boot", "note"], 1),
    "boot before the tip": (["pin", "boot", "note", "note", "note"], 3),
    "pin only before the tip": (["note", "pin", "note", "boot", "note"], 2),
    "no pin at all": (["note", "note", "boot", "note"], 1),
    "tip is the last row": (["pin", "boot", "note"], 2),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_tip_readers_say_what_the_whole_file_says(tmp_path, shape):
    kinds, at = SHAPES[shape]
    r = rec(tmp_path)
    build(r, kinds)
    seal(r, at)
    assert asked(r) == truth(r)
    assert Record(r.box, r.clock, "v", TOKEN, r.anchor_path)._from_tip() is not None


def test_a_pin_named_inside_a_field_is_not_a_pin(tmp_path):
    r = rec(tmp_path)
    r.append("note", WHO, carried={"kind": "pin"}, text='"kind":"pin"')
    build(r, ["boot", "note"])
    seal(r, 1)
    assert asked(r) == truth(r) and asked(r)[2] is False


@pytest.mark.parametrize(
    "spoil",
    ["wrong hash", "tip past the end", "a line inserted before the tip", "torn tail"],
)
def test_a_tip_that_doesnt_check_out_reads_the_whole_file(tmp_path, spoil):
    r = rec(tmp_path)
    build(r, ["pin", "boot", "note", "note"])
    seal(r, 2)
    p, a = r.path, r.anchor_path
    if spoil == "wrong hash":
        a.write_text(a.read_text().replace(r.rows()[2]["hash"], "0" * 64))
    elif spoil == "tip past the end":
        a.write_text(a.read_text().replace('"n":2', '"n":99'))
    elif spoil == "a line inserted before the tip":
        lines = p.read_text().split("\n")
        p.write_text("\n".join([lines[0], "not a row", *lines[1:]]))
    else:
        with p.open("a") as f:
            f.write('{"n": 4')
    if spoil != "torn tail":
        assert Record(r.box, r.clock, "v", TOKEN, a)._from_tip() is None
    assert asked(r) == truth(r)


def test_an_append_from_the_tip_links_and_numbers_as_ever(tmp_path):
    r = rec(tmp_path)
    build(r, ["pin", "boot", "note", "note"])
    seal(r, 2)
    fresh = Record(r.box, r.clock, "v", TOKEN, r.anchor_path)
    fresh.append("note", WHO, text="from the tip")
    assert fresh.verify_chain() == []
    assert [x["n"] for x in fresh.rows()] == list(range(5))


def test_a_prompt_with_a_sealed_tip_writes_the_same_bytes_and_parses_nothing_whole(
    box,  # noqa: F811
    monkeypatch,
):
    """One box, one record state, the same prompt twice: once read from a
    sealed tip, once (the record put back, the anchor gone) from the whole
    file. The files must come out byte for byte the same."""
    c = box["cfg"]
    for _ in range(3):
        prompt_path.prompt(c, BITE)
    path, a = c.box / "record.jsonl", c.keys.parent / opening.ANCHOR
    r = Record(c.box, lambda: "", "", object(), a)
    last = r.rows()[-1]
    r.set_anchor({"n": last["n"], "hash": last["hash"]}, "proof")
    snapshot = path.read_bytes()

    parses = []
    real = Record._scan_file
    monkeypatch.setattr(
        Record, "_scan_file", lambda self: parses.append(self.box) or real(self)
    )
    sealed = prompt_path.prompt(c, BITE)
    assert c.box not in parses, "a prompt on a sealed box parsed the whole record"
    from_tip = path.read_bytes()

    path.write_bytes(snapshot)
    a.unlink()
    plain = prompt_path.prompt(c, BITE)
    assert c.box in parses  # with no anchor, the whole file, as before
    assert path.read_bytes() == from_tip
    assert from_tip.count(b"\n") == snapshot.count(b"\n") + 2  # invocation, lookup
    assert Record(c.box, lambda: "", "", object()).verify_chain() == []
    assert sealed["doc"]["state"] == plain["doc"]["state"] == "populated"


def test_another_writers_row_is_never_lost_from_a_remembered_tip(tmp_path):
    """A reads from the tip and remembers it; B appends; A appends. A's next
    read must hold B's row: a remembered view of an older file is never
    extended, it is read again."""
    a = rec(tmp_path)
    build(a, ["pin", "boot", "note"])
    seal(a, 2)
    a.since_last("boot")  # A remembers the tip view
    b = Record(a.box, a.clock, "v", TOKEN, a.anchor_path)
    b.append("note", WHO, text="b's")
    a.rows()  # A parses the new file whole, while its tip view is of the old one
    a.append("note", WHO, text="a's")
    _, after = a.since_last("boot")
    assert [r.get("text") for r in after] == [None, "b's", "a's"]
    assert asked(a) == truth(a)
