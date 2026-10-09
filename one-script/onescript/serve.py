"""serve — only the tables in scope, to one file the model reads.

Cites: the security core and "Where the reading lands" (willows-grove,
docs/design/one-script/README.md); the four pieces join (willows-grove,
docs/design/one-box/four-pieces-join.md, "What one-box adds to serve").

The model is served only the tables in its scope, by ids it can't guess, and
nothing else: a table out of scope isn't named, counted or marked. The one
served file is the model's only reference (Miller), and holding it is the
permission. Code fills out every field here; the model never sees a box.

  scope   a human seal over the exact set of table hashes (Janus: the seal binds
          to one hash, never to an attempt or a session). A seal over a
          different set covers nothing.
  stack   the scope is a stack of piles (the operator, 2026-10-07: "yes to the
          stack"). A pile is one who/what/when/where group of record rows,
          its receipt the group key plus each row's number and hash, checked
          against the chain before anything is served. Code proposes the
          stack; the card lists the joins the model could make across it
          (the mosaic: the human judges the combination); the human seals
          its one hash.
  view    what the model reads is not the record. Row numbers, hashes, prev,
          content digests, versions and receipts stay on the record's side;
          the model gets the four W's and a named payload, with any full-width
          hash withheld, so it can't count, name or reverse what it wasn't
          served.
  ids     keyed with a serve key made fresh at each check-in, never the plain
          content hash: they hold for the session, can't be enumerated, and
          can't be linked from one session to the next. The serve key makes
          ids; it never signs authority and is never the seal key.
  trust   `human-sealed` when the record holds the human's seal over that
          table's hash, else `untrusted`. The label is read from the record,
          never from the table. It covers the table's authored rows, which
          are all that is served: the run's own rows (`who == "run"`) are
          not bound by a seal, so they are never served.
  return  one fixed line, the same every time, says what the model may return.
  fails   closed and loud. No sealed scope, a seal that covers nothing, a table
          without a receipt, no serve key: nothing is served, and the file
          says why. A sealed table the run can't find is `unreachable`, not
          `empty`.

Home: willow-bot (D2, sealed), with the rest of the skeleton.
"""

from __future__ import annotations

import hmac
import os
import re

from . import gate
from .record import Record, canon, h256

OUT = "served.json"
WS = ("who", "what", "when", "where")
SYSTEM = "run"  # gate.system()'s who: the run's rows about its own steps

# The payload a served row may carry besides its W's. Anything not named here
# is left out: a new field reaches the model only when someone adds it here.
PAYLOAD = ("family", "standing", "turn", "verdict", "reason", "intent", "bite")

# What each grouping can't hold, said to the model so absence isn't read as fact.
CANNOT_HOLD = {
    "who": "grouped by who, the identity the gate verified; an unverified "
    "claim is in no who-pile",
    "what": "grouped by what, the row's kind",
    "when": "grouped by when, the day on the run's clock",
    "where": "grouped by where; a row whose place wasn't recorded is in no "
    "where-pile, so a missing review or seal here may only be unrecorded",
}

RETURN = (
    'Return only rows of {"path": text, "data": text, "cites": [served table id], '
    '"claim": text}, one JSON object per line. A cite must be an id in this file. '
    'For an idea of your own that no table supports, give "cites": []; it is '
    "marked as yours and read only alongside this file. Nothing else is read."
)

_HASH = re.compile(r"[0-9a-f]{64}")
WITHHELD = "[hash withheld]"


def session_key() -> bytes:
    """A fresh serve key, made once at check-in and kept for the session."""
    return os.urandom(32)


def w_of(row: dict, w: str) -> str | None:
    """The four W's code can know without judging."""
    if w == "who":
        return row.get("who")
    if w == "what":
        return row.get("kind")
    if w == "when":
        return (row.get("ts") or "")[:10] or None  # the day
    if w == "where":
        return row.get("where") or row.get("path")
    raise ValueError(f"not a W: {w!r}")


