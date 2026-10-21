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
          table's hash, its proof re-checked (`sealed_verified`), else
          `untrusted`. The label is read from the record, never from the
          table, and never from a row's `who` alone. It covers the table's authored rows, which
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

import os
import re
import unicodedata

from . import gate, lore, nestor_seal, rules, scrub, sign
from .record import Record, canon, h256
from .states import EMPTY, POPULATED, UNREACHABLE

OUT = "served.json"
WS = ("who", "what", "when", "where")
SYSTEM = "run"  # gate.system()'s who: the run's rows about its own steps

# The payload a served row may carry besides its W's. Anything not named here
# is left out: a new field reaches the model only when someone adds it here.
PAYLOAD = (
    "family",
    "standing",
    "turn",
    "verdict",
    "reason",
    "intent",
    "bite",
    "fact",  # lore (R11): one fact, served only when `lore.servable`
    "body",  # rules (T-mai-2): one `@phase` body, served only when `rules.servable`
    "total",  # a table roll's 2d6+stat sum (R12): the D0 `table.roll` answer
    "session",  # which table session a verdict row belongs to (W3b B4);
    # `table.check_row` keeps it plain text, so it is safe on a card
)

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

_ZW = re.compile("[\u200b-\u200f\u2060\u2064\ufeff\u00ad]")
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


def _marker(kind):
    return WITHHELD if kind == "hash64" else f"[REDACTED:{kind}]"


def _scrub(value):
    """No full-width hash reaches the model, wherever it sits. Since flatten-2
    F12 the rule is ``scrub.py``'s: the hash first, as it always was (its
    marker is still ``WITHHELD``), then the credential shapes, which are added
    coverage (a key in a record's text reaches the model no more than a hash)."""
    if isinstance(value, str):
        return scrub.apply(value, ("hash64", "credentials"), marker=_marker)
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
    # `fact` is lore (R11): it rides only on a lore row, which `_serve` has
    # already passed through `lore.servable`. On any other row it is not payload.
    islore = (row.get("kind") or row.get("what")) == lore.KIND
    # `total` and `session` are the table's (R12, N3): they ride only on a
    # verdict row the `table` identity wrote.
    istable = row.get("kind") == "verdict" and row.get("who") == "table"
    isrules = (row.get("kind") or row.get("what")) == rules.KIND
    only = {"fact": islore, "body": isrules, "total": istable, "session": istable}
    out.update({k: row[k] for k in PAYLOAD if k in row and only.get(k, True)})
    # `dice`: a table roll row's own dice, `roll.result` (W4 break 2), so a
    # reader can see a `total` or `verdict` that disagrees with them. Only the
    # sum: the roll record's seed id and counters stay off the card.
    roll = row.get("roll") if istable else None
    if isinstance(roll, dict) and isinstance(roll.get("result"), int):
        out["dice"] = roll["result"]
    # A hash in another form is still a hash: `fact` and a lore row's scene are
    # rewritten to NFKC (so ligatures and fullwidth forms read as plain text; the
    # served fact can differ from the sealed bytes in those forms only) and
    # stripped of zero-width characters before the scrub looks for 64-hex.
    for k in ("fact", "where") if islore else ():
        if isinstance(out.get(k), str):
            out[k] = _ZW.sub("", unicodedata.normalize("NFKC", out[k]))
    return _scrub(out)


def scope_seal(row: dict) -> bool:
    """A human seal over a scope subject (`serve:<hash>`). It is about a scope,
    so it is in none: as a `who == "human"` row it would otherwise land in the
    human's pile (and the seal kind's, and the day's), change that pile's id,
    and move the very subject it seals; sealing the new subject would move it
    again, forever. Tip and wager seals are the human's own acts and stay."""
    return row.get("kind") == "seal" and str(row.get("subject") or "").startswith(
        "serve:"
    )


def piles(rows: list[dict], *ws: str) -> list[dict]:
    """Group the record by the given W's: a GROUP BY, exact, nothing judged.
    A row missing any of them isn't in a pile; code doesn't guess it. A scope
    seal is in no pile (`scope_seal`): sealing a scope never adds or moves one."""
    if not ws or any(w not in WS for w in ws):
        raise ValueError(f"group by one or more of {WS}, got {ws}")
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if scope_seal(r):
            continue
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


