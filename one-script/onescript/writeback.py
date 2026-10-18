"""writeback — every escalation writes back, and what nothing answered is logged.

The loop (willows-grove `docs/design/one-script/next-pile.md`, "the loop", 6):
"the cloud's answer is recorded into the pile, tied to the words that missed,
so the next prompt is scanned against a pile that has something to hit". The
ladder (the one-script README, "the escalation ladder"): "every answer is
written back by the hash of the phrasing, so the next time it stops at rung 1
and nothing runs". The forge's intake contract (`the-forge-shape.md`): "a miss
log — what was asked that nothing answered".

Three pieces, each a record row, none a write into anything but the record:

    miss       a seed whose words the session's store has never seen (no
               posting, in any form `rank` would look for), and whose bite has
               no sentence said before. The row holds the bite's hash and the
               SHA-256 of each missing word, and the count: never a word.
    writeback  an escalation that settled with an answer (an `escalate_rung`
               row answered, or a proposal the human sealed) becomes a
               *candidate*: subject `writeback:<sha256>` over the bite's hash,
               the answer's hash and the cites (content hashes, never this
               session's keyed ids). Recorded `proposed`. It is `held` only
               while the human's seal over that exact subject verifies
               (`Run.held`: the HMAC proof, or a sealed Nestor pair whose
               conclusion is the subject, read from Nestor's store read-only).
    read-back  at the next seed, before anything is ranked, a held write-back
               whose bite hash is this bite's hash, exactly, answers it:
               `answered_by: writeback:<subject>`, with its cites. Nothing
               fuzzy: a byte of difference in the bite is another hash.

The model has no Write and the one script never writes into Nestor's store: a
seal is the human's, made in Nestor's own UI or with the human's key. Exporting
held write-backs to Nestor as draft pairs is the operator's decision, and is
not done here.

Three states at the read-back, never collapsed: a held write-back (`held`), no
candidate for this bite (`none`), or a candidate whose seal can't be checked
here (`unreachable`, with why). An unchecked seal is never a hit.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Iterable

from . import pile as pile_mod
from . import seed as seed_mod
from . import serve as served
from .record import canon, h256

MISS = "miss"
KIND = "writeback"
PREFIX = "writeback:"
PROPOSED = "proposed"
HELD, NONE, UNREACHABLE = "held", "none", "unreachable"

#: The fields a write-back's subject is the hash of, and nothing else.
SUBJECT_KEYS = ("bite_hash", "answer_hash", "cites")


# ── the subject ─────────────────────────────────────────────────────────────
def _hex64(x: object) -> bool:
    return (
        isinstance(x, str) and len(x) == 64 and all(c in "0123456789abcdef" for c in x)
    )


def subject(bite_hash: str, answer_hash: str, cites: Iterable[str]) -> str:
    """What the human seals: one hash over the bite, the answer and the cites,
    the cites as a sorted set. A changed byte anywhere is another subject."""
    body = {
        "bite_hash": bite_hash,
        "answer_hash": answer_hash,
        "cites": sorted(set(cites)),
    }
    return PREFIX + h256(canon(body))


def answer_hash(answer: object) -> str:
    return h256(canon(answer))


def well_formed(row: dict) -> str | None:
    """Why a `writeback` row is not the candidate it says it is, or None. Its
    subject must be the hash of its own fields, and its answer must hash to its
    `answer_hash`: a row that says one subject and carries other bytes is
    skipped, never trusted."""
    try:
        if not (_hex64(row.get("bite_hash")) and _hex64(row.get("answer_hash"))):
            return "a hash field is not a SHA-256"
        cites = row.get("cites")
        if not isinstance(cites, list) or not cites or not all(map(_hex64, cites)):
            return "the cites are not content hashes"
        if answer_hash(row.get("answer")) != row["answer_hash"]:
            return "the answer doesn't hash to its answer_hash"
        if subject(row["bite_hash"], row["answer_hash"], cites) != row.get("subject"):
            return "the row doesn't hash to its subject"
    except (TypeError, ValueError):
        return "the row can't be hashed"
    return None


def candidates(rows: list[dict]) -> list[dict]:
    """Every well-formed write-back row on record, in order. Each is judged on
    its own bytes: a row is never taken on the word of another row with the
    same subject."""
    return [r for r in rows if r.get("kind") == KIND and well_formed(r) is None]


def subjects(rows: list[dict]) -> set[str]:
    return {r["subject"] for r in candidates(rows)}


# ── the bite an answer is tied to ───────────────────────────────────────────
def bite_of(rows: list[dict], fallback: str | None = None) -> tuple[str | None, str]:
    """(bite hash, where it came from): this check-in's seed (the words that
    missed), else the hash of `fallback` as written (the task, or the turn's
    own bite), else (None, why)."""
    sd = next((r for r in seed_mod.since_boot(rows) if r["kind"] == "seed"), None)
    if sd is not None and _hex64(sd.get("bite_hash")):
        return sd["bite_hash"], "seed"
    if isinstance(fallback, str) and fallback:
        return h256(fallback), "task"
    return None, "no seed in this check-in and no bite to tie the answer to"


# ── cites: this session's keyed ids back to content ─────────────────────────
def cite_map(run, spec_tables: Callable) -> dict[str, str]:
    """{served id: content hash} for this check-in's served file: a chunk's own
    SHA-256, else the table's content id. Read from the scope row the serve row
    names; {} when nothing stands (then no cite maps and nothing is proposed).
    The served ids are keyed with this session's serve key, so they mean
    nothing in another session; the content hashes do."""
    rows = run.rec.rows()
    sv = next(
        (r for r in reversed(seed_mod.since_boot(rows)) if r["kind"] == "serve"), None
    )
    key = getattr(run, "serve_key", None)
    if sv is None or not sv.get("scope") or not key:
        return {}
    sc = next(
        (
            r
            for r in reversed(rows)
            if r["kind"] == "scope" and r.get("where") == sv["scope"]
        ),
        None,
    )
    if sc is None:
        return {}
    try:
        tables, _ = spec_tables(run, sc["spec"])
    except (ValueError, KeyError, TypeError):
        return {}
    out = {}
    for t in tables:
        tid = served.table_id(t)
        content = t["source"]["chunk"]["sha256"] if served.is_chunk(t) else tid
        out[served.served_id(key, tid)] = content
    return out


def _content(cites: object, cmap: dict[str, str]) -> tuple[list[str] | None, str]:
    if not isinstance(cites, list) or not cites:
        return None, "no cites: an answer with nothing behind it isn't written back"
    got = []
    for c in cites:
        if not isinstance(c, str) or c not in cmap:
            return None, "a cite is not a served id of this check-in"
        got.append(cmap[c])
    return sorted(set(got)), ""


def _mapped_rows(rows: list, cmap: dict[str, str]) -> list:
    out = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("cites"), list):
            r = {**r, "cites": [cmap.get(c, c) for c in r["cites"]]}
        out.append(r)
    return out


# ── proposing a write-back ──────────────────────────────────────────────────
def propose(run, *, bite_hash, bite_from, answer, cites, source: dict) -> dict:
    """Record one candidate, `proposed`; the same subject already on record is
    returned as it stands, not recorded twice."""
    ah = answer_hash(answer)
    s = subject(bite_hash, ah, cites)
    have = next((r for r in candidates(run.rec.rows()) if r["subject"] == s), None)
    if have is not None:
        return {"state": PROPOSED, "subject": s, "row": have["n"], "new": False}
    row = run.rec.append(
        KIND,
        run.sys,
        where=s,
        subject=s,
        state=PROPOSED,
        bite_hash=bite_hash,
        bite_from=bite_from,
        answer_hash=ah,
        answer=answer,
        cites=sorted(set(cites)),
        source=source,
    )
    return {"state": PROPOSED, "subject": s, "row": row["n"], "new": True}


def from_escalate(run, out: dict, task: str, spec_tables: Callable) -> list[dict]:
    """A candidate for each piece of an escalation that settled `answered`.
    Anything that can't be tied to a bite or to content cites is not proposed,
    and says why."""
    rows = run.rec.rows()
    bh, frm = bite_of(rows, task)
    cmap = cite_map(run, spec_tables)
    by_n = {r["n"]: r for r in rows}
    got = []
    for p in out.get("pieces") or []:
        if p.get("outcome") != "answered":
            continue
        src = by_n.get(p.get("row"))
        if src is None or src.get("kind") != "escalate_rung":
            got.append(
                {"piece": p.get("piece"), "state": "not_proposed", "why": "no rung row"}
            )
            continue
        if bh is None:
            got.append({"piece": src["piece"], "state": "not_proposed", "why": frm})
            continue
        cites, why = _content(src.get("cites"), cmap)
        if cites is None:
            got.append({"piece": src["piece"], "state": "not_proposed", "why": why})
            continue
        answer = {
            "detail": src.get("detail"),
            "rows": _mapped_rows(src.get("rows") or [], cmap),
        }
        res = propose(
            run,
            bite_hash=bh,
            bite_from=frm,
            answer=answer,
            cites=cites,
            source={"kind": "escalate_rung", "row": src["n"], "piece": src["piece"]},
        )
        got.append({"piece": src["piece"], **res})
    return got


def from_proposal(run, prop: dict, spec_tables: Callable) -> dict:
    """A candidate for a proposal the human sealed (and was written)."""
    rows = run.rec.rows()
    turn_bite = next(
        (
            r.get("bite")
            for r in rows
            if r["kind"] == "bite" and r.get("turn") == prop.get("turn")
        ),
        None,
    )
    bh, frm = bite_of(rows, turn_bite)
    if bh is None:
        return {"state": "not_proposed", "why": frm}
    cmap = cite_map(run, spec_tables)
    cites, why = _content(prop.get("cites"), cmap)
    if cites is None:
        return {"state": "not_proposed", "why": why}
    answer = {
        "path": prop["path"],
        "data": prop["data"],
        "claim": prop["claim"],
        "cites": cites,
    }
    return propose(
        run,
        bite_hash=bh,
        bite_from=frm,
        answer=answer,
        cites=cites,
        source={"kind": "proposal", "row": prop["n"], "subject": prop["subject"]},
    )


# ── the read-back at rung 1 ─────────────────────────────────────────────────
def read_back(run, bite_hash: str) -> dict:
    """The held write-back that answers this exact bite, or why none does.
    `{"state": "held", "answered_by", "cites", "row"}`, `{"state": "none"}`,
    or `{"state": "unreachable", "why"}` when a candidate's seal can't be
    checked here. Only a seal that verifies counts."""
    rows = run.rec.rows()
    mine = [r for r in candidates(rows) if r["bite_hash"] == bite_hash]
    if not mine:
        return {"state": NONE}
    held, unchecked = run.held(rows)
    hit = next((r for r in reversed(mine) if r["subject"] in held), None)
    if hit is not None:
        return {
            "state": HELD,
            "answered_by": hit["subject"],
            "cites": hit["cites"],
            "row": hit["n"],
        }
    stuck = next((r for r in mine if r["subject"] in unchecked), None)
    if stuck is not None:
        return {
            "state": UNREACHABLE,
            "why": unchecked[stuck["subject"]],
            "subject": stuck["subject"],
        }
    return {"state": NONE, "unsealed": len(mine)}


def state_of(run, subj: str) -> dict:
    """A candidate's state as the record stands: proposed, held, or unreachable."""
    rows = run.rec.rows()
    if subj not in subjects(rows):
        return {"state": "none", "why": "no such write-back on record"}
    held, unchecked = run.held(rows)
    if subj in held:
        return {"state": HELD}
    if subj in unchecked:
        return {"state": UNREACHABLE, "why": unchecked[subj]}
    return {"state": PROPOSED}


