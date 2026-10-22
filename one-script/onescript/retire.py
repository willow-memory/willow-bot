"""retire — propose dropping served rows, gated on a human seal.

escalate's deterministic cousin. Where escalate hands a piece to a local model,
retire needs none: a caller's ``select`` reads the served scope and names, by
served id, which rows are stale and why. Code turns each into a proposal that
cites that served id and runs it through ``Run.turn``'s door exactly as a
model's rows go. Nothing is written here; each proposal stands unsealed, so the
turn is 42 while any stands, and a later apply drops only what a human sealed.
The one free thing stays the seal.

``select`` is injected the way escalate injects its rung: the rule for *which*
rows are stale (an envelope superseded by a later grant, a duplicate pile) lives
with the caller that knows the rows' meaning. This module only turns a named,
reasoned candidate into a seal-gated proposal, and refuses a candidate whose
cite is not in the served scope — a drop must name a row the human can see.

Deterministic in every part but the seal: same served scope and same ``select``
give the same proposals every run (no clock, no model); the person's seal is the
only free thing, as in the-smallest-script.
"""

from __future__ import annotations

from typing import Callable, Iterable

from . import proposals, serve
from .states import POPULATED

#: The keys a ``select`` candidate must carry: the served id it would drop, the
#: reason a person reads on the card, and the detail written into the proposal.
CANDIDATE_KEYS = ("cite", "reason", "detail")

#: ``(served document) -> iterable of candidate dicts``. Injected; this module
#: never decides on its own which rows are stale.
Selector = Callable[[dict], Iterable[dict]]


def proposal_row(cite: str, reason: str, detail: str) -> dict:
    """One candidate as the exact ``{path, data, cites, claim}`` the door reads:
    a plain relative path under ``retire/``, the reason as the claim, the detail
    as the bytes, and the single served id as the only cite. Raises when the
    contract would refuse the row rather than hand the door something malformed."""
    row = {
        "path": f"retire/{cite}",
        "data": detail,
        "cites": [cite],
        "claim": reason,
    }
    checked, why = proposals.check(row)
    if checked is None:
        raise ValueError(f"retire built a row the contract refuses: {why}")
    return row


def _candidate(c: object) -> dict:
    """One ``select`` result, validated. A candidate read as data, never trusted
    to be well-formed."""
    if not isinstance(c, dict):
        raise ValueError("a retire candidate must be an object")
    missing = [k for k in CANDIDATE_KEYS if k not in c]
    if missing:
        raise ValueError(f"a retire candidate lacks {missing}")
    cite, reason, detail = c["cite"], c["reason"], c["detail"]
    if not (isinstance(cite, str) and cite):
        raise ValueError("a retire candidate's cite must be a served id")
    if not (isinstance(reason, str) and reason.strip()):
        raise ValueError("a retire candidate must give its reason")
    if not isinstance(detail, str):
        raise ValueError("a retire candidate's detail must be text")
    return {"cite": cite, "reason": reason, "detail": detail}


def propose(run, ident: dict, *, select: Selector, bite: str = "retire") -> dict:
    """Read the served scope, let ``select`` name the rows to drop, and propose
    dropping each through the door.

    Returns ``{state, why, candidates, proposals, out}``. ``proposals`` is the
    judged rows — 42 while any stands — and is empty when the scope is not
    populated or ``select`` named nothing. Like escalate's "only along side",
    nothing is proposed unless the served scope is populated: an empty or
    unreachable scope drops nothing and says so."""
    doc = run.served_file()
    state = doc.get("state") if isinstance(doc, dict) else None
    base: dict = {"state": state, "candidates": [], "proposals": [], "out": None}
    if not (isinstance(doc, dict) and state == POPULATED and doc.get("tables")):
        return {
            **base,
            "why": "nothing is served as populated; retire proposes nothing",
        }

    cands = [_candidate(c) for c in select(doc)]
    lost = serve.check_cites(doc, [c["cite"] for c in cands])
    if lost:
        names = ", ".join(sorted({x["cite"] for x in lost}))
        raise ValueError(
            f"a retire candidate cites a row not in the served scope: {names}"
        )

    rows = [proposal_row(c["cite"], c["reason"], c["detail"]) for c in cands]
    out = run.turn(ident, bite, proposed=rows) if rows else {"proposals": []}
    return {
        **base,
        "why": "",
        "candidates": cands,
        "proposals": out.get("proposals", []),
        "out": out,
    }
