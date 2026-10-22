"""plant_rule.py -- the rules of planting the prompt hook reads (IDEAS 121).

Split from `plant.py` so the hook's path stays a leaf: which repos a session
may read on a planted box, and where the last handoff letter is. The verbs
that plant, consent and write the letter are `plant.py`'s. Stdlib only.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

PLANT, CONSENT = "plant", "consent"
LETTERS = "letters"


def _since_boot(rows: list[dict]) -> list[dict]:
    i = max((k for k, r in enumerate(rows) if r.get("kind") == "boot"), default=-1)
    return rows[i + 1 :]


def planted(rows: list[dict]) -> dict | None:
    """The box's ``plant`` row, or None: a box is planted once."""
    return next((r for r in rows if r.get("kind") == PLANT), None)


def consented(rows: list[dict]) -> dict | None:
    """This check-in's newest ``consent`` row, or None (consent expires with
    the session: an earlier check-in's says nothing about this one)."""
    return next(
        (r for r in reversed(_since_boot(rows)) if r.get("kind") == CONSENT), None
    )


def allowed_repos(rows: list[dict]) -> frozenset | None:
    """The repos this session may read chunks from: None when the box is not
    planted (no consent asked, as before planting existed); an empty set when
    it is planted and this session gave none."""
    if planted(rows) is None:
        return None
    c = consented(rows)
    return frozenset(c.get("streams", ())) if c else frozenset()


def allowed_from(rec) -> frozenset | None:
    """`allowed_repos` from a `Record` without parsing it whole: its tip-aware
    `has_kind` and `since_last` (the prompt hook's fast path on a sealed box)."""
    if not rec.has_kind(PLANT):
        return None
    _, session = rec.since_last("boot")
    c = next((r for r in reversed(session) if r.get("kind") == CONSENT), None)
    return frozenset(c.get("streams", ())) if c else frozenset()


def refusal(rows: list[dict], repos: Iterable[str]) -> str | None:
    """Why chunks from ``repos`` can't be served this session, or None."""
    return why_not(allowed_repos(rows), repos)


def why_not(allowed: frozenset | None, repos: Iterable[str]) -> str | None:
    """`refusal`, given the allowed set already read."""
    if allowed is None:
        return None
    if not allowed:
        return "this session has given no consent (run `consent --allow REPO`)"
    missing = sorted(set(repos) - allowed)
    if missing:
        return f"this session has no consent to read {', '.join(missing)}"
    return None


def latest_letter(box: Path) -> str | None:
    """The newest handoff letter's place in the box, or None."""
    names = sorted(p.name for p in (Path(box) / LETTERS).glob("*.md"))
    return f"{LETTERS}/{names[-1]}" if names else None
