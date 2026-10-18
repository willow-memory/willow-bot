"""The repetition ladder over bites, and the tombstones of chunks gone.

workflow.md §2c (operator, 2026-10-02): every prompt is a bite, and
repetition decides what the system does next, by count.

| Times asked | The system |
|---|---|
| 1 | does it, and records it |
| 2 | notices it: the seed row says `noticed` |
| 3 | offers to make it standing: an `offer` row, listed in NEEDS YOU |

The count is exact: seed rows whose `bite_key` is the same, counted from the
record. The key is the SHA-256 of the bite's words, lowercased, in order,
joined by one space; the bite's text is never stored. The offer is made again
at every OFFER_AT-th ask (3, 6, 9, ...), until a recorded No (`offer_declined`
with that key) stops it. At three the system offers and never applies:
making something standing is the human's act (constitution §0.3).

next-pile.md ("Forgetting is recorded like anything else"): a chunk the
record points at that is no longer where it was is tombstoned, never
silently dropped. NOTICE_AT and OFFER_AT are the operator's numbers (the
plan's 1/2/3); whether they are per box or per user is still OPEN there.
"""

from __future__ import annotations

import hashlib

from . import seed

NOTICE_AT = 2  # the operator's number (workflow.md §2c)
OFFER_AT = 3  # the operator's number (workflow.md §2c)
OFFER = "make it standing?"


# ── the ladder ─────────────────────────────────────────────────────────────
def bite_key(bite: str) -> str | None:
    """The bite's words, lowercased, in order, one space between, hashed.
    None for a bite with no words."""
    ws = seed.WORD.findall(bite.lower())
    if not ws:
        return None
    return hashlib.sha256(" ".join(ws).encode()).hexdigest()


def declined(rows: list[dict], key: str) -> bool:
    return any(r["kind"] == "offer_declined" and r.get("key") == key for r in rows)


def step(rows: list[dict], key: str) -> dict:
    """Where this ask sits on the ladder, from the rows before it: its count
    (this ask included) and what the system does. `offer` means an `offer` row
    is to be recorded now."""
    asked = 1 + sum(1 for r in rows if r["kind"] == "seed" and r.get("bite_key") == key)
    if asked < NOTICE_AT:
        does = "done"
    elif asked < OFFER_AT:
        does = "noticed"
    elif declined(rows, key):
        does = "declined"
    elif asked % OFFER_AT == 0:
        does = "offer"
    else:
        does = "noticed"
    return {"bite_key": key, "asked": asked, "ladder": does}


def open_offers(rows: list[dict]) -> list[dict]:
    """One per key: the latest `offer` row whose key has no recorded No."""
    latest: dict[str, dict] = {}
    for r in rows:
        if r["kind"] == "offer" and isinstance(r.get("key"), str):
            latest[r["key"]] = r
    return [
        {"key": k, "asked": r["asked"], "row": r["n"], "offer": OFFER}
        for k, r in sorted(latest.items(), key=lambda kv: kv[1]["n"])
        if not declined(rows, k)
    ]


def offer_line(o: dict) -> str:
    return f"offer · asked {o['asked']} times: {o['offer']} · key {o['key']}"


# ── tombstones ─────────────────────────────────────────────────────────────
# `pile.read_chunk`'s why, mapped to a fixed reason word. A why not here (no
# checkout, a git error) says the place can't be reached right now, not that
# the chunk is gone: no tombstone.
GONE = {
    "not in the index": "not_in_index",
    "the blob doesn't hash to the index": "hash_mismatch",  # or blob_gone, below
    "bytes don't hash to the index": "hash_mismatch",  # seed.Texts' wording
    "blob not in the checkout": "blob_gone",
    "the chunk isn't at that line with that hash": "not_at_line",
    "not utf-8": "not_utf8",
}
REASONS = frozenset(GONE.values()) | {"blob_gone"}


def reason(why: str | None, blob_there=None) -> str | None:
    """The fixed reason word for a failed read, or None when the failure is not
    the chunk being gone. `blob_there()` tells a missing blob from one that no
    longer hashes to the index (None when it can't say)."""
    r = GONE.get(why or "")
    if r == "hash_mismatch" and blob_there is not None and blob_there() is False:
        return "blob_gone"
    return r


def tombstoned(rows: list[dict], sha: str) -> bool:
    """Whether this check-in already tombstoned the chunk: once per check-in."""
    return any(
        r["kind"] == "tombstone" and r.get("chunk") == sha
        for r in seed.since_boot(rows)
    )


def forward(chunk: dict, places, read) -> list | None:
    """Another place the same chunk SHA-256 sits now, read back to prove it:
    `places` yields (repo, path, line, sha); `read(chunk) -> (text, why)`."""
    here = (chunk.get("repo"), chunk.get("path"), chunk.get("line"))
    for repo, path, line, sha in places:
        if sha != chunk.get("sha256") or (repo, path, line) == here:
            continue
        loc = {"repo": repo, "path": path, "line": line, "sha256": sha}
        if read(loc)[0] is not None:
            return [repo, path, line]
    return None
