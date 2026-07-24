"""
semantic.py — Loki's semantic eyes.
b17: LOKI3

Mistletoe, from loki/SPEC.md:

    "An agent describes a thing that already exists on disk... The architects
     keep designing what has already been built."

The original ``Accumulator.check_mistletoe`` is a pure string-membership check:
it fires only when a spec's *repo directory name* is absent from the catalog.
It never reads the spec's CONTENT, so it cannot catch the real blind spot — a
brand-new spec, in a brand-new (or existing) repo, that DESCRIBES a capability
the fleet already built under a different name.

This module gives Loki content-level sight by wiring an
:class:`~nestor.entity.EntityResolver` (Nestor's verified-match engine) behind
an :class:`ExistenceIndex`. Known things (catalog apps, repos, willow_compose
pieces, fleet capabilities) are *sealed* as ``surface -> canonical`` mappings;
a new spec's summary is then fuzzy-resolved against that sealed memory. A match
at/above the seal threshold means: *this already exists.*

Dependencies: stdlib + nestor only. Importing this module pulls no network and
no Postgres — the sealed memory lives in an injected
:class:`~nestor.sqlite_store.SqliteStore` (a loki-local temp DB by default).
"""
from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from nestor.entity import EntityResolver
from nestor.sqlite_store import SqliteStore

# Generic filler words that carry no capability signal. Dropping them keeps the
# token overlap focused on the words that actually name a thing.
_STOPWORDS = {
    "a", "an", "the", "for", "to", "of", "and", "with", "on", "in", "or", "is",
    "that", "this", "it", "its", "native", "new", "add", "build", "make",
    "create", "primitive", "component", "using", "via", "into", "over", "as",
    "at", "by", "be", "we", "i", "our", "my",
}


class _TokenMatcher:
    """A word-overlap matcher for the Nestor :class:`~nestor.matcher.Matcher` seam.

    Nestor's default :class:`~nestor.matcher.StringMatcher` scores raw *character*
    difflib similarity — right for translation strings, but it drowns semantic
    overlap in descriptor noise ("build a shared entity graph for the ledger
    pair" scores only ~0.58 against "shared entity graph component clusters edges
    resolve aliases"). For "does this spec re-describe an existing thing?" the
    signal is *shared capability words*, so this matcher:

      * ``normalize`` -> lowercased, de-punctuated, stop-word-stripped, de-duped
        token string (the canonical key Nestor persists as ``source_norm``).
      * ``similarity`` -> fuzzy token overlap. Each query token greedily claims
        its best surface token (exact, or difflib ratio >= ``tau`` to catch
        ``watcher~watch``); the accumulated overlap is scored as the max of a
        symmetric Dice coefficient and a shared-token count boost, so a spec
        that shares two or more distinctive capability terms with a sealed thing
        clears a 0.60 seal threshold, while an unrelated spec scores ~0.

    Stdlib only (``difflib`` + ``re``); satisfies the Matcher Protocol
    (``normalize`` / ``similarity``).
    """

    tau = 0.8

    def _tokens(self, value) -> list[str]:
        text = re.sub(r"[^\w\s]", " ", str(value).lower())
        out: list[str] = []
        for tok in text.split():
            if tok and tok not in _STOPWORDS and tok not in out:
                out.append(tok)
        return out

    def normalize(self, value) -> str:
        return " ".join(self._tokens(value))

    def similarity(self, a_norm: str, b_norm: str) -> float:
        qs = a_norm.split()
        ss = b_norm.split()
        if not qs or not ss:
            return 0.0
        if a_norm == b_norm:
            return 1.0
        used = [False] * len(ss)
        overlap = 0.0
        for q in qs:
            best = 0.0
            best_i = -1
            for i, s in enumerate(ss):
                if used[i]:
                    continue
                r = 1.0 if q == s else difflib.SequenceMatcher(None, q, s).ratio()
                if r > best:
                    best = r
                    best_i = i
            if best >= self.tau and best_i >= 0:
                used[best_i] = True
                overlap += best
        dice = 2 * overlap / (len(qs) + len(ss))
        # Shared-token boost: two solid capability-word matches clear 0.60.
        boost = overlap / 3.0
        return round(min(1.0, max(dice, boost)), 3)


