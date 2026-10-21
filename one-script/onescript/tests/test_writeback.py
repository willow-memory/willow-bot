"""writeback: every escalation writes back, a held write-back answers the
same bite at rung 1, and what nothing answered is logged by hash.

Each guard is attempted: an unsealed write-back is never read back, a seal
over another subject doesn't count, a changed byte is another subject, a row
that says one subject and carries other bytes is skipped, a byte of
difference in the bite is no hit, and a miss row holds no word. Every Nestor
store here is a tmp SQLite file; none is ever written by the code under test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, pile, record  # noqa: E402
from onescript import writeback as wb  # noqa: E402
from onescript.tests.test_api import (  # noqa: E402
    cfg,  # noqa: F401
    needs_ed25519,
    nestor_pair,
    nestor_store,
    proof,
    rows,
)
from onescript.tests.test_escalate import Stub, good, never  # noqa: E402
from onescript.tests.test_store import BITE, DOCS  # noqa: E402
from onescript.tests.test_seed import index_into_box  # noqa: E402
from onescript.tests.test_xref import make  # noqa: E402

TASK = "Summarize what each table shows"
UNSEEN = "zyxwvutsrq"


@pytest.fixture
def box(cfg, tmp_path):  # noqa: F811
    repo = make(tmp_path, "docs", DOCS)
    index_into_box(cfg, repo)
    assert api.checkin(cfg)["code"] == 0
    return cfg


def answered(cfg, bite=BITE, rung=good):  # noqa: F811
    """Seed, scope the seed's chunks, seal, serve, escalate: the candidates."""
    sd = api.seed(cfg, bite)
    assert sd["code"] == 0, sd
    card = api.scope(cfg, by=(), seed_top=2)
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    assert api.serve(cfg, card["spec"], max_chars=10**6)["state"] == "populated"
    res = api.escalate(cfg, TASK, rung=Stub(rung), piece_chars=10**6)
    assert "refused" not in res, res
    return sd, res


def proposed(res) -> list[dict]:
    return [w for w in res["writeback"] if w["state"] == "proposed"]


def seed_again(cfg, bite=BITE):  # noqa: F811
    assert api.checkin(cfg)["code"] == 0
    got = api.seed(cfg, bite)
    # 0 when the pile holds the bite; 42 when nothing a human said does
    assert got["code"] in (0, api.ESCALATE) and "refused" not in got, got
    return got


def run_of(cfg):  # noqa: F811
    run, _ = api._open(cfg, "probe", ())
    return run


# ── the loop: escalate, write back, seal, stop at rung 1 ────────────────────
def test_an_answered_escalation_proposes_a_write_back_tied_to_the_bite(box):
    sd, res = answered(box)
    got = proposed(res)
    assert got and len(got) == res["answered"]
    row = next(r for r in rows(box) if r["kind"] == "writeback")
    assert row["state"] == "proposed" and row["bite_from"] == "seed"
    assert row["bite_hash"] == record.h256(BITE)
    # the cites are the chunks' own SHA-256, not this session's keyed ids
    chunks = {h["chunk"] for h in sd["rank"]}
    assert set(row["cites"]) <= chunks and row["cites"]
    assert row["subject"] == wb.subject(
        row["bite_hash"], row["answer_hash"], row["cites"]
    )