# ── the miss log ────────────────────────────────────────────────────────────
def word_hash(w: str, key: bytes) -> str:
    """A missing word, keyed with the box's store key: a plain hash of one
    word is a dictionary entry ("a plain hash of a child's name is itself a
    rainbow-table entry", next-pile.md). So miss rows compare within a box,
    never across boxes."""
    return hashlib.blake2b(w.encode(), digest_size=16, key=key).hexdigest()


def missing(cached, words: Iterable[str]) -> list[str]:
    """The words the store holds no posting for, in any form `rank` looks for."""
    return [
        w
        for w in words
        if not any(cached.postings(f) for f in sorted(pile_mod.forms(w)))
    ]


def log_miss(run, seed_row: dict, lost: list[str]) -> dict:
    """One `miss` row: the bite's hash, each missing word keyed (`word_hash`,
    hashed while the store was open, so no word reaches here), the count, and
    the check-in. Never a word."""
    hashes = sorted(set(lost))
    boot = next((r for r in reversed(run.rec.rows()) if r["kind"] == "boot"), None)
    row = run.rec.append(
        MISS,
        run.sys,
        where=f"miss:{seed_row['bite_hash']}",
        bite_hash=seed_row["bite_hash"],
        seed_row=seed_row["n"],
        checkin=boot["n"] if boot else None,
        missing=hashes,
        count=len(hashes),
    )
    return {"state": MISS, "row": row["n"], "count": len(hashes)}


