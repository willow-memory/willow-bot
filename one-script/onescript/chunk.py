"""chunk.py -- a repo chunk as the model reads it: its keyed id, its trust
label, its place and its text, scrubbed. Moved verbatim out of `serve.py`,
which re-exports every name, so `prompt` serves its chunks the same way
`serve` does without loading the scope, the lore or the rules behind it.
"""

from __future__ import annotations

from . import gate, scrub
from .states import UNREACHABLE


WITHHELD = "[hash withheld]"


def _marker(kind):
    return WITHHELD if kind == "hash64" else f"[REDACTED:{kind}]"


def _scrub(value):
    """No full-width hash reaches the model, wherever it sits. Since flatten-2
    F12 the rule is ``scrub.py``'s: the hash first, as it always was (its
    marker is still ``WITHHELD``), then the credential shapes, which are added
    coverage (a key in a record's text reaches the model no more than a hash)."""
    if isinstance(value, str):
        return scrub.apply(value, ("hash64", "credentials"), marker=_marker)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def chunk_name(table: dict) -> str:
    c = table["source"]["chunk"]
    return f"{c['repo']}:{c['path']}:{c['line']}"


def served_id(serve_key: bytes, tid: str) -> str:
    return gate.sign(serve_key, "served", tid)


#: What a scope served with no seal says of itself (`Config.seal_at_end`).
UNSEALED = "unsealed: this session"


def _chunk_entry(t: dict, tid: str, serve_key: bytes, seals, read_chunk) -> dict:
    """A repo chunk as the model reads it: its place and its text, re-read at
    the indexed blob now. A chunk is someone's file, not a sealed row, so it is
    `untrusted` unless its own id carries the human's seal."""
    where = chunk_name(t)
    entry = {
        "id": served_id(serve_key, tid),
        "trust": "human-sealed" if tid in seals else "untrusted",
        "group": _scrub({"where": where}),
        "cannot_hold": [],
        "rows": [],
    }
    if read_chunk is None:
        text, why = None, "no reader for repo chunks in this serve"
    else:
        text, why = read_chunk(t["source"]["chunk"])
    if text is None:
        entry["state"], entry["why"] = UNREACHABLE, f"{where}: {why}"
    else:
        entry["rows"] = [
            {"what": "chunk", "where": _scrub(where), "text": _scrub(text)}
        ]
    return entry