def test_a_held_write_back_answers_the_same_bite_at_rung_one(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    sealed = api.writeback_seal(box, s, proof=proof(s))
    assert sealed["sealed"] and sealed["state"] == "held"
    got = seed_again(box)
    assert got["answered_by"] == s and got["writeback"]["state"] == "held"
    assert got["writeback"]["cites"]
    seed_row = rows(box)[got["row"]]
    assert seed_row["answered_by"] == s  # the record says it, not only the reply
    assert not [r for r in rows(box)[got["row"] :] if r["kind"] == "miss"]


def test_an_unsealed_write_back_is_never_read_back(box):
    answered(box)
    got = seed_again(box)
    assert "answered_by" not in got
    assert got["writeback"]["state"] == "none" and got["writeback"]["unsealed"] >= 1


def test_a_wrong_proof_seals_nothing_and_nothing_is_read_back(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    bad = api.writeback_seal(box, s, proof="00" * 32)
    assert bad["sealed"] is False and bad["state"] == "proposed"
    assert "answered_by" not in seed_again(box)


def test_a_seal_over_another_subject_does_not_count(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    run = run_of(box)
    other = wb.propose(
        run,
        bite_hash=record.h256("another bite entirely"),
        bite_from="task",
        answer={"detail": "x", "rows": []},
        cites=["c" * 64],
        source={"kind": "test"},
    )
    assert api.writeback_seal(box, other["subject"], proof=proof(other["subject"]))[
        "sealed"
    ]
    got = seed_again(box)
    assert "answered_by" not in got and s != other["subject"]
    # and a proof made for the other subject doesn't seal this one
    wrong = api.writeback_seal(box, s, proof=proof(other["subject"]))
    assert wrong["sealed"] is False


def test_a_byte_of_difference_in_the_bite_is_no_hit(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    api.writeback_seal(box, s, proof=proof(s))
    got = seed_again(box, BITE + " ")
    assert "answered_by" not in got and got["writeback"]["state"] == "none"


def test_a_changed_byte_in_the_answer_is_a_different_subject():
    bh, cites = "a" * 64, ["b" * 64]
    one = wb.subject(bh, wb.answer_hash({"detail": "x"}), cites)
    two = wb.subject(bh, wb.answer_hash({"detail": "y"}), cites)
    assert one != two
    assert wb.subject(bh, wb.answer_hash({"detail": "x"}), ["c" * 64]) != one
    assert wb.subject("d" * 64, wb.answer_hash({"detail": "x"}), cites) != one


def forged(box, held_row: dict, **change) -> dict:  # noqa: F811
    run = run_of(box)
    fields = {
        k: held_row[k]
        for k in ("subject", "bite_hash", "answer_hash", "answer", "cites", "source")
    }
    return run.rec.append(
        "writeback",
        run.sys,
        where=held_row["subject"],
        state="proposed",
        **{**fields, **change},
    )


@pytest.mark.parametrize(
    "change",
    [
        {"bite_hash": record.h256("a forged bite")},
        {"answer": {"detail": "a changed answer", "rows": []}},
        {"cites": ["e" * 64]},
    ],
)
def test_a_row_that_says_a_held_subject_and_carries_other_bytes_is_skipped(box, change):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    api.writeback_seal(box, s, proof=proof(s))
    held = next(r for r in rows(box) if r["kind"] == "writeback" and r["subject"] == s)
    bad = forged(box, held, **change)
    if "bite_hash" in change:
        got = seed_again(box, "a forged bite")
        assert "answered_by" not in got and got["writeback"]["state"] == "none"
    last = rows(box)[bad["n"]]
    assert last["kind"] == "writeback" and last["subject"] == s
    assert wb.well_formed(last) is not None
    assert last["n"] not in {r["n"] for r in wb.candidates(rows(box))}


def test_writeback_seal_takes_only_a_write_back_on_record(box):
    api.seed(box, BITE)
    for s in ("proposal:" + "a" * 64, "serve:" + "a" * 64, wb.PREFIX + "a" * 64):
        got = api.writeback_seal(box, s, proof=proof(s))
        assert got["sealed"] is False and got["row"]["kind"] == "refused"
    assert not [r for r in rows(box) if r["kind"] == "seal"]
    why = [r["reason"] for r in rows(box) if r["kind"] == "refused"]
    assert why == ["not a write-back subject"] * 2 + ["no such write-back on record"]


def self_consistent(**over) -> dict:
    """A row whose subject is the hash of its own fields, whatever they hold."""
    row = {"kind": "writeback", "bite_hash": "a" * 64, "answer": {"x": 1}, **over}
    row["answer_hash"] = wb.answer_hash(row["answer"])
    row["cites"] = over.get("cites", ["b" * 64])
    row["subject"] = wb.PREFIX + record.h256(
        record.canon(
            {
                "bite_hash": row["bite_hash"],
                "answer_hash": row["answer_hash"],
                "cites": sorted(set(row["cites"])),
            }
        )
    )
    return row


@pytest.mark.parametrize(
    "over",
    [
        {"cites": []},
        {"cites": ["a served id, not a hash"]},
        {"cites": ["B" * 64]},
        {"bite_hash": "the bite itself"},
    ],
)
def test_a_self_consistent_row_still_needs_content_hashes(over):
    assert wb.well_formed(self_consistent()) is None
    assert wb.well_formed(self_consistent(**over)) is not None


def test_an_answer_whose_cites_are_not_served_ids_is_not_written_back():
    assert wb._content(["x" * 64], {})[0] is None
    assert wb._content([], {"a": "b"})[0] is None
    assert wb._content(["a"], {"a": "b" * 64}) == (["b" * 64], "")


def test_an_escalation_that_did_not_answer_writes_nothing_back(box):
    from onescript.tests.test_escalate import mark

    _, res = answered(box, rung=mark("not enough here"))
    assert res["answered"] == 0 and res["writeback"] == []
    assert not [r for r in rows(box) if r["kind"] == "writeback"]


def test_the_same_answer_again_is_not_recorded_twice(box):
    _, res = answered(box)
    again = api.escalate(box, TASK, rung=Stub(never), piece_chars=10**6)
    assert {w["subject"] for w in proposed(again)} == {
        w["subject"] for w in proposed(res)
    }
    assert all(w["new"] is False for w in proposed(again))
    subs = [r["subject"] for r in rows(box) if r["kind"] == "writeback"]
    assert len(subs) == len(set(subs))


def test_a_sealed_proposal_is_written_back_too(box):
    _, res = answered(box)
    prop = next(r for r in rows(box) if r["kind"] == "proposal")
    got = api.seal_proposal(box, prop["subject"], proof=proof(prop["subject"]))
    assert got["sealed"] and got["writeback"]["state"] == "proposed"
    row = rows(box)[got["writeback"]["row"]]
    assert row["source"] == {
        "kind": "proposal",
        "row": prop["n"],
        "subject": prop["subject"],
    }
    assert row["answer"]["data"] == prop["data"] and wb.well_formed(row) is None


@needs_ed25519
def test_a_nestor_pair_seals_a_write_back_and_the_store_is_only_read(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    nestor_store(box, nestor_pair(s))
    before = box.nestor_db.read_bytes()
    got = api.writeback_seal(box, s, pair=nestor_pair(s))
    assert got["sealed"] and got["row"]["via"] == "nestor"
    assert seed_again(box)["answered_by"] == s
    assert box.nestor_db.read_bytes() == before  # read, never written


@needs_ed25519
def test_a_seal_that_cannot_be_checked_is_unreachable_not_a_hit(box):
    _, res = answered(box)
    s = proposed(res)[0]["subject"]
    nestor_store(box, nestor_pair(s))
    assert api.writeback_seal(box, s, pair=nestor_pair(s))["sealed"]
    box.nestor_db.unlink()  # the store is gone: nothing can confirm the pair
    got = seed_again(box)
    assert "answered_by" not in got
    assert got["writeback"]["state"] == "unreachable" and got["writeback"]["why"]


# ── the miss log ────────────────────────────────────────────────────────────
def test_an_unseen_word_is_a_miss_row_of_hashes_never_words(box):
    got = api.seed(box, f"the {UNSEEN} ad breaks")
    assert got["miss"]["count"] == 1
    (m,) = [r for r in rows(box) if r["kind"] == "miss"]
    key, _ = api._store_key(box, box.box)
    assert m["missing"] == [wb.word_hash(UNSEEN, key)] and m["count"] == 1
    assert record.h256(UNSEEN) not in json.dumps(m)  # keyed, never the plain hash
    assert m["bite_hash"] == record.h256(f"the {UNSEEN} ad breaks")
    assert m["seed_row"] == got["row"]
    assert UNSEEN not in json.dumps(m) and "breaks" not in json.dumps(m)


def test_a_bite_whose_words_all_hit_is_no_miss(box):
    got = api.seed(box, BITE)
    assert "miss" not in got
    assert not [r for r in rows(box) if r["kind"] == "miss"]


def test_a_bite_said_before_is_no_miss_even_with_a_new_word(box):
    got = api.seed(box, f"{BITE} And {UNSEEN} too, said here.")
    assert got["quotes"] and "miss" not in got
    assert not [r for r in rows(box) if r["kind"] == "miss"]


def test_a_store_that_cannot_be_read_logs_no_miss(box):
    path = pile.store_path(box.box)
    with open(path, "ab") as f:
        f.write(b"\0")
    got = api.seed(box, f"the {UNSEEN} ad breaks")
    assert "miss" not in got
    assert not [r for r in rows(box) if r["kind"] == "miss"]


def test_a_write_back_hit_is_no_miss(box):
    _, res = answered(box, bite=f"the {UNSEEN} ad breaks")
    assert [r for r in rows(box) if r["kind"] == "miss"]
    s = proposed(res)[0]["subject"]
    api.writeback_seal(box, s, proof=proof(s))
    n = len(rows(box))
    got = seed_again(box, f"the {UNSEEN} ad breaks")
    assert got["answered_by"] == s and "miss" not in got
    assert not [r for r in rows(box)[n:] if r["kind"] == "miss"]


def test_needs_counts_misses_and_waiting_write_backs_with_no_words(box):
    answered(box, bite=f"the {UNSEEN} ad breaks")
    got = api.needs(box)
    w = got["writeback"]
    assert w["misses"] == 1 and w["line"].startswith("1 misses since ")
    assert w["writebacks_proposed"] >= 1 and w["writebacks_held"] == 0
    assert UNSEEN not in json.dumps(got)
