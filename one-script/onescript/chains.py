"""chains.py -- one verifier per hash-chain SHAPE (flatten-2 LE-6, F15).

The fleet writes its hash chains four ways. The operator, D-c: "One verifier
per shape" -- the chains keep their own stores; this holds only the walk.
No storage code lives here: a caller reads its rows (a file, Postgres, sqlite)
and hands them in. Nothing here imports beyond ``canon`` and ``states``.

The four shapes (a family name picks the bytes, from ``canon.py``)::

    payload_prev  hash = sha256(canon(row without hash)), `prev` inside the row
                  (onescript Record, family A); or, with covers="body_nul_prev",
                  sha256(canon(body) + b"\\x00" + prev) (willow-bot deposits)
    prev_payload  hash = sha256(prev + canon(payload)); prev is "" at genesis
                  (willow-mcp governance_ledger, Grove frank_ledger, family G)
    raw_line      each row stores sha256 of the PREVIOUS RAW LINE; no hash field
                  (attribution_ledger; SigningLedger, playgate, aristarchus)
    positional    hash = sha256(canon([prev, *fields])), a positional array
                  (willow-mcp receipts, family E)

Every verifier returns ``[]`` when the chain holds, else one break:
``{"index": i, "why": ...}`` for the FIRST broken row (nothing after a break
vouches for anything), ``why`` one of ``prev mismatch``, ``hash mismatch``,
``garbled``, ``tip mismatch`` (a cut tail or a swapped head, only when the
caller passes the anchor it kept out of band) or ``unreachable`` (rows None:
the caller could not look; never read as an empty chain).

A verifier may never accept what a copy rejected, so these are strict where the
copies were loose: the first row must carry the genesis value (deposits took
any first ``prev_hash``). Hash functions the copies keep as legacy (governance
v1) are accepted by listing both payloads in ``payload_of``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from . import canon as _canon
from . import states

PREV_MISMATCH, HASH_MISMATCH, GARBLED = "prev mismatch", "hash mismatch", "garbled"
TIP_MISMATCH, UNREACHABLE = "tip mismatch", "unreachable"


def _h(data: bytes | str) -> str:
    return hashlib.sha256(
        data if isinstance(data, bytes) else data.encode()
    ).hexdigest()


def _break(index: int | None, why: str) -> list[dict]:
    return [{"index": index, "why": why}]


def _enc(family: str):
    return _canon.get(family)


def _anchor_break(count: int, head: Any, anchor: dict | None) -> list[dict]:
    """The out-of-band check: ``anchor`` is ``{"count":, "head":}`` kept
    elsewhere. A cut tail changes the head; a swapped one changes both."""
    if anchor is None:
        return []
    if anchor.get("count") not in (None, count) or anchor.get("head") != head:
        return _break(count, TIP_MISMATCH)
    return []


@dataclass(frozen=True)
class PayloadPrev:
    """Shape 1. ``prev_key`` and ``hash_key`` name the row's fields;
    ``chain_keys`` are left out of the hashed body (default: just the hash key;
    deposits leave out both). ``covers`` is ``"row"`` (the canon of the row
    minus its hash, prev inside) or ``"body_nul_prev"`` (the canon of the body
    minus ``chain_keys``, then ``\\x00``, then prev)."""

    family: str
    prev_key: str = "prev"
    hash_key: str = "hash"
    genesis: str = "0" * 64
    covers: str = "row"
    chain_keys: tuple[str, ...] = ()

    def _hash(self, row: dict) -> str:
        drop = set(self.chain_keys) | {self.hash_key}
        enc = _enc(self.family)
        if self.covers == "row":
            return _h(enc.bytes({k: v for k, v in row.items() if k != self.hash_key}))
        if self.covers == "body_nul_prev":
            body = {k: v for k, v in row.items() if k not in drop}
            return _h(enc.bytes(body) + b"\x00" + row[self.prev_key].encode("ascii"))
        raise ValueError(f"no covers {self.covers!r}")

    def verify(
        self, rows: Sequence[dict] | None, anchor: dict | None = None
    ) -> list[dict]:
        if rows is None:
            return _break(None, UNREACHABLE)
        prev = self.genesis
        for i, row in enumerate(rows):
            if not (
                isinstance(row, dict)
                and isinstance(row.get(self.prev_key), str)
                and isinstance(row.get(self.hash_key), str)
            ):
                return _break(i, GARBLED)
            if row[self.prev_key] != prev:
                return _break(i, PREV_MISMATCH)
            try:
                ok = self._hash(row) == row[self.hash_key]
            except (UnicodeError, TypeError, ValueError):
                return _break(i, GARBLED)
            if not ok:
                return _break(i, HASH_MISMATCH)
            prev = row[self.hash_key]
        return _anchor_break(len(rows), prev, anchor)

    def tip(self, rows: Sequence[dict] | None) -> dict:
        head = rows[-1].get(self.hash_key) if rows else self.genesis
        return _tip(rows, head)


@dataclass(frozen=True)
class PrevPayload:
    """Shape 2. ``payload_of(row)`` returns the object (or a list of objects,
    any one of which may match: the legacy hash beside the current one) whose
    canon follows ``prev`` in the hashed string. ``genesis`` is the stored
    first ``prev`` (None in the Postgres chains); it hashes as ``""``."""

    family: str
    payload_of: Callable[[dict], Any]
    prev_key: str = "prev_hash"
    hash_key: str = "hash"
    genesis: str | None = None

    def verify(
        self, rows: Sequence[dict] | None, anchor: dict | None = None
    ) -> list[dict]:
        if rows is None:
            return _break(None, UNREACHABLE)
        enc, prev = _enc(self.family), self.genesis
        for i, row in enumerate(rows):
            if not (
                isinstance(row, dict)
                and isinstance(row.get(self.hash_key), str)
                and (
                    row.get(self.prev_key) is None
                    or isinstance(row.get(self.prev_key), str)
                )
            ):
                return _break(i, GARBLED)
            if row[self.prev_key] != prev:
                return _break(i, PREV_MISMATCH)
            try:
                got = self.payload_of(row)
                cands = got if isinstance(got, list) else [got]
                ok = any(
                    _h(f"{prev or ''}{enc(c)}") == row[self.hash_key] for c in cands
                )
            except (UnicodeError, TypeError, ValueError, KeyError):
                return _break(i, GARBLED)
            if not ok:
                return _break(i, HASH_MISMATCH)
            prev = row[self.hash_key]
        return _anchor_break(len(rows), prev, anchor)

    def tip(self, rows: Sequence[dict] | None) -> dict:
        return _tip(rows, rows[-1].get(self.hash_key) if rows else self.genesis)


@dataclass(frozen=True)
class RawLine:
    """Shape 3. Rows are the raw LINES (str), exactly as stored; each parses to
    a dict whose ``prev_key`` is the sha256 of the previous line, stripped
    (``genesis`` at the first). Blank lines are skipped, and ``index`` counts
    the lines given. There is no hash field, so the last line is vouched for
    only by an anchor (the copies say so too)."""

    prev_key: str = "prev"
    genesis: str = "genesis"

    def verify(
        self, lines: Iterable[str] | None, anchor: dict | None = None
    ) -> list[dict]:
        if lines is None:
            return _break(None, UNREACHABLE)
        prev, count = self.genesis, 0
        for i, raw in enumerate(lines):
            line = raw.strip() if isinstance(raw, str) else None
            if line == "":
                continue
            try:
                rec = json.loads(line)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return _break(i, GARBLED)
            if not isinstance(rec, dict) or not isinstance(rec.get(self.prev_key), str):
                return _break(i, GARBLED)
            if rec[self.prev_key] != prev:
                return _break(i, PREV_MISMATCH)
            prev = _h(line)
            count += 1
        return _anchor_break(count, prev, anchor)

    def tip(self, lines: Iterable[str] | None) -> dict:
        kept = None if lines is None else [x.strip() for x in lines if x.strip()]
        return _tip(kept, _h(kept[-1]) if kept else self.genesis)


@dataclass(frozen=True)
class Positional:
    """Shape 4. hash = sha256(canon([prev, row[f] for f in fields])); the row
    holds ``prev_key`` and ``hash_key`` beside the fields."""

    family: str
    fields: tuple[str, ...]
    prev_key: str = "prev_hash"
    hash_key: str = "entry_hash"
    genesis: str = "0" * 64

    def verify(
        self, rows: Sequence[dict] | None, anchor: dict | None = None
    ) -> list[dict]:
        if rows is None:
            return _break(None, UNREACHABLE)
        enc, prev = _enc(self.family), self.genesis
        for i, row in enumerate(rows):
            if not (
                isinstance(row, dict)
                and isinstance(row.get(self.prev_key), str)
                and isinstance(row.get(self.hash_key), str)
                and all(f in row for f in self.fields)
            ):
                return _break(i, GARBLED)
            if row[self.prev_key] != prev:
                return _break(i, PREV_MISMATCH)
            try:
                ok = (
                    _h(enc.bytes([prev, *(row[f] for f in self.fields)]))
                    == row[self.hash_key]
                )
            except (UnicodeError, TypeError, ValueError):
                return _break(i, GARBLED)
            if not ok:
                return _break(i, HASH_MISMATCH)
            prev = row[self.hash_key]
        return _anchor_break(len(rows), prev, anchor)

    def tip(self, rows: Sequence[dict] | None) -> dict:
        return _tip(rows, rows[-1].get(self.hash_key) if rows else self.genesis)


def _tip(rows: Sequence | None, head: Any) -> dict:
    """What to keep out of band: the three-state word, the count, the head."""
    return {
        "state": states.state_of(rows),
        "count": len(rows) if rows is not None else 0,
        "head": head if rows is not None else None,
    }


def verify(chain, rows, anchor: dict | None = None) -> list[dict]:
    """``chain.verify(rows, anchor)``: the walk, by whichever shape ``chain`` is."""
    return chain.verify(rows, anchor)


def tip(chain, rows) -> dict:
    """``{"state", "count", "head"}`` for anchoring; pass it back as ``anchor``
    (``state`` is ignored there) on a later verify."""
    return chain.tip(rows)


#: The shapes, by name.
SHAPES = {
    "payload_prev": PayloadPrev,
    "prev_payload": PrevPayload,
    "raw_line": RawLine,
    "positional": Positional,
}