def _scrub(value):
    """No full-width hash reaches the model, wherever it sits."""
    if isinstance(value, str):
        return _HASH.sub(WITHHELD, value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def view(row: dict) -> dict:
    """A record row as the model reads it: the W's and the named payload."""
    out = {
        "who": row.get("who"),
        "what": row.get("kind") or row.get("what"),
        "when": row.get("ts") or row.get("when"),
        "where": w_of(row, "where"),
    }
    out.update({k: row[k] for k in PAYLOAD if k in row})
    return _scrub(out)


def piles(rows: list[dict], *ws: str) -> list[dict]:
    """Group the record by the given W's: a GROUP BY, exact, nothing judged.
    A row missing any of them isn't in a pile; code doesn't guess it."""
    if not ws or any(w not in WS for w in ws):
        raise ValueError(f"group by one or more of {WS}, got {ws}")
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = tuple(w_of(r, w) for w in ws)
        if None not in key:
            groups.setdefault(key, []).append(r)
    return [
        {
            "rows": grp,
            "source": {
                "group": dict(zip(ws, key)),
                "rows": [[r["n"], r["hash"]] for r in grp],
            },
        }
        for key, grp in sorted(groups.items())
    ]


def name(pile: dict) -> str:
    """What the human reads on the card. The model never sees it."""
    src = pile["source"]
    group = src.get("group", {}) if isinstance(src, dict) else {}
    return " · ".join(f"{k}={v}" for k, v in group.items()) or str(src)


def joins(stack: list[dict]) -> dict:
    """The mosaic, as the model would see it: every W value that two or more
    piles share. Those are the joins the model can make; the human judges
    whether the combination is harmless before sealing."""
    seen: dict[str, dict[str, set[str]]] = {w: {} for w in WS}
    for p in stack:
        for r in p["rows"]:
            for w in WS:  # when by the day: the model can match days itself
                v = _scrub(w_of(r, w))
                if v is not None:
                    seen[w].setdefault(str(v), set()).add(name(p))
    return {
        w: {val: sorted(names) for val, names in sorted(vals.items()) if len(names) > 1}
        for w, vals in seen.items()
        if any(len(n) > 1 for n in vals.values())
    }


def choose(stack: list[dict], **match: str) -> list[dict]:
    """The piles of a stack whose group matches every given W."""
    return [
        p
        for p in stack
        if isinstance(p["source"], dict)
        and all(p["source"]["group"].get(k) == v for k, v in match.items())
    ]


def propose(stack: list[dict], **match: str) -> dict:
    """Code proposes the stack: every pile whose group matches. The card is for
    the human; sealing `subject` is the only thing that makes it a scope."""
    chosen = choose(stack, **match)
    ids = [table_id(p) for p in chosen]
    return {
        "subject": scope_subject(ids) if ids else None,
        "ids": ids,
        "stack": [
            {"name": name(p), "id": i, "rows": len(p["rows"])}
            for p, i in zip(chosen, ids)
        ],
        "joins": joins(chosen),
    }


def authored(rows: list[dict]) -> list[dict]:
    """The rows a scope can bind: everything but the run's own bookkeeping
    (`who == "run"`: invocation, boot, serve, ...). Every launch appends that, so
    a table id over it would move on each launch, the scope subject with it, and
    a scope the human sealed once would never match again. The seal binds the
    authored rows and the pile's group; the run's rows in a sealed pile are
    checked against the chain by `_receipt` but never served, since the seal
    does not bind them (served content is exactly the sealed content)."""
    return [r for r in rows if r.get("who") != SYSTEM]


def table_id(table: dict) -> str:
    """A table's content hash: its authored rows and its source receipt,
    canonically. The run's own rows are in no id, under any W."""
    rows, src = table["rows"], table["source"]
    if isinstance(src, dict) and "rows" in src:
        if len(src["rows"]) != len(rows):
            raise ValueError(
                f"table_id: source.rows ({len(src['rows'])}) does not line up "
                f"with rows ({len(rows)}); refusing an id that could carry run provenance"
            )
        src = {
            **src,
            "rows": [s for s, r in zip(src["rows"], rows) if r.get("who") != SYSTEM],
        }
    return h256(canon({"rows": authored(rows), "source": src}))


def scope_subject(ids) -> str:
    """What the human seals: one hash over the exact, sorted set of table ids."""
    return "serve:" + h256(canon(sorted(set(ids))))


def served_id(serve_key: bytes, tid: str) -> str:
    return gate.sign(serve_key, "served", tid)


def sealed(rows: list[dict]) -> set[str]:
    """Every subject the record holds a human seal over."""
    return {r["subject"] for r in rows if r["kind"] == "seal" and r["who"] == "human"}


def serve(
    rec: Record,
    tables: list[dict],
    scope: list[str] | None,
    serve_key: bytes | None,
    out: str = OUT,
    max_chars: int | None = None,
) -> dict:
    """Write the served file and record it. Returns the served document.
    The receipts and the scope stay in the record's row, never in the file.

    `max_chars` is the caller's cap on the served document's text (its
    canonical JSON). The caller sizes it, from the model's context: serve does
    not know the model. Over the cap the answer is `empty`, "narrow the stack",
    never a truncation. No cap is `empty` too: nothing is served uncapped."""
    try:
        doc = _serve(rec.rows(), tables, scope, serve_key, max_chars)
    except gate.Refused as e:
        doc = _nothing("empty", str(e))
    except Exception as e:  # serve that can't think clearly serves nothing
        doc = _nothing("empty", f"serve error, failing closed: {type(e).__name__}")
    data = (canon(doc) + "\n").encode()
    untrusted = any(t["trust"] == "untrusted" for t in doc["tables"])
    rec.write_file(
        gate.system(),
        out,
        data,
        provenance="third-party" if untrusted else "authored",
    )
    rec.append(
        "serve",
        gate.system(),
        where=out,
        state=doc["state"],
        why=doc["why"],
        scope=scope_subject(scope) if scope else None,
        served=[t["id"] for t in doc["tables"]],
        sha=h256(data),
    )
    return doc


def _nothing(state: str, why: str) -> dict:
    return {"state": state, "why": why, "return": RETURN, "tables": []}


def _serve(rows, tables, scope, serve_key, max_chars=None) -> dict:
    if max_chars is None:
        return _nothing(
            "empty", "no max_chars given; the caller sizes the cap, nothing is uncapped"
        )
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        return _nothing("empty", "max_chars must be a positive whole number")
    if not serve_key:
        return _nothing("empty", "no serve key; ids are made only after check-in")
    if not scope:
        return _nothing("empty", "no scope given; nothing is served without one")
    seals = sealed(rows)
    if scope_subject(scope) not in seals:
        return _nothing(
            "empty", "the scope has no human seal over this exact set; nothing served"
        )
    by_id = {}
    for t in tables:
        _receipt(t, rows)
        by_id[table_id(t)] = t
    missing = set(scope) - set(by_id)
    if missing:  # in scope, so a count is no leak; the plain hashes are
        return _nothing("unreachable", f"{len(missing)} sealed table(s) not found")
    served = []
    for tid in sorted(set(scope)):
        t = by_id[tid]
        src = t["source"]
        group = src.get("group", {}) if isinstance(src, dict) else {}
        served.append(
            {
                "id": served_id(serve_key, tid),
                "trust": "human-sealed" if tid in seals else "untrusted",
                "group": _scrub(group),
                "cannot_hold": [CANNOT_HOLD[w] for w in group if w in CANNOT_HOLD],
                # served == sealed: the seal binds the authored rows only, so a
                # run row (`who == "run"`) is never served, let alone labelled
                # human-sealed. `_receipt` above still chain-checks every row.
                "rows": [view(r) for r in authored(t["rows"])],
            }
        )
    served.sort(key=lambda t: t["id"])
    doc = {"state": "populated", "why": "", "return": RETURN, "tables": served}
    if len(canon(doc)) > max_chars:
        return _nothing(
            "empty", "the scope is too large for this model; narrow the stack"
        )
    return doc


def _receipt(table: dict, rows: list[dict]) -> None:
    """A pile's receipt is checked against the chain; a string receipt names an
    outside source, and is served as untrusted like everything unsealed."""
    src = table.get("source")
    if isinstance(src, str) and src.strip():
        return
    if not isinstance(src, dict) or not src.get("rows"):
        raise gate.Refused("a table without a source receipt is never served")
    at = {r["n"]: r for r in rows}
    cited = [at.get(n) for n, _ in src["rows"]]
    if (
        any(r is None or r["hash"] != h for r, (_, h) in zip(cited, src["rows"]))
        or cited != table["rows"]
    ):
        raise gate.Refused("a pile's receipt doesn't match the record; nothing served")


def check_cites(doc: dict, cited: list[str]) -> list[dict]:
    """A cite outside what was served is `link_fail`: a name the model can't
    have known. Caught here and escalated, never accepted as an answer."""
    known = [t["id"].encode() for t in doc.get("tables", [])]
    return [
        {"cite": c, "verdict": "link_fail", "why": "not in the served file"}
        for c in cited
        if not any(hmac.compare_digest(c.encode(errors="replace"), k) for k in known)
    ]