# ── repo chunks: the session store's pointers, as tables ──────────────────
def is_chunk(table: dict) -> bool:
    src = table.get("source")
    return isinstance(src, dict) and isinstance(src.get("chunk"), dict)


def chunk_name(table: dict) -> str:
    c = table["source"]["chunk"]
    return f"{c['repo']}:{c['path']}:{c['line']}"


def chunk_tables(seed_row: dict, top: int) -> list[dict]:
    """The seed's top chunks, each a table: no rows, and a source that names
    the chunk by repo, path, line and its own SHA-256, with its receipt: the
    seed row's number and hash, chain-checked at serve (`_seed_receipt`). Its
    id is that content, so a seal over it binds exactly those bytes; the text
    is read at serve."""
    out = []
    receipt = [seed_row.get("n"), seed_row.get("hash")]
    for h in (seed_row.get("rank") or [])[: max(top, 0)]:
        ch = {
            "repo": h["repo"],
            "path": h["path"],
            "line": h["line"],
            "sha256": h["chunk"],
        }
        out.append({"rows": [], "source": {"chunk": ch, "seed": receipt}})
    return out


def propose_chunks(tables: list[dict]) -> dict:
    """The card for chunk tables: their names, and the files two or more share
    (the mosaic: the human judges the combination)."""
    ids = [table_id(t) for t in tables]
    files: dict[str, list[str]] = {}
    for t in tables:
        c = t["source"]["chunk"]
        files.setdefault(f"{c['repo']}:{c['path']}", []).append(chunk_name(t))
    return {
        "ids": ids,
        "stack": [
            {"name": chunk_name(t), "id": i, "rows": 1, "kind": "chunk"}
            for t, i in zip(tables, ids)
        ],
        "joins": {f: sorted(n) for f, n in sorted(files.items()) if len(n) > 1},
    }


def _binds(row: dict) -> bool:
    """Whether a scope's seal binds this row: not the run's own bookkeeping,
    and not a scope seal (`scope_seal`)."""
    return row.get("who") != SYSTEM and not scope_seal(row)


def authored(rows: list[dict]) -> list[dict]:
    """The rows a scope can bind: everything but the run's own bookkeeping
    (`who == "run"`: invocation, boot, serve, ...) and scope seals. Every launch
    appends the first and every seal the second, so a table id over either would
    move, the scope subject with it, and a scope the human sealed once would
    never match again. The seal binds the authored rows and the pile's group;
    the run's rows in a sealed pile are checked against the chain by `_receipt`
    but never served, since the seal does not bind them (served content is
    exactly the sealed content)."""
    return [r for r in rows if _binds(r)]


def table_id(table: dict) -> str:
    """A table's content hash: its authored rows and its source receipt,
    canonically. The run's own rows and scope seals are in no id, under any W."""
    rows, src = table["rows"], table["source"]
    if isinstance(src, dict) and "rows" in src:
        if len(src["rows"]) != len(rows):
            raise ValueError(
                f"table_id: source.rows ({len(src['rows'])}) does not line up "
                f"with rows ({len(rows)}); refusing an id that could carry run provenance"
            )
        src = {
            **src,
            "rows": [s for s, r in zip(src["rows"], rows) if _binds(r)],
        }
    return h256(canon({"rows": authored(rows), "source": src}))


def scope_subject(ids) -> str:
    """What the human seals: one hash over the exact, sorted set of table ids."""
    return "serve:" + h256(canon(sorted(set(ids))))


def served_id(serve_key: bytes, tid: str) -> str:
    return gate.sign(serve_key, "served", tid)


def claimed_seals(rows: list[dict]) -> set[str]:
    """Every subject the record holds a row CLAIMING a human seal over. A claim
    only: anyone who can write the record can append such a row (A5). Nothing
    that grants authority reads this; it reads `sealed_verified`."""
    return {r["subject"] for r in rows if r["kind"] == "seal" and r["who"] == "human"}


