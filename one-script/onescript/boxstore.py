"""boxstore.py -- a box's store, opened and looked up: the store key, the one
store opener, the background build's finish, and `lookup`'s merge and its one
`lookup` row. Moved verbatim out of `api.py`, which re-exports every name, so
`prompt` can look a bite up without loading what a `Run` carries (`run.py`).
A `run` here is anything with a record and its own stamp (`rec`, `sys`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from . import pile as pile_mod
from . import record
from . import seed as seed_mod
from .opening import Config
from .states import EMPTY, RANKED, STEP_ESCALATED_COLD_START, UNREACHABLE

if TYPE_CHECKING:
    from .run import Run


def _repos(cfg: Config, index: dict) -> dict:
    where = {n: cfg.root.parent / n for n in index.get("arrival", {})}
    where.update({n: Path(p) for n, p in (cfg.repos or {}).items()})
    return where


def _store_key(cfg: Config, box: Path, make: bool = False):
    """(key, None) for a box's store key, or (None, why). The key lives in
    the operator's keys dir (`pile.key_name`, 0600), never in a box or beside
    its store, so the keyed hashes in a store are no dictionary to whoever
    holds the store alone. Made at check-in when absent, like `serve.key`;
    another box's key is only ever read, never made."""
    path = cfg.keys.parent / pile_mod.key_name(box)
    for inside in (box, pile_mod.store_path(box).parent):
        if path.resolve().is_relative_to(Path(inside).resolve()):
            return None, "the store key can't live inside a box or its store"
    if make:
        try:
            pile_mod.make_key(path)
        except OSError as e:
            return None, f"the store key can't be made: {type(e).__name__}"
    return pile_mod.read_key(path)


def _finish_building(cfg: Config, run: Run, att: dict) -> dict:
    """A `building` store, attached once its file is ready for this index and
    key: the `attached` row this verb writes (the builder writes no rows).
    Not ready yet: the `building` row as it stands."""
    index, sha, _ = seed_mod.read_index(run.rec)
    key, _ = _store_key(cfg, cfg.box)
    path = pile_mod.store_path(cfg.box)
    if index is None or key is None or sha != att.get("index_sha256"):
        return att
    if not pile_mod.cache_ready(index, _repos(cfg, index), path, sha, key):
        return att
    db = pile_mod.connect_ro(path)
    try:
        tally = pile_mod.Cached(db, key).tally()
    finally:
        db.close()
    row = {
        "state": "attached",
        "why": "",
        "how": "built in the background",
        "index_sha256": sha,
        "store": path.name,
        "store_sha256": pile_mod.file_sha256(path),
        **tally,
    }
    return run.rec.append("store", run.sys, where=path.name, **row)


def _open_store(cfg: Config, box: Path, run: Run | None = None):
    """The one store opener, for this check-in's store (`run`) or another
    box's: (Cached, None), or (None, why), never an exception.

    The store counts only while its box's last `store` row says `attached`,
    the row's `index_sha256` is the box's pile pointer now and the store's own
    meta says the same, and the file hashes to the row (`pile.open_store`:
    read once, read-only, capped). Another box is read through a record that
    can't write, and is trusted only when its chain verifies; what comes back
    of it is a fixed reason, never its own `why`. Its store key must be in
    this operator's keys dir, or it is `unreachable`, said. Nothing in
    another box is written or created."""
    box = Path(box)
    if run is not None:
        rec = run.rec
        rows = rec.since_last("boot")[1]  # seed.since_boot, read from the tip on
    else:
        if not (box / "record.jsonl").is_file():
            return None, "no record in that box"
        rec = record.Record(box, lambda: "", "", object())
        if rec.verify_chain():
            return None, "its record's chain doesn't verify"
        rows = rec.rows()
    att = next((r for r in reversed(rows) if r.get("kind") == "store"), None)
    if att is not None and att.get("state") == "building":
        if run is None:
            return None, "that session's store is still building"
        att = _finish_building(cfg, run, att)
        if att.get("state") == "building":
            return None, "the store is still building; it attaches when ready"
    if att is None:
        if run is not None:
            return None, "no store attached at this check-in"
        return None, "that session attached no store"
    if att.get("state") != "attached":
        if run is not None:
            return None, att.get("why") or "the store is unreachable"
        return None, "that session's store is unreachable"
    index, sha, why = seed_mod.read_index(rec)
    if index is None:
        return None, why if run is not None else "its index isn't vouched for"
    if att.get("index_sha256") != sha:
        return None, "the store was built for another index; check in again"
    key, why = _store_key(cfg, box)
    if key is None:
        return None, why
    return pile_mod.open_store(
        pile_mod.store_path(box), att.get("store_sha256"), key, sha
    )


SEED_TOP = (
    12  # baseline (D12): the ranked chunks a seed row keeps; scope draws from these
)


# ── lookup: across sessions, merged by hash ─────────────────────────────────
# What a session's lookup answer may say and keep: the ranker's variant, the
# named cold-start step (a step, NOT a state: states.STEP_ESCALATED_COLD_START), and two
# of the three states. Anything else is recorded as unreachable.
STATE_WORDS = (RANKED, STEP_ESCALATED_COLD_START, EMPTY, UNREACHABLE)


def _session_tag(key: bytes, box: Path) -> str:
    """A session's name in this box's `lookup` row: a short keyed hash of its
    box's path, so the row never holds another box's absolute path."""
    h = hashlib.blake2b(str(Path(box).resolve()).encode(), digest_size=6, key=key)
    return "box-" + h.hexdigest()


def _lookup(
    cfg: Config, run: Run, bite: str, boxes: Iterable[Path] = (), keep: int = 0
) -> dict:
    """`lookup`'s work on an open run: the merge, and its one `lookup` row.
    The row keeps SEED_TOP chunks; the answer keeps `keep` (default SEED_TOP),
    so a caller fusing a second rank has candidates past the row's."""
    stores, states, tags = {}, {}, {"this": "this"}
    mine, why = _open_store(cfg, cfg.box, run)
    if mine is None:
        states["this"] = {"state": UNREACHABLE, "why": why}
    else:
        stores["this"] = mine
    tag_key = pile_mod.read_key(cfg.keys.parent / pile_mod.key_name(cfg.box))[0]
    for i, box in enumerate(boxes):
        name = str(Path(box))
        tags[name] = _session_tag(tag_key, box) if tag_key else f"box-{i}"
        c, why = _open_store(cfg, Path(box))
        if c is None:
            states[name] = {"state": UNREACHABLE, "why": why}
        else:
            stores[name] = c
    try:
        out = pile_mod.merge(stores, bite)
    finally:
        for c in stores.values():
            c.db.close()
    out["sessions"] = {**states, **out["sessions"]}
    out["hits"] = out["hits"][: max(keep, SEED_TOP)]
    top = out["hits"][:SEED_TOP]
    run.rec.append(
        "lookup",
        run.sys,
        bite_hash=record.h256(bite),
        sessions={
            tags[n]: s["state"] if s["state"] in STATE_WORDS else UNREACHABLE
            for n, s in out["sessions"].items()
        },
        top=[[h["chunk"], h["seen_in"]] for h in top],
    )
    return out
