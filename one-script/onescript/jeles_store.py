"""jeles_store — Jeles's verified corpus, the pile's escalation rung.

The operator, 2026-10-19: Jeles's nuggets are served into the SOIL store "for
the session to use", and Jeles is asked "escalation only": the pile answers a
bite first, and Jeles is asked only when the pile doesn't hold the bite's
subject. That is: there is no pile to ask (no index, no store), the pile ranks
nothing, or the pile is missing at least `ESCALATE_MISSED` of the bite's words.
Ranking alone can't say it: BM25 ranks chunks on any shared word, so "what is a
light-year" ranks every chunk holding "is" and "a" while the pile holds neither
"light" nor "year".

Jeles is a store like the pile, not a new way in. Its hits go into the seed
row's `rank` as chunks (`repo` "jeles", `path` "ask_jeles_corpus/<id>", `line`
1, `chunk` the SHA-256 of the card's text), so scope proposes them, the human
seals them with the rest of the stack, and serve re-reads each one here and
serves it only while it still hashes to what was sealed. Nothing about trust
changes: a Jeles chunk is `untrusted` until its id is under the human's seal,
exactly like a repo chunk. Whose record a card rests on is said inside its text
("verified — public record: NASA, ...").

Only what Jeles itself lets answer is offered: a nugget whose
`verification_kind` is `human` or `machine` and whose `visibility` is `serve`
or `public` (Jeles PR #98). Asserted research never reaches a stack.

| state | when |
|---|---|
| ranked | at least one servable nugget for the bite |
| empty | Jeles answered and nothing servable ranked |
| unreachable | `jeles` isn't installed, its store won't open, or the read failed |

Read only: Jeles's store is never written from here. `jeles` is optional; this
module imports it only when a store is made (`default_store`), so the one
script runs the same without it.
"""

from __future__ import annotations

import hashlib
from typing import Callable

REPO = "jeles"
PATH_PREFIX = "ask_jeles_corpus/"
ANSWERING = ("human", "machine")
SERVED = ("serve", "public")
#: Ask Jeles when the pile ranks fewer chunks than this for the bite.
#: Baseline (D12): 1, so a pile that ranks nothing always escalates.
ESCALATE_BELOW = 1
#: Ask Jeles when the pile is missing at least this share of the bite's words
#: (seed's `missed` over `missed` + `keys`). Baseline (D12): half.
ESCALATE_MISSED = 0.5
#: The most Jeles hits one seed row keeps.
TOP = 4
#: How many nuggets to read for each one kept, since some are filtered out.
OVERFETCH = 3

RANKED, EMPTY, UNREACHABLE = "ranked", "empty", "unreachable"
_LABEL = {"human": "verified", "machine": "corroborated"}


def card_text(n: dict) -> str:
    """The text a nugget is served as, and the bytes its chunk hash covers:
    the question, the answer, who it rests on, and where to read more."""
    kind = n.get("verification_kind") or "human"
    lines = [
        (n.get("question") or "").strip(),
        "",
        (n.get("answer") or "").strip(),
        "",
        f"{_LABEL.get(kind, kind)}: {(n.get('verified_by') or '').strip()}",
    ]
    sources = [str(s) for s in (n.get("sources") or []) if str(s).strip()]
    if sources:
        lines.append("sources: " + " ".join(sources))
    return "\n".join(lines)


def servable(n: dict) -> tuple[bool, str]:
    """Whether Jeles lets this nugget answer a served session, and if not why."""
    kind = n.get("verification_kind") or "human"
    if kind not in ANSWERING:
        return False, f"not settled in Jeles ({kind})"
    vis = n.get("visibility") or "internal"
    if vis not in SERVED:
        return False, f"not served by Jeles ({vis})"
    if not (n.get("question") or "").strip() or not (n.get("answer") or "").strip():
        return False, "no question or answer"
    return True, ""


def chunk_of(n: dict, score: float) -> dict:
    """A nugget as a seed row's ranked chunk."""
    return {
        "repo": REPO,
        "path": PATH_PREFIX + str(n.get("_id") or ""),
        "line": 1,
        "chunk": hashlib.sha256(card_text(n).encode()).hexdigest(),
        "score": score,
    }