def sealed_verified(
    rows: list[dict],
    human_key: bytes | None = None,
    keyring: dict | str | os.PathLike | None = None,
    nestor_db: str | os.PathLike | None = None,
    through: int | None = None,
    unpinned: tuple[int, str] | None = None,
) -> tuple[set[str], dict[str, str]]:
    """(held, unreachable): the subjects whose seal row carries a proof that
    checks out against that subject right now, and the subjects whose proof
    could not be checked here, each with why.

    A row's `who == "human"` is a label; the proof on the row is the seal. A key
    seal carries the HMAC `proof` and is checked with the human's key; a Nestor
    seal carries the `pair` and is checked against the operator's keyring and
    Nestor's store as it stands now. A row with no proof, or one that does not
    verify, is in neither set: it was never a seal. With no key, keyring or
    store to check by, the answer is `unreachable`, never a pass.

    A row with no proof at all, written before seal rows carried one, holds
    only up to `through`: the tip the human had sealed when this code first
    wrote to the box, while the sealed tip still covers it (`Run.old_seals`,
    `reverse.pinned`). After it, the row is a claim, nothing more. When the box
    has no pin yet, `unpinned` is (the sealed tip's row, why): a proofless row
    inside that tip is `unreachable`, said, never passed."""
    held: set[str] = set()
    unreachable: dict[str, str] = {}
    ring: dict | None = keyring if isinstance(keyring, dict) else None
    ring_why: str | None = None
    if ring is None and keyring is not None:
        try:
            ring = nestor_seal.load_keyring(keyring)
        except nestor_seal.Unverifiable as e:
            ring_why = f"unreachable: {e}"
    for r in rows:
        subject = r.get("subject")
        if r.get("kind") != "seal" or r.get("who") != "human":
            continue
        if not isinstance(subject, str) or subject in held:
            continue
        ok, why = _seal_proof(r, subject, human_key, ring, ring_why, nestor_db)
        if not ok and why is None and _before_proofs(r, through):
            ok = True
        elif not ok and why is None and unpinned and _before_proofs(r, unpinned[0]):
            why = unpinned[1]
        if ok:
            held.add(subject)
            unreachable.pop(subject, None)
        elif why is not None:
            unreachable.setdefault(subject, why)
    return held, unreachable


def _before_proofs(row: dict, through: int | None) -> bool:
    """A seal row with no evidence on it, at or before row `through`. An old
    Nestor row (`via: nestor`, no `pair`) is one: rows carried no pair then."""
    if through is None or "proof" in row or "pair" in row:
        return False
    return isinstance(row.get("n"), int) and row["n"] <= through


def _seal_proof(row, subject, human_key, ring, ring_why, nestor_db):
    """(True, None) when the row's proof verifies; (False, why) when it can't be
    checked here; (False, None) when it is no seal at all."""
    if row.get("via") == "nestor":
        pair = row.get("pair")
        if nestor_seal.shape(pair, subject) or pair["verifier"] != row.get("verifier"):  # type: ignore[index]
            return False, None  # malformed: no seal, whatever can be reached
        if ring is None:
            return False, ring_why or "unreachable: no operator keyring to check by"
        if nestor_db is None:
            return False, "unreachable: no Nestor store to confirm it"
        if not nestor_seal.have_ed25519():
            return False, nestor_seal.NO_CRYPTO
        who, _ = nestor_seal.check(pair, subject, ring)
        if who is None or who != row.get("verifier"):
            return False, None
        try:
            live = nestor_seal.current(nestor_db, subject, pair)
        except nestor_seal.Unverifiable as e:
            return False, f"unreachable: {e}"
        return bool(live), None
    proof = row.get("proof")
    if not isinstance(proof, str):
        return False, None
    if not human_key:
        return False, "unreachable: no human key to check the seal by"
    return gate.verify_seal(subject, proof, human_key), None


