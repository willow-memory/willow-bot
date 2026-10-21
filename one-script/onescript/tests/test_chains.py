"""One verifier per chain shape (flatten-2 LE-6, F15). Each good chain is built
by an ORACLE: the real writer's hashing, copied here with its file:line.
Then: a tampered row, a cut tail, a reordered pair, garbled rows, unreachable."""

from __future__ import annotations

import hashlib
import json

import pytest

from onescript import chains, record

G64 = "0" * 64


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def first(breaks):
    assert len(breaks) == 1
    return breaks[0]["index"], breaks[0]["why"]


# -- shape 1a: onescript Record (willow-bot one-script/onescript/record.py:162-166) --


def rec_rows(n=4):
    rows, prev = [], record.GENESIS
    for i in range(n):  # record.py:162 prev, :166 row["hash"] = h256(canon(row))
        row = {"n": i, "kind": "k", "who": "é", "prev": prev, "where": None}
        row["hash"] = record.h256(record.canon(row))
        rows.append(row)
        prev = row["hash"]
    return rows


REC = chains.PayloadPrev("sorted_compact_utf8")


# -- shape 1b: deposits (willow-bot willow_bot/deposits.py:115-133) -----------


def dep_hash(rec, prev_hash):  # deposits.py:115-133, _canonical + compute_row_hash
    body = {k: v for k, v in rec.items() if k not in {"prev_hash", "row_hash"}}
    c = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(c + b"\x00" + prev_hash.encode("ascii")).hexdigest()


def dep_rows(n=4):
    rows, prev = [], G64
    for i in range(n):  # deposits.py:196-198
        r = {"id": i, "outcome": "ok", "note": "é"}
        h = dep_hash(r, prev)
        r["prev_hash"], r["row_hash"] = prev, h
        rows.append(r)
        prev = h
    return rows


DEP = chains.PayloadPrev(
    "sorted_compact_ascii",
    prev_key="prev_hash",
    hash_key="row_hash",
    covers="body_nul_prev",
    chain_keys=("prev_hash", "row_hash"),
)


# -- shape 2: governance_ledger (willow-mcp governance_ledger.py:57-86) ------


def gov_v2(prev_hash, record_id, project, event_type, content):
    payload = json.dumps(  # governance_ledger.py:71-75 _payload_v2
        {
            "id": record_id,
            "project": project,
            "event_type": event_type,
            "content": content,
        },
        sort_keys=True,
    )
    return hashlib.sha256(f"{prev_hash or ''}{payload}".encode()).hexdigest()  # :84-86


def gov_v1(prev_hash, event_type, content):  # governance_ledger.py:57-68
    payload = json.dumps({"event_type": event_type, "content": content}, sort_keys=True)
    return hashlib.sha256(f"{prev_hash or ''}{payload}".encode()).hexdigest()


def grove_hash(prev_hash, event_type, content):  # willows-grove grove_db.py:491-494
    payload = json.dumps({"event_type": event_type, "content": content}, sort_keys=True)
    return hashlib.sha256(f"{prev_hash or ''}{payload}".encode()).hexdigest()


def gov_rows(n=4, legacy_at=None):
    rows, prev = [], None
    for i in range(n):  # governance_ledger.py:109-119 (prev = last hash or None)
        r = {
            "id": f"id{i}",
            "project": "p",
            "event_type": "e",
            "content": {"a": i, "z": "é"},
        }
        if i == legacy_at:
            h = gov_v1(prev, r["event_type"], r["content"])
        else:
            h = gov_v2(prev, r["id"], r["project"], r["event_type"], r["content"])
        r["prev_hash"], r["hash"] = prev, h
        rows.append(r)
        prev = h
    return rows


def _gov_payloads(r):  # governance_ledger.py:217-218 accepts v2 or v1
    return [
        {
            "id": r["id"],
            "project": r["project"],
            "event_type": r["event_type"],
            "content": r["content"],
        },
        {"event_type": r["event_type"], "content": r["content"]},
    ]