class JelesStore:
    """Jeles, shaped as the pile's store for a seed (`rank`, `quotes`) and as
    a chunk reader for serve (`read_chunk`).

    `search(bite, limit) -> [nugget]` and `get(id) -> nugget` are Jeles's
    `corpus.search_nuggets` and `corpus.get_nugget` (`default_store`), or
    stand-ins in a test. A nugget is the record Jeles stores, with its `_id`.
    """

    name = REPO

    def __init__(self, search: Callable, get: Callable, *, top: int = TOP):
        self._search = search
        self._get = get
        self.top = top

    def rank(self, bite: str) -> dict:
        try:
            found = list(self._search(bite, self.top * OVERFETCH) or [])
        except Exception as e:  # noqa: BLE001 — a failed read is a state
            return {"state": UNREACHABLE, "why": _err(e), "hits": []}
        keep = [n for n in found if servable(n)[0] and n.get("_id")][: self.top]
        if not keep:
            return {"state": EMPTY, "why": "nothing servable ranked", "hits": []}
        # Jeles's own order, best first; not BM25, so not comparable to a pile score.
        hits = [chunk_of(n, round(1.0 / (i + 1), 4)) for i, n in enumerate(keep)]
        return {"state": RANKED, "why": "", "hits": hits}

    def quotes(self, bite: str) -> dict:
        """Jeles holds no words of the person's to quote back."""
        return {"state": RANKED, "why": "", "quotes": []}

    def read_chunk(self, chunk: dict) -> tuple[str | None, str | None]:
        """(text, None) while the nugget still hashes to the sealed chunk and
        Jeles still lets it answer; (None, why) otherwise."""
        path = str(chunk.get("path") or "")
        if chunk.get("repo") != REPO or not path.startswith(PATH_PREFIX):
            return None, "not a Jeles chunk"
        nid = path[len(PATH_PREFIX) :]
        try:
            n = self._get(nid)
        except Exception as e:  # noqa: BLE001
            return None, _err(e)
        if not isinstance(n, dict) or n.get("error"):
            return None, "no longer in Jeles"
        ok, why = servable(n)
        if not ok:
            return None, why
        text = card_text(n)
        if hashlib.sha256(text.encode()).hexdigest() != chunk.get("sha256"):
            return None, "changed in Jeles since it was sealed"
        return text, None


def default_store() -> JelesStore | None:
    """Jeles's own corpus, or None when `jeles` isn't installed. Whether its
    store opens is learned at the first read, and said as `unreachable`."""
    try:
        from jeles import corpus  # noqa: PLC0415 — optional, and only here
    except ImportError:
        return None
    return JelesStore(corpus.search_nuggets, corpus.get_nugget)


def should_escalate(out: dict) -> tuple[bool, str]:
    """Whether a seed's pile result sends the bite on to Jeles, and why."""
    store = (out.get("store") or {}).get("state")
    if out.get("state") == UNREACHABLE or store != "attached":
        return True, "no pile to ask"
    if len(out.get("rank") or []) < ESCALATE_BELOW:
        return True, "the pile ranked nothing"
    missed = len(out.get("missed") or [])
    held = len(out.get("keys") or [])
    if missed and missed / (missed + held) >= ESCALATE_MISSED:
        return True, f"the pile is missing {missed} of the bite's {missed + held} words"
    return False, "the pile holds the bite's words"


def escalate(store: JelesStore | None, bite: str, out: dict) -> dict | None:
    """The seed row's `jeles` entry, putting Jeles's hits ahead of the pile's in
    `out["rank"]` when the pile doesn't hold the bite. None when Jeles isn't configured: the row is then
    exactly what it was before this rung existed."""
    if store is None:
        return None
    go, why = should_escalate(out)
    if not go:
        return {"state": "not_asked", "why": why}
    r = store.rank(bite)
    if r["state"] == RANKED:
        # The bite escalated because the pile doesn't hold its subject, so
        # Jeles's hits lead and the pile's follow; scope draws from the top.
        out["rank"] = r["hits"] + list(out.get("rank") or [])
    return {"state": r["state"], "why": r.get("why") or why, "asked": why}


def chunk_reader(store: JelesStore | None, pile_reader: Callable | None) -> Callable:
    """One `read_chunk` for serve: Jeles's chunks through Jeles, every other
    chunk through the pile's reader (or why there is none)."""

    def read(chunk: dict):
        if chunk.get("repo") == REPO:
            if store is None:
                return None, "Jeles isn't configured in this run"
            return store.read_chunk(chunk)
        if pile_reader is None:
            return None, "no xref index to read chunks by"
        return pile_reader(chunk)

    return read


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:80]}"
