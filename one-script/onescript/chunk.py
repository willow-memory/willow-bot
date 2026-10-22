"""chunk.py -- a repo chunk as the model reads it: its keyed id, its trust
label, its place and its text, scrubbed. Moved verbatim out of `serve.py`,
which re-exports every name, so `prompt` serves its chunks the same way
`serve` does without loading the scope, the lore or the rules behind it.
"""

from __future__ import annotations

from . import scrub, sign
from .states import UNREACHABLE


WITHHELD = "[hash withheld]"

#: The highest pii rung a served chunk may carry to the model (IDEAS 119): a
#: case number (L2) passes; a phone, an email, a home path (L3), money and
#: birth (L4) and an SSN (L5) are withheld and named. The operator's number.
SERVE_CEILING = 2


def _marker(kind):
    return WITHHELD if kind == "hash64" else f"[REDACTED:{kind}]"


def _scrub(value):
    """No full-width hash reaches the model, wherever it sits. Since flatten-2
    F12 the rule is ``scrub.py``'s: the hash first, as it always was (its
    marker is still ``WITHHELD``), then the credential shapes, which are added
    coverage (a key in a record's text reaches the model no more than a hash),
    and a password a person typed, by its label; then a person's data over
    `SERVE_CEILING`, withheld with its kind named."""
    if isinstance(value, str):
        out = scrub.apply(
            value, ("hash64", "credentials", "typed_password"), marker=_marker
        )
        return scrub.withhold(out, SERVE_CEILING)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def withhold_names(doc, names):
    """A served document with every name the person gave withheld, wherever it
    sits (``[withheld: name, L3]``): the last pass before the model reads it.
    Nothing is touched when no names are given."""
    if not names:
        return doc
    return scrub.walk_strings(
        doc, lambda s: scrub.withhold(s, SERVE_CEILING, names=names)
    )


def chunk_name(table: dict) -> str:
    c = table["source"]["chunk"]
    return f"{c['repo']}:{c['path']}:{c['line']}"


def served_id(serve_key: bytes, tid: str) -> str:
    return sign.hmac_parts(serve_key, "served", tid)  # gate.sign's bytes


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
