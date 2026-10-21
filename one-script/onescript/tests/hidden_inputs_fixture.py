"""The F17 fixture: what the one script computes, printed as ASCII canon.

Run as ``python -m onescript.tests.hidden_inputs_fixture``. `test_hidden_inputs`
runs it in several processes that differ only in what the process was never
told to depend on (``PYTHONHASHSEED``, ``LC_ALL``/``LANG``, ``PYTHONUTF8``,
the filesystem encoding), and asserts the output is byte-identical.

Each section is one place a hidden input could reach a persisted byte: the
canon encodings (key order, Unicode, floats), a record's rows and hashes, the
pile's words / bite key / forms / ranking / store, the stream's draws, and
calibration. Nothing here prints a path, a clock or a commit id.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from onescript import calibration, canon, ladder, pile, seed, xref
from onescript.record import Record, Verified, h256, version_of
from onescript.stream import Stream, StreamRandom
from onescript.tests.test_xref import make

BITE = "Nobody counts the ad breaks in kids games, not even once today. Café ÉCOLE"
DOCS = {
    "ads.md": (
        "# Ad breaks\n\n"
        "Nobody counts the ad breaks in kids games, not even once today.\n\n"
        "Four minutes of ads for thirty seconds of play is the trade here?\n"
    ),
    "other.md": "A note about gardens and soil, with nothing about games in it.\n",
    "more.md": "The ad breaks again, said plainly in a body line of this file.\n"
    "Café, école, and the gardens of cafés: games and gaming.\n",
}
NFC, NFD = "café", "café"  # one word, two Unicode forms
VALUE = {
    "z": [1, 2.5, 0.1 + 0.2, 1e22, -0.0, True, None],
    "é": NFC,
    "é": NFD,
    "日本": {"b": 1, "a": {"y": 2, "x": 3}},
    "line": "a b\u0085c d",
    "a": "",
}


def _reversed(d: dict) -> dict:
    """The same mapping, built in the opposite insertion order."""
    return {
        k: _reversed(v) if isinstance(v, dict) else v for k, v in reversed(d.items())
    }


def section_canon() -> dict:
    out = {}
    for name, enc in sorted(canon.ENCODINGS.items()):
        for label, value in (("fwd", VALUE), ("rev", _reversed(VALUE))):
            try:
                out[f"{name}.{label}"] = enc(value)
            except (ValueError, TypeError, UnicodeError) as e:
                out[f"{name}.{label}"] = f"refused:{type(e).__name__}"
    return out


def section_record() -> dict:
    token = object()
    who = Verified("willow", "self-declared", token)
    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "box"
        rec = Record(box, lambda: "2026-01-01T00:00:00Z", "v-fixed", token)
        for i, text in enumerate(("plain", NFC, NFD, "日本 x")):
            rec.append("note", who, text=text, n_in=i, tags=sorted({"b", "a", text}))
        raw = (box / "record.jsonl").read_bytes()
        return {
            "bytes_sha": hashlib.sha256(raw).hexdigest(),
            "hashes": [r["hash"] for r in rec.rows()],
            "chain": rec.verify_chain(),
            "h256": h256(NFC),
            "version_of": version_of(Path(canon.__file__).parent),
        }


def section_words() -> dict:
    w = seed.words(BITE)
    return {
        "words": w,
        "key": ladder.bite_key(BITE),
        "key_nfd": ladder.bite_key(BITE.replace("é", NFD[-2:])),
        "wanted": [sorted(x) for x in pile.wanted(BITE)],
        "forms": {x: sorted(pile.forms(x)) for x in w},
        "edits": pile.edits1("games")[:50],
    }


def section_pile() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = make(Path(tmp), "docs", DOCS)
        idx = xref.build([("docs", repo)])
        ranked = pile.rank(idx, {"docs": repo}, BITE)
        db_path = Path(tmp) / "c" / "p.sqlite"
        db, _ = pile.open_cache(idx, {"docs": repo}, db_path, "sha-1", bytes(range(32)))
        dump = "\n".join(db.iterdump())
        cached = pile.Cached(db, bytes(range(32))).rank(BITE)
        db.close()
        return {
            "rank": canon.sorted_compact_ascii(ranked),
            "cached": canon.sorted_compact_ascii(cached),
            "store_sha": hashlib.sha256(dump.encode()).hexdigest(),
        }


def section_stream() -> dict:
    s = Stream.from_seed(42, b"q")
    r = StreamRandom.from_seed("00ff" * 8, b"label")
    deck = list(range(30))
    r.shuffle(deck)
    return {
        "u32": [s.u32() for _ in range(8)],
        "die": [s.die(6) for _ in range(8)],
        "shuffle": s.shuffle(list(range(20))),
        "floats": [repr(r.random()) for _ in range(6)],
        "deck": deck,
        "mark": r.mark(),
    }


def section_calibration() -> dict:
    pairs = [(0.5 + i / 100, i % 3 != 0) for i in range(50)] + [(1.0, True)]
    return {
        "brier": [repr(calibration.brier(c, o)) for c, o in pairs],
        "log": [repr(calibration.log_score(c, o)) for c, o in pairs],
        "bins": [{k: repr(v) for k, v in b.items()} for b in calibration.bins(pairs)],
    }


SECTIONS = {
    "canon": section_canon,
    "record": section_record,
    "words": section_words,
    "pile": section_pile,
    "stream": section_stream,
    "calibration": section_calibration,
}


def run() -> str:
    return canon.sorted_compact_ascii({k: f() for k, f in SECTIONS.items()})


if __name__ == "__main__":
    sys.stdout.write(run() + "\n")
