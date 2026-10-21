"""box_lookup: the willow side of the Forge's `BoxLookup` seam, over the pile.

    "The real box (the corpus under ~/github) is willow-side; the default here
    returns nothing and SAYS so."                       (forge/entry.py, tier 2)

`PileBox` duck-types the Forge's `BoxLookup` (`name: str`, `lookup(hits) ->
list[Candidate]`) without importing the Forge: the Forge never imports willow,
and willow-bot does not require forge-play. A `Candidate` here is a local
dataclass with the same three fields (`name`, `where`, `why`).

`lookup(hits)` makes a bite of the hits' `source` keywords, ranks it against
one or more `pile.Cached` stores (merged by chunk hash, `pile.merge`, when
there are several), and returns the top chunks. Three states, never collapsed
(`last_state`, with `last_why`):

| state | when | `name` reads |
|---|---|---|
| populated | at least one chunk ranked | `willow pile` |
| empty | a store answered and nothing ranked, or no keyword to ask | `willow pile (empty: why)` |
| unreachable | no store, an empty store, or the read failed | `willow pile (unreachable: why)` |

The Forge prints `tiers["box"]` as `name: N candidate(s)` or `name: nothing`,
so `name` carries the state whenever nothing comes back. Read only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import pile as pile_mod

from .states import EMPTY, POPULATED, RANKED, UNREACHABLE  # the three, once (F5, F-18)


@dataclass(frozen=True)
class Candidate:
    """Shaped as `forge.entry.Candidate`: something the box already holds."""

    name: str
    where: str
    why: str = ""


def _named(stores) -> dict:
    """A `Cached`, a {name: Cached} dict, or an iterable of `Cached`."""
    if stores is None:
        return {}
    if isinstance(stores, dict):
        return dict(stores)
    if isinstance(stores, pile_mod.Cached) or hasattr(stores, "rank"):
        return {"store": stores}
    return {f"store{i}": c for i, c in enumerate(stores)}


class PileBox:
    """Tier 2 for the Forge, over this session's pile (and others', by hash).

    `stores` is what to rank against; `rank` is a callable `bite -> result`
    used instead (testable without a store), where the result is a rank dict
    (`{"state", "hits"}`) or a bare list of hits. A hit carries `repo`,
    `path`, `line`, and `score` (or `where`/`seen_in`, as `pile.merge` gives).
    """

    def __init__(
        self,
        stores=None,
        *,
        rank: Callable[[str], dict | list] | None = None,
        top: int = 5,
        label: str = "willow pile",
    ):
        self._stores = _named(stores)
        self._rank = rank
        self.top = top
        self.label = label
        self.last_state = UNREACHABLE
        self.last_why = "not asked yet"

    @property
    def name(self) -> str:
        if self.last_state == POPULATED:
            return self.label
        return f"{self.label} ({self.last_state}: {self.last_why})"

    def _set(self, state: str, why: str) -> None:
        self.last_state, self.last_why = state, why

    def _ranked(self, bite: str) -> tuple[str, list, str]:
        """(state, hits, why) from the callable or the stores."""
        if self._rank is not None:
            out = self._rank(bite)
            if isinstance(out, dict):
                state, hits = out.get("state", RANKED), list(out.get("hits") or [])
            else:
                state, hits = RANKED, list(out or [])
            if state == UNREACHABLE:
                return UNREACHABLE, [], "the ranker reports no pile"
            return state, hits, ""
        if not self._stores:
            return UNREACHABLE, [], "no store"
        if len(self._stores) == 1:
            ((name, c),) = self._stores.items()
            out = c.rank(bite)
            if out["state"] == UNREACHABLE:
                return UNREACHABLE, [], f"{name}: {out.get('why') or 'unreachable'}"
            return out["state"], out["hits"], ""
        out = pile_mod.merge(self._stores, bite)
        down = [n for n, s in out["sessions"].items() if s.get("state") == UNREACHABLE]
        if len(down) == len(self._stores):
            return UNREACHABLE, [], "no store could answer: " + ", ".join(down)
        why = f"unreachable: {', '.join(down)}" if down else ""
        return RANKED, out["hits"], why

    def lookup(self, hits) -> list[Candidate]:
        keywords = list(dict.fromkeys(h.source for h in hits if h.source))
        if not keywords:
            if not self._stores and self._rank is None:
                self._set(UNREACHABLE, "no store")
            else:
                self._set(EMPTY, "no keyword to look up")
            return []
        bite = " ".join(keywords)
        try:
            state, ranked, why = self._ranked(bite)
        except Exception as err:  # noqa: BLE001 — a failed read is a state
            self._set(UNREACHABLE, f"{type(err).__name__}: {str(err)[:80]}")
            return []
        if state == UNREACHABLE:
            self._set(UNREACHABLE, why)
            return []
        out = [self._candidate(h, keywords) for h in ranked[: self.top]]
        if not out:
            self._set(EMPTY, f"nothing ranked for {', '.join(keywords)}")
            return []
        self._set(POPULATED, why or f"{len(out)} of {len(ranked)} ranked")
        return out

    @staticmethod
    def _candidate(h: dict, keywords: list) -> Candidate:
        if "where" in h and "repo" not in h:  # a pile.merge hit
            repo, path, line = h["where"][0]
            tail = f"seen in {h.get('seen_in', 1)} store(s), fused by RRF"
        else:
            repo, path, line = h["repo"], h["path"], h["line"]
            tail = f"score {h['score']}" if "score" in h else "unscored"
        return Candidate(
            name=f"{repo}:{path}",
            where=f"{repo}:{path}:{line}",
            why=f"keywords {', '.join(keywords)}; {tail}",
        )
