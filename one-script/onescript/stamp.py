"""stamp.py -- the record's write token, and the run's own stamp.

A row is written only by a `Verified` that carries this one object
(`record.Record.append`). It was `gate._TOKEN`; it lives here so a path that
writes rows but checks no agent (`opening`, the prompt hook's) needs neither
the gate nor what the gate imports. `gate` re-exports all three names, the
same objects, so who can mint a stamp is exactly as wide as before: any code
in the process that imports either module.
"""

from __future__ import annotations

from .record import Verified

_TOKEN = object()  # the only key that mints a Verified; the record checks for it


def token() -> object:
    return _TOKEN


def system() -> Verified:
    """The run itself, for the rows it writes about its own steps."""
    return Verified("run", "python", _TOKEN)