GOV = chains.PrevPayload("sorted_spaced_ascii", _gov_payloads)


# -- shape 3: attribution_ledger (willow-mcp attribution_ledger.py:95-107, 138-145) --


def attr_lines(n=4):
    lines, prev = [], "genesis"
    for i in range(n):
        entry = {
            "kind": "session_attestation",
            "session_id": f"s{i}",
            "verifier": "é",
            "prev": prev,
        }
        line = json.dumps(
            entry, separators=(",", ":"), ensure_ascii=False
        )  # :104 compact_utf8
        lines.append(line + "\n")
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()  # :107
    return lines


RAW = chains.RawLine()


# -- shape 4: receipts (willow-mcp receipts.py:58-62, verify :158-164) --------


def rcp_hash(prev_hash, ts, app_id, tool, outcome, detail):  # receipts.py:58-62
    payload = json.dumps(
        [prev_hash, ts, app_id, tool, outcome, detail],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rcp_rows(n=4):
    rows, prev = [], G64  # receipts.py:47 _GENESIS
    for i in range(n):
        f = (f"2026-10-0{i + 1}", "app", "tool", "ok", None if i == 0 else "é")
        h = rcp_hash(prev, *f)
        rows.append(
            dict(
                zip(("ts", "app_id", "tool", "outcome", "detail"), f),
                prev_hash=prev,
                entry_hash=h,
            )
        )
        prev = h
    return rows


RCP = chains.Positional("compact_utf8", ("ts", "app_id", "tool", "outcome", "detail"))


# -- every shape: good, tampered, cut tail, reordered pair --------------------

CASES = {
    "record": (REC, rec_rows, "hash", "prev"),
    "deposits": (DEP, dep_rows, "row_hash", "prev_hash"),
    "governance": (GOV, gov_rows, "hash", "prev_hash"),
    "receipts": (RCP, rcp_rows, "entry_hash", "prev_hash"),
}


@pytest.mark.parametrize("name", CASES)
def test_the_writers_chain_holds(name):
    chain, build, *_ = CASES[name]
    assert chain.verify(build()) == []


@pytest.mark.parametrize("name", CASES)
def test_a_tampered_row_breaks_at_that_row(name):
    chain, build, hk, pk = CASES[name]
    rows = build()
    victim = next(k for k in rows[2] if k not in (hk, pk))
    rows[2][victim] = "forged"
    assert first(chain.verify(rows)) == (2, "hash mismatch")


@pytest.mark.parametrize("name", CASES)
def test_a_rehashed_forgery_breaks_at_its_successor(name):
    chain, build, hk, pk = CASES[name]
    rows = build()
    rows[1][pk] = "f" * 64  # wrong prev: caught before the hash
    assert first(chain.verify(rows)) == (1, "prev mismatch")


@pytest.mark.parametrize("name", CASES)
def test_a_cut_tail_is_caught_only_by_the_anchor(name):
    chain, build, *_ = CASES[name]
    rows = build()
    anchor = chains.tip(chain, rows)
    assert anchor["state"] == "populated" and anchor["count"] == 4
    assert chain.verify(rows[:-1]) == []  # the walk alone cannot tell
    assert first(chain.verify(rows[:-1], anchor)) == (3, "tip mismatch")
    assert chain.verify(rows, anchor) == []


@pytest.mark.parametrize("name", CASES)
def test_a_reordered_pair_breaks(name):
    chain, build, *_ = CASES[name]
    rows = build()
    rows[1], rows[2] = rows[2], rows[1]
    assert first(chain.verify(rows)) == (1, "prev mismatch")


@pytest.mark.parametrize("name", CASES)
def test_garbled_and_unreachable_and_empty(name):
    chain, build, *_ = CASES[name]
    assert first(chain.verify([build()[0], "not a row"])) == (1, "garbled")
    assert first(chain.verify([{}])) == (0, "garbled")
    assert first(chain.verify(None)) == (None, "unreachable")
    assert chain.verify([]) == []
    assert chains.tip(chain, []) == {
        "state": "empty",
        "count": 0,
        "head": chain.genesis,
    }
    assert chains.tip(chain, None) == {"state": "unreachable", "count": 0, "head": None}


def test_record_chain_agrees_with_Record_verify_chain(tmp_path):
    """The verifier walks the bytes the one script's own Record wrote."""
    rows = rec_rows()
    assert chains.verify(REC, rows) == []
    rows[2]["who"] = "x"
    assert first(chains.verify(REC, rows)) == (2, "hash mismatch")


def test_deposits_first_row_must_be_genesis_stricter_than_the_copy():
    rows = dep_rows()
    r0 = rows[0]
    r0["prev_hash"] = "a" * 64
    r0["row_hash"] = dep_hash(r0, "a" * 64)
    assert first(DEP.verify(rows)) == (0, "prev mismatch")


def test_governance_legacy_v1_row_still_verifies_and_grove_writes_the_same_chain():
    assert GOV.verify(gov_rows(legacy_at=2)) == []
    # Grove's writer (no id/project in the digest) is the v1 payload.
    g = [
        {
            "event_type": "e",
            "content": {"k": "é"},
            "prev_hash": None,
            "hash": grove_hash(None, "e", {"k": "é"}),
        }
    ]
    g.append(
        {
            "event_type": "e2",
            "content": [1],
            "prev_hash": g[0]["hash"],
            "hash": grove_hash(g[0]["hash"], "e2", [1]),
        }
    )
    v1 = chains.PrevPayload(
        "sorted_spaced_ascii",
        lambda r: {"event_type": r["event_type"], "content": r["content"]},
    )
    assert v1.verify(g) == []


def test_governance_first_prev_must_be_none():
    rows = gov_rows()
    rows[0]["prev_hash"] = ""
    assert first(GOV.verify(rows)) == (0, "prev mismatch")


# -- shape 3 -------------------------------------------------------------------


def test_raw_line_chain_holds_and_skips_blank_lines():
    lines = attr_lines()
    assert RAW.verify(lines) == []
    assert RAW.verify([lines[0], "\n", lines[1], *lines[2:]]) == []
    assert (
        chains.tip(RAW, lines)["head"]
        == hashlib.sha256(lines[-1].strip().encode()).hexdigest()
    )


def test_raw_line_tamper_shows_at_the_next_line_and_the_last_needs_the_anchor():
    lines = attr_lines()
    anchor = chains.tip(RAW, lines)
    t = list(lines)
    t[1] = t[1].replace("s1", "sX")
    assert first(RAW.verify(t)) == (2, "prev mismatch")
    t = list(lines)
    t[3] = t[3].replace("s3", "sX")  # last line: invisible to the walk
    assert RAW.verify(t) == []
    assert first(RAW.verify(t, anchor)) == (4, "tip mismatch")


def test_raw_line_reorder_cut_and_garbled():
    lines = attr_lines()
    anchor = chains.tip(RAW, lines)
    r = list(lines)
    r[1], r[2] = r[2], r[1]
    assert first(RAW.verify(r)) == (1, "prev mismatch")
    assert RAW.verify(lines[:-1]) == []
    assert first(RAW.verify(lines[:-1], anchor)) == (3, "tip mismatch")
    assert first(RAW.verify([lines[0], "{not json\n"])) == (1, "garbled")
    assert first(RAW.verify([lines[0], "[1]\n"])) == (1, "garbled")
    assert first(RAW.verify(None)) == (None, "unreachable")
    assert chains.tip(RAW, []) == {"state": "empty", "count": 0, "head": "genesis"}


# -- the verifiers are pure -----------------------------------------------------


def test_no_imports_beyond_canon_and_states():
    import ast
    import pathlib

    src = pathlib.Path(chains.__file__).read_text()
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom) and n.level:
            mods |= {a.name for a in n.names}
    assert mods == {"canon", "states"}