class ExistenceIndex:
    """Nestor-backed "does this already exist?" index.

    Wraps an :class:`~nestor.entity.EntityResolver` over an injected store,
    matched by :class:`_TokenMatcher`. Seal known things (apps, repos,
    capabilities, willow_compose pieces) with :meth:`seal_known` /
    :meth:`build_from_catalog` / :meth:`build_from_pieces`, then ask
    :meth:`resolve_existing` whether a chunk of prose describes one of them.

    ``seal_threshold`` (default ``0.60``) is the fuzzy similarity at/above which
    a sealed match is served as a confident "this exists." ``context_threshold``
    is pushed to ``0.0`` so the seal threshold alone gates the answer (nothing is
    pre-dropped by Nestor's context floor before the seal check).
    """

    def __init__(self, store: Optional[SqliteStore] = None,
                 seal_threshold: float = 0.60) -> None:
        if store is None:
            # A loki-local temp DB so sealing never writes into the repo.
            db_dir = Path(tempfile.mkdtemp(prefix="loki-existence-"))
            store = SqliteStore(str(db_dir / "existence.db"))
        self.store = store
        self.resolver = EntityResolver(
            store,
            domain="existence",
            matcher=_TokenMatcher(),
            seal_threshold=seal_threshold,
            context_threshold=0.0,
        )

    # -- sealing ----------------------------------------------------------

    def seal_known(self, surface: str, canonical: str, provenance: dict) -> dict:
        """Register a known thing (an app, repo, or capability).

        ``surface`` is the describable text, ``canonical`` the stable id it
        resolves to. ``provenance`` (a dict) is stored via the resolver's
        ``origin`` field and echoed back on a match.
        """
        origin = _encode_provenance(provenance)
        return self.resolver.seal(surface, canonical, origin=origin)

    def build_from_catalog(self, catalog_path: str) -> int:
        """Seal every app in a SAFE App Store catalog.json.

        For each app: ``surface = f"{id} {name} {description}"``,
        ``canonical = id``, ``provenance = {"source": "catalog", "id": id}``.
        Returns the number of apps sealed.
        """
        import json

        data = json.loads(Path(catalog_path).read_text())
        count = 0
        for app in data.get("apps", []):
            app_id = app.get("id", "")
            if not app_id:
                continue
            surface = f"{app_id} {app.get('name', '')} {app.get('description', '')}".strip()
            self.seal_known(surface, app_id,
                            {"source": "catalog", "id": app_id})
            count += 1
        return count

    def build_from_pieces(self, pieces: Iterable[dict]) -> int:
        """Seal willow_compose pieces — DOCUMENTED SEAM ONLY.

        The real source of truth for pieces is the ``willow_compose.pieces``
        Postgres table. Wiring live Postgres here would violate this module's
        stdlib+nestor / no-network contract, so this method deliberately does
        NOT connect to any database. It accepts an already-materialized iterable
        of piece dicts (as a host would hand it after querying
        ``willow_compose.pieces``) and seals each one:

            surface   = f"{label} {ref}"   (label/ref describe the piece)
            canonical = piece_key           (the stable piece identity)
            provenance= {"source": "willow_compose.pieces", "piece_key": ...}

        Each dict should carry ``piece_key`` (or ``key``) and, ideally, ``label``
        and ``ref``. Returns the number of pieces sealed.
        """
        count = 0
        for piece in pieces:
            piece_key = piece.get("piece_key") or piece.get("key")
            if not piece_key:
                continue
            label = piece.get("label", "")
            ref = piece.get("ref", "")
            surface = f"{label} {ref}".strip() or str(piece_key)
            self.seal_known(surface, piece_key,
                            {"source": "willow_compose.pieces",
                             "piece_key": piece_key})
            count += 1
        return count

    # -- resolving --------------------------------------------------------

    def resolve_existing(self, text: str) -> dict:
        """Ask whether ``text`` describes a thing already sealed in the index.

        Returns::

            {"exists": bool,           # True iff a sealed match >= threshold
             "canonical": str | None,  # the matched thing's id
             "confidence": float,      # fuzzy similarity of the match
             "provenance": dict}       # provenance recorded at seal time

        ``exists`` is ``True`` exactly when Nestor serves a *sealed* match at or
        above the seal threshold; otherwise ``exists`` is ``False`` and
        ``canonical`` is ``None`` (any sub-threshold suggestion is carried in
        ``provenance`` for a human to inspect, but is NOT treated as existence).
        """
        res = self.resolver.resolve(text)
        if res.get("sealed"):
            provenance = _decode_provenance(res.get("provenance", {}).get("origin", ""))
            return {
                "exists": True,
                "canonical": res["canonical"],
                "confidence": res["confidence"],
                "provenance": provenance,
            }
        return {
            "exists": False,
            "canonical": None,
            "confidence": res.get("confidence", 0.0),
            "provenance": res.get("provenance", {}),
        }


# -- provenance encoding ---------------------------------------------------
#
# The EntityResolver persists a single ``origin`` string per sealed pair and
# echoes it back on resolve. We JSON-encode the provenance dict into that field
# so a match can carry its full structured source (catalog id, piece_key, ...).

def _encode_provenance(provenance: dict) -> str:
    import json

    try:
        return json.dumps(provenance, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(provenance)


def _decode_provenance(origin: str) -> dict:
    import json

    if not origin:
        return {}
    try:
        val = json.loads(origin)
        return val if isinstance(val, dict) else {"origin": origin}
    except (TypeError, ValueError):
        return {"origin": origin}


# -- spec summarization ----------------------------------------------------

def spec_summary(path: str, max_chars: int = 600) -> str:
    """Extract a spec's title + opening prose for :meth:`ExistenceIndex.resolve_existing`.

    Reads the file and returns the first markdown ``# H1`` heading followed by
    the first block of non-empty prose lines — skipping metadata/status lines
    (``b17:``, ``Status:``) and code fences. Bounded to ``max_chars`` so the
    summary stays small and network-free. Returns ``""`` if the file can't be
    read.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    title = ""
    prose: list[str] = []
    in_fence = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            # A blank line after we've collected prose ends the first paragraph.
            if prose and not stripped:
                break
            continue
        if stripped.startswith("# ") and not title:
            title = stripped.lstrip("# ").strip()
            continue
        # Skip lower-level headings and known metadata lines.
        if stripped.startswith("#"):
            continue
        low = stripped.lower()
        if low.startswith("b17:") or low.startswith("status:"):
            continue
        prose.append(stripped)
        if sum(len(p) for p in prose) >= max_chars:
            break

    summary = " ".join([title, *prose]).strip()
    return summary[:max_chars]