def at_seed(run, bite: str, words: list[str], open_store: Callable) -> dict:
    """The store's half of the miss log, taken before the seed row is written:
    the missing words (kept only in memory) and whether the bite was said
    before. {"lost": [...]} or {"state": ..., "why"}."""
    c, why = open_store()
    if c is None:
        return {"state": UNREACHABLE, "why": why}
    try:
        lost = missing(c, words)
        q = c.quotes(bite) if lost else {"state": "empty"}
    except pile_mod.BROKEN as e:  # a store that can't be read: unknown, not a miss
        return {
            "state": UNREACHABLE,
            "why": f"the store can't be read: {type(e).__name__}",
        }
    finally:
        c.db.close()
    if q["state"] == "unreachable":  # unknown is not a miss
        return {"state": UNREACHABLE, "why": q["why"]}
    quoted = q["state"] == "populated"
    if not lost:
        return {"state": NONE}
    if quoted:
        return {"state": NONE, "quoted": True}
    return {"state": MISS, "lost": sorted({word_hash(w, c.key) for w in lost})}


def needs(rows: list[dict], held: set[str]) -> dict:
    """The counts the morning screen can say: "N misses since <ts>", and the
    write-backs waiting on a seal. Counts only, never a word."""
    misses = [r for r in rows if r.get("kind") == MISS]
    cands = subjects(rows)
    waiting = sum(1 for s in cands if s not in held)
    since = misses[0]["ts"] if misses else None
    return {
        "misses": len(misses),
        "since": since,
        "line": f"{len(misses)} misses since {since}" if misses else "no misses",
        "writebacks_proposed": waiting,
        "writebacks_held": len(cands) - waiting,
    }