def serve(
    rec: Record,
    tables: list[dict],
    scope: list[str] | None,
    serve_key: bytes | None,
    out: str = OUT,
    max_chars: int | None = None,
    *,
    human_key: bytes | None = None,
    keyring: dict | str | os.PathLike | None = None,
    nestor_db: str | os.PathLike | None = None,
    through: int | None = None,
    unpinned: tuple[int, str] | None = None,
    read_chunk=None,
    session=None,
) -> dict:
    """Write the served file and record it. Returns the served document.
    `read_chunk(chunk) -> (text, why)` reads a chunk table's text at its
    indexed blob (`pile.read_chunk`); without it a chunk is unreachable.
    The receipts and the scope stay in the record's row, never in the file.

    `max_chars` is the caller's cap on the served document's text (its
    canonical JSON). The caller sizes it, from the model's context: serve does
    not know the model. Over the cap the answer is `empty`, "narrow the stack",
    never a truncation. No cap is `empty` too: nothing is served uncapped.

    `session` is set when the one seal comes at the session's end
    (`Config.seal_at_end`, `onescript.session`): a scope nobody sealed is then
    served, marked `unsealed: this session`, with only the record rows
    `session(n)` passes (the open session's, and sealed sessions')."""
    try:
        rows = rec.rows()
        seals = sealed_verified(
            rows, human_key, keyring, nestor_db, through, unpinned=unpinned
        )
        doc = _serve(
            rows, tables, scope, serve_key, max_chars, seals, read_chunk, session
        )
        if doc["state"] == POPULATED and _has_lore(doc, tables, scope):
            # lore rests on the stored proposal bytes: a rewritten record serves none
            broken = rec.verify_chain()
            if broken:
                what = (
                    "rules"
                    if any(
                        r.get("kind") == rules.KIND
                        for t in tables
                        if table_id(t) in set(scope or ())
                        for r in t["rows"]
                    )
                    and not any(
                        r.get("kind") == lore.KIND
                        for t in tables
                        if table_id(t) in set(scope or ())
                        for r in t["rows"]
                    )
                    else "lore"
                )
                doc = _nothing(
                    UNREACHABLE,
                    f"the record's chain is broken ({broken[0]}); no {what} is served",
                )
    except gate.Refused as e:
        doc = _nothing(EMPTY, str(e))
    except Exception as e:  # serve that can't think clearly serves nothing
        doc = _nothing(EMPTY, f"serve error, failing closed: {type(e).__name__}")
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


def _has_lore(doc: dict, tables: list[dict], scope) -> bool:
    """Whether any scoped row rests on stored proposal bytes (lore or rules)."""
    ids = set(scope or ())
    return any(
        r.get("kind") in (lore.KIND, rules.KIND)
        for t in tables
        if table_id(t) in ids
        for r in t["rows"]
    )


def _nothing(state: str, why: str) -> dict:
    return {"state": state, "why": why, "return": RETURN, "tables": []}


#: What a scope served with no seal says of itself (`Config.seal_at_end`).
UNSEALED = "unsealed: this session"


def _serve(
    rows,
    tables,
    scope,
    serve_key,
    max_chars=None,
    seals=None,
    read_chunk=None,
    session=None,
) -> dict:
    if max_chars is None:
        return _nothing(
            EMPTY, "no max_chars given; the caller sizes the cap, nothing is uncapped"
        )
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        return _nothing(EMPTY, "max_chars must be a positive whole number")
    if not serve_key:
        return _nothing(EMPTY, "no serve key; ids are made only after check-in")
    if not scope:
        return _nothing(EMPTY, "no scope given; nothing is served without one")
    seals, unchecked = seals if seals is not None else (set(), {})
    sealed_scope = scope_subject(scope) in seals
    if not sealed_scope and session is None:
        if scope_subject(scope) in unchecked:
            return _nothing(
                UNREACHABLE,
                "the scope's seal can't be checked here "
                f"({unchecked[scope_subject(scope)]})",
            )
        return _nothing(
            EMPTY, "the scope has no human seal over this exact set; nothing served"
        )
    in_session = (lambda r: True) if sealed_scope else (lambda r: session(r["n"]))
    by_id = {}
    for t in tables:
        if is_chunk(t):  # its seed row on the chain here; its blob at the read
            _seed_receipt(t, rows)
        else:
            _receipt(t, rows)
        by_id[table_id(t)] = t
    missing = set(scope) - set(by_id)
    if missing:  # in scope, so a count is no leak; the plain hashes are
        return _nothing(UNREACHABLE, f"{len(missing)} sealed table(s) not found")
    ctx = rules.Index(rows)  # once per serve, not once per row; lore reads it too
    served = []
    for tid in sorted(set(scope)):
        t = by_id[tid]
        if is_chunk(t):
            served.append(_chunk_entry(t, tid, serve_key, seals, read_chunk))
            continue
        src = t["source"]
        group = src.get("group", {}) if isinstance(src, dict) else {}
        stuck = lore.unchecked_seals(t["rows"], ctx, seals, unchecked)
        stuck_rules = rules.unchecked_seals(t["rows"], ctx, seals, unchecked)
        entry = {
            "id": served_id(serve_key, tid),
            "trust": "human-sealed" if tid in seals else "untrusted",
            "group": _scrub(group),
            "cannot_hold": [CANNOT_HOLD[w] for w in group if w in CANNOT_HOLD],
            # served == sealed: the seal binds the authored rows only, so a
            # run row (`who == "run"`) is never served, let alone labelled
            # human-sealed. `_receipt` above still chain-checks every row.
            "rows": [
                view(r)
                for r in authored(t["rows"])
                if in_session(r)
                and lore.servable(r, ctx, seals)
                and rules.servable(r, ctx, seals)
            ],
        }
        if (
            stuck or stuck_rules
        ):  # said on the table, never shown as empty (INVARIANTS section 1)
            entry["state"] = UNREACHABLE
            kind = (
                "lore"
                if stuck and not stuck_rules
                else ("rules" if stuck_rules and not stuck else "lore and rules")
            )
            both = {**stuck, **stuck_rules}
            entry["why"] = (
                f"{len(both)} {kind} proposal seal(s) can't be checked here "
                f"({next(iter(both.values()))}); those {kind} rows are not served"
            )
        served.append(entry)
    served.sort(key=lambda t: t["id"])
    doc = {"state": POPULATED, "why": "", "return": RETURN, "tables": served}
    if not sealed_scope:
        doc["seal"] = UNSEALED
    if served and all(t.get("state") == UNREACHABLE and not t["rows"] for t in served):
        doc["state"], doc["why"] = UNREACHABLE, served[0]["why"]
    if len(canon(doc)) > max_chars:
        return _nothing(
            EMPTY, "the scope is too large for this model; narrow the stack"
        )
    return doc


