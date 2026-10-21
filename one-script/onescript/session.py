"""session — one seal per session, at its end, over the session's bundle.

The operator, 2026-10-19: *"there is really nothing to seal yet because no real
decision has been made other than hey I want to build this thing that's not a
decision that's an idea"*, and *"It's still my seal, the bundle is what I'm
sealing. I don't need to seal tool calls, I need, and user's, to be able to
say, yes, these were my actions in this session. Agree, or disagree, if
disagree, then deny the pair. The record is still there, and can still be
looked at, it just can't be served."*

So, with `Config.seal_at_end`:

  during    nothing waits on a seal. `serve` hands out a scope nobody sealed,
            marked `unsealed: this session`; what it serves of the record is
            this session's own rows and the rows of sessions the person
            sealed. The Forge's card answers are Nestor drafts.
  checkout  the session (from its check-in to now) becomes one bundle: the
            person's actions, what came of each, and every record row by its
            hash. It is written to the box (`sessions/<from>.json`) and
            proposed to Nestor as one draft pair, `session:<sha256>` over the
            bundle's bytes (`nestor.session_seal`). One `session` row says so.
  the seal  the person's, in Nestor's own UI: Seal (yes, these were my
            actions) or Deny. The next check-in reads a sealed one back as a
            `seal` row, checked like any Nestor seal (`api._seal`).
  serving   a sealed session's rows serve; a denied or unanswered one's never
            do, and stay on the record to be read.

Tagged, not excluded: a sealed session's rows are served like any other piece,
ranked and capped by the step that reads them.
"""

from __future__ import annotations

import bisect
import hashlib

from . import canon

KIND = "session"
PREFIX = "session:"
DIR = "sessions"
#: Run bookkeeping that says nothing about what the person did.
_QUIET = frozenset({"invocation", "pin"})


def canonical(bundle: dict) -> bytes:
    """The bundle's bytes, as Nestor hashes them (`nestor.session_seal.canonical`):
    sorted keys, compact, UTF-8. The `session` row's subject is over these."""
    return canon.sorted_compact_utf8.bytes(bundle)


def subject(data: bytes) -> str:
    return PREFIX + hashlib.sha256(data).hexdigest()


def starts(rows: list[dict]) -> list[int]:
    """Each session's first row: the `checkin` invocation that opened it."""
    return sorted(
        r["n"]
        for r in rows
        if r.get("kind") == "invocation" and r.get("cmd") == "checkin"
    )


def current(rows: list[dict]) -> int | None:
    """The open session's first row, or None before any check-in."""
    s = starts(rows)
    return s[-1] if s else None


def _of(n: int, opened: list[int]) -> int | None:
    i = bisect.bisect_right(opened, n) - 1
    return opened[i] if i >= 0 else None


def subjects(rows: list[dict]) -> dict[int, str]:
    """Each checked-out session's subject, by its first row."""
    return {
        r["from"]: r["subject"]
        for r in rows
        if r.get("kind") == KIND and isinstance(r.get("subject"), str)
    }


def _label(r: dict) -> str:
    said = r.get("state") or r.get("verdict")
    if r.get("kind") == "boot":
        said = "hard close" if r.get("hard_close") else "open"
    return f"{r['kind']}: {said}" if isinstance(said, str) and said else r["kind"]


def actions(rows: list[dict], start: int) -> list[dict]:
    """What the person did in the session from row `start`: one action per verb
    they ran, and what came of each, in the record's order."""
    out: list[dict] = []
    for r in rows:
        if r["n"] < start:
            continue
        if r["kind"] == "invocation":
            out.append(
                {
                    "did": " ".join(map(str, r.get("argv") or [r.get("cmd")])),
                    "came_of": [],
                }
            )
        elif out and r["kind"] not in _QUIET:
            out[-1]["came_of"].append(_label(r))
    return out


def bundle(rows: list[dict], start: int, box_id: str) -> dict:
    """The session from row `start` to the record's tip, compressed: the
    actions, and every row by number, kind and hash. `box_id` keeps two boxes'
    sessions apart."""
    mine = [r for r in rows if r["n"] >= start]
    return {
        "session": f"{box_id}#{start}",
        "actions": actions(rows, start),
        "rows": [[r["n"], r["kind"], r["hash"]] for r in mine],
        "tip": mine[-1]["hash"] if mine else "",
    }


def servable(rows: list[dict], held: set[str]):
    """`ok(n)`: whether the record row `n` may be served with no scope seal —
    a row of the open session, or of a session whose subject the person
    sealed. A row before any check-in, or of a session never checked out,
    denied or still unanswered, never is."""
    opened = starts(rows)
    now = opened[-1] if opened else None
    sealed = {s for s, subj in subjects(rows).items() if subj in held}

    def ok(n: int) -> bool:
        s = _of(n, opened)
        return s is not None and (s == now or s in sealed)

    return ok
