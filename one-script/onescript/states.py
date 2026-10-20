"""states.py -- the three states, once (flatten F5, F-18).

Every reader in the fleet answers one of three, never collapsed
(willows-grove INVARIANTS §1): it found rows (``populated``), it looked and
there were none (``empty``), or it could not look (``unreachable``). Empty is
not unreachable: "no rows" and "no store" are different answers, and a
reader that says the first when the truth is the second has hidden a fault.

The one script, the-table and willows-grove's Grove import these; the
operator: "2 a".
"""

from __future__ import annotations

POPULATED, EMPTY, UNREACHABLE = "populated", "empty", "unreachable"
STATES = (POPULATED, EMPTY, UNREACHABLE)


def state_of(rows: object) -> str:
    """``populated`` or ``empty`` for rows a reader got back; None (the reader
    could not look) is ``unreachable``. A reader that failed passes None,
    never ``[]``."""
    if rows is None:
        return UNREACHABLE
    return POPULATED if len(rows) else EMPTY  # type: ignore[arg-type]


def checked(state: object) -> str:
    """``state`` if it is one of the three; anything else reads as
    ``unreachable``: a state nobody can name is not a found or an empty."""
    return state if state in STATES else UNREACHABLE  # type: ignore[return-value]
