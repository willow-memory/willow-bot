"""proposals — the Write is a proposal: parsed here, judged by the gate, written
only after the human's seal.

The model has no Write. It returns rows (Rat's build B writes this file):

    JSONL, one {"path": str, "data": str, "cites": [served id], "claim": str}
    per line. Exactly those four keys. `cites` is empty only for the model's own
    idea; code then marks the row `own_idea` (never the model), and it is
    accepted only alongside a served state of `populated`.

Code reads each row as data, never as instructions. A row that doesn't fit the
contract is not a crash and not a guess: it is recorded as `refused`, with the
line it came from and why, and the rest are still read.

A proposal's subject is `proposal:<sha256>` over the four fields, canonically.
The human seals that one hash; a changed path, byte, cite or claim is a
different proposal that needs its own seal.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import PurePosixPath
from typing import Iterable

from .record import canon, h256

KEYS = frozenset({"path", "data", "cites", "claim"})
SUBJECT = "proposal:"

#: Files the run keeps in the box for itself; a proposal never lands on them.
RESERVED = frozenset({"record.jsonl", "pile.json", "served.json"})


def subject(p: dict) -> str:
    """What the human seals: the exact path, data, cites and claim."""
    return SUBJECT + h256(canon({k: p[k] for k in ("path", "data", "cites", "claim")}))


#: A path longer than this is not a name a person can read on a card.
MAX_PATH = 240


def path_problem(path: object) -> str | None:
    """Why `path` is not a plain relative POSIX path, or None when it is.

    Judged as written, the way ratatosk judges it (`plain_relative_path`): the
    card shows exactly the bytes that will be written, so nothing is
    normalized or decoded first. No leading or trailing whitespace, no control,
    format or bidi character (U+202E included), no newline, no separator
    (U+2028, U+2029 or any other Z* character but the ASCII space), no space at
    a segment's start or end, no `~`, `%` or backslash, no '.', '..' or empty segment, and not absolute."""
    if not isinstance(path, str) or not path:
        return "path is empty or not text"
    if len(path) > MAX_PATH:
        return f"path is longer than {MAX_PATH} characters"
    if path != path.strip():
        return "path must be plain text: no leading or trailing whitespace"
    if any(unicodedata.category(c).startswith("C") for c in path):
        return "path must be plain text: no control, format or bidi character"
    # Separators (Z*: U+2028, U+2029, no-break and ideographic space, ...) are not
    # control characters, but a card can't show them as what they are. Only the
    # ASCII space is a space a person can read in a name.
    if any(unicodedata.category(c).startswith("Z") and c != " " for c in path):
        return "path must be plain text: no line, paragraph or other separator"
    if any(part != part.strip(" ") for part in path.split("/")):
        return "path must be plain text: no space at the start or end of a segment"
    if any(c in path for c in ("~", "%", "\\")):
        return "path must be plain text: no '~', '%' or backslash"
    if PurePosixPath(path).is_absolute() or any(
        part in ("", ".", "..") for part in path.split("/")
    ):
        return "path must be relative to the box, with no '.', '..' or empty part"
    if path.startswith("tmp/") or path in RESERVED:
        return "path is temp or one the run keeps for itself"
    return None


def check(obj: object) -> tuple[dict | None, str]:
    """(proposal, "") when `obj` fits the contract exactly; else (None, why)."""
    if not isinstance(obj, dict):
        return None, "a proposal is a JSON object"
    if set(obj) != KEYS:
        extra, gone = sorted(set(obj) - KEYS), sorted(KEYS - set(obj))
        return (
            None,
            f"keys must be exactly {sorted(KEYS)}; extra {extra}, missing {gone}",
        )
    if not isinstance(obj["path"], str) or not isinstance(obj["data"], str):
        return None, "path and data must be text"
    if not isinstance(obj["claim"], str):
        return None, "claim must be text"
    cites = obj["cites"]
    if not isinstance(cites, list) or not all(isinstance(c, str) and c for c in cites):
        return None, "cites must be a list of served table ids"
    bad = path_problem(obj["path"])
    if bad:
        return None, bad
    # An empty `cites` is the model's own idea (operator, 2026-10-07: "It should be
    # able to write it's own ideas, just marked as such"). Whether it is allowed is
    # a fact about the served file, not about the row, so `Run._take` decides it:
    # only alongside a served state of `populated`.
    try:
        canon(obj).encode("utf-8")  # every field, as the subject will hash it
    except (UnicodeEncodeError, ValueError):
        return None, "data or claim can't be written as UTF-8 text"
    return {k: obj[k] for k in sorted(KEYS)}, ""


def read(items: Iterable[object]) -> list[dict]:
    """Each input, in order, as {"line", "proposal" | None, "why"}.

    An input is a JSONL text line or an already-parsed object. Blank lines are
    skipped. Nothing here raises on bad input: it comes back as a row with
    `proposal` None and the reason, for the record."""
    out = []
    for i, item in enumerate(items, 1):
        if isinstance(item, (bytes, bytearray)):
            try:
                item = bytes(item).decode("utf-8")
            except UnicodeDecodeError:
                out.append({"line": i, "proposal": None, "why": "not UTF-8 text"})
                continue
        if isinstance(item, str):
            if not item.strip():
                continue
            try:
                item = json.loads(item)
            except (ValueError, RecursionError):
                out.append({"line": i, "proposal": None, "why": "not valid JSON"})
                continue
        p, why = check(item)
        out.append({"line": i, "proposal": p, "why": why})
    return out