def _chunk_entry(t: dict, tid: str, serve_key: bytes, seals, read_chunk) -> dict:
    """A repo chunk as the model reads it: its place and its text, re-read at
    the indexed blob now. A chunk is someone's file, not a sealed row, so it is
    `untrusted` unless its own id carries the human's seal."""
    where = chunk_name(t)
    entry = {
        "id": served_id(serve_key, tid),
        "trust": "human-sealed" if tid in seals else "untrusted",
        "group": _scrub({"where": where}),
        "cannot_hold": [],
        "rows": [],
    }
    if read_chunk is None:
        text, why = None, "no reader for repo chunks in this serve"
    else:
        text, why = read_chunk(t["source"]["chunk"])
    if text is None:
        entry["state"], entry["why"] = UNREACHABLE, f"{where}: {why}"
    else:
        entry["rows"] = [
            {"what": "chunk", "where": _scrub(where), "text": _scrub(text)}
        ]
    return entry


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


def _seed_receipt(table: dict, rows: list[dict]) -> None:
    """A chunk's receipt is its seed row, checked against the chain like a
    pile's: the row at that number is a seed row, carries that hash, hashes to
    it, and ranks this very chunk. A chunk without one is never served."""
    src, ch = table["source"], table["source"]["chunk"]
    rc = src.get("seed")
    if not (
        isinstance(rc, list)
        and len(rc) == 2
        and isinstance(rc[0], int)
        and not isinstance(rc[0], bool)
    ):
        raise gate.Refused("a chunk without a seed receipt is never served")
    n, h = rc
    row = rows[n] if 0 <= n < len(rows) else None
    body = {k: v for k, v in (row or {}).items() if k != "hash"}
    if (
        row is None
        or row.get("n") != n
        or row.get("kind") != "seed"
        or row.get("hash") != h
        or h256(canon(body)) != h
        or not any(
            isinstance(r, dict)
            and [r.get("repo"), r.get("path"), r.get("line"), r.get("chunk")]
            == [ch.get("repo"), ch.get("path"), ch.get("line"), ch.get("sha256")]
            for r in row.get("rank") or []
        )
    ):
        raise gate.Refused(
            "a chunk's seed receipt doesn't match the record; nothing served"
        )


def check_cites(doc: dict, cited: list[str]) -> list[dict]:
    """A cite outside what was served is `link_fail`: a name the model can't
    have known. Caught here and escalated, never accepted as an answer."""
    known = [t["id"].encode() for t in doc.get("tables", [])]
    return [
        {"cite": c, "verdict": "link_fail", "why": "not in the served file"}
        for c in cited
        if not any(sign.hmac_equal(c, k) for k in known)
    ]
