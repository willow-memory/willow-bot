"""The F17 fixture: what the one script computes, printed as ASCII canon.

Run as ``python -m onescript.tests.hidden_inputs_fixture``. `test_hidden_inputs`
runs it in several processes that differ only in what the process was never
told to depend on (``PYTHONHASHSEED``, ``LC_ALL``/``LANG``, ``PYTHONUTF8``,
the filesystem encoding), and asserts the output is byte-identical.

Each section is one place a hidden input could reach a persisted byte: the
canon encodings (key order, Unicode, floats), a record's rows and hashes, the
pile's words / bite key / forms / ranking / store, the stream's draws, and
calibration; and, for each module the second flattening gave one home, a section
that names it: ``reach`` (``where``/``reach`` over a fixed env pointing at a tmp
tree), ``clock`` (the formats on a pinned ``now``), ``states`` (``state_of`` /
``base_of`` over fixed rows), ``words`` (``words`` / ``bite_key``, NFC and NFD
inputs), ``scrub`` (``apply`` over fixed samples) and ``sign`` (hmac, and ed25519
sign/verify with a fixed throwaway key). Nothing here prints a path, a clock or
a commit id.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from onescript import (
    calibration,
    canon,
    clock,
    ladder,
    pile,
    reach,
    scrub,
    seed,
    sign,
    states,
    words,
    xref,
)
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


def section_reach() -> dict:
    """``reach.where`` / ``reach.reach`` / ``reach.present`` over a tmp tree and a
    fixed env. Paths are printed relative to the tree, sentences with it masked."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        home, grove = t / "one-script", t / "grove"
        (home / "onescript").mkdir(parents=True)
        (home / "onescript" / "reach.py").write_text("")
        (grove / "governance").mkdir(parents=True)
        (grove / "governance" / "CONSTITUTION.md").write_text("law\n")
        nowhere, no_law = t / "nowhere", t / "no-law"

        def mask(x):
            if isinstance(x, Path):
                return str(x.relative_to(t))
            if isinstance(x, dict):
                return {k: mask(v) for k, v in sorted(x.items())}
            return x.replace(str(t), "<T>") if isinstance(x, str) else x

        saved = {k: os.environ.pop(k, None) for k in (reach.ENV_HOME, reach.ENV_GROVE)}
        try:
            beside = mask(reach.where(repos=t))  # no env: beside willow-bot, by name
            os.environ[reach.ENV_HOME] = str(home)
            os.environ[reach.ENV_GROVE] = str(grove)
            from_env = mask(reach.where())
            reachable = mask(reach.reach())
            os.environ[reach.ENV_HOME] = str(nowhere)
            gone = mask(reach.reach())
            os.environ[reach.ENV_HOME] = str(home)
            os.environ[reach.ENV_GROVE] = str(no_law)
            lawless = mask(reach.reach())
            lawless_ok = mask(reach.reach(law=False))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        return {
            "env": from_env,
            "reachable": reachable,
            "gone": gone,
            "no_law": lawless,
            "no_law_not_asked": lawless_ok,
            "present": [
                reach.present(home),
                reach.present(nowhere),
                reach.present(None),
            ],
            "beside": beside,
            "sentences": [mask(reach.no_onescript(None)), mask(reach.no_law(None))],
        }


PIN = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)


def section_clock() -> dict:
    """Every named format on a pinned ``now``, and the pin's readers."""
    pins = [
        "2026-10-20T12:00:00Z",
        "2026-10-20T14:00:00+02:00",
        "1789000000.5",
        "2026-10-20T12:00:00",
    ]
    refused = []
    for bad in ("tomorrow", "", "12:00"):
        try:
            clock.parse_pin(bad)
            refused.append("accepted")
        except clock.BadPin:
            refused.append("BadPin")
    return {
        "seconds_z": clock.seconds_z(PIN),
        "isoformat_utc": clock.isoformat_utc(PIN),
        "isoformat_seconds_utc": clock.isoformat_seconds_utc(PIN),
        "pin_str": clock.seconds_z("2026-10-20T14:00:00+02:00"),
        "pins": [clock.isoformat_utc(clock.parse_pin(x)) for x in pins],
        "refused": refused,
        "pinned_env": clock.seconds_z(
            clock.pinned({clock.ENV_NOW: "2026-10-20T12:00:00Z"})
        ),
        "unpinned_env": repr(clock.pinned({})),
        "naive_at": clock.seconds_z(PIN.replace(tzinfo=None)),
        "format": clock.SECONDS_Z,
    }


def section_states() -> dict:
    """``state_of`` over fixed rows (None is unreachable, never empty) and
    ``base_of`` / ``checked`` over fixed words."""
    rows = [None, [], (), [0], [{"a": 1}], {"k": 1}, {}, "", "x"]
    names = [
        states.RANKED,
        states.HELD,
        states.NONE,
        "populated",
        "empty",
        "unreachable",
        "loading",
        "",
        None,
        3,
    ]
    return {
        "state_of": [states.state_of(r) for r in rows],
        "base_of": [states.base_of(w) for w in names],
        "checked": [states.checked(w) for w in names],
        "states": list(states.STATES),
        "variants": sorted(states.VARIANTS.items()),
    }


def section_wordrules() -> dict:
    """The key rule, v1 and the NFC variant, over NFC and NFD inputs (the module
    is ``words``; ``section_words`` above drives the same rule through seed)."""
    texts = [
        BITE,
        BITE.replace("\u00e9", NFD[-2:]),
        "Cafe\u0301 CAFE\u00c9 caf\u00e9",
        "  --  ",
        "a_b c-d 42",
        "",
    ]
    return {
        "words": [words.words(t) for t in texts],
        "v1": [words.words_v1(t) for t in texts],
        "nfc": [words.words_nfc(t) for t in texts],
        "key": [words.bite_key(t) for t in texts],
        "key_nfc": [words.bite_key_nfc(t) for t in texts],
        "nfc_nfd_same_v1": words.words_v1(NFC) == words.words_v1(NFD),
        "nfc_nfd_same_nfc": words.words_nfc(NFC) == words.words_nfc(NFD),
        "pattern": words.WORD.pattern,
    }


SAMPLES = [
    "wrapAKIA" + "Q" * 16 + "wrap",
    "ghp_" + "a" * 36 + " and AIza" + "b" * 35 + "tail",
    "Authorization: Bearer abc.def.ghi\nnext line",
    "postgres://u:p@ss@host/db  DB_PASSWORD=hunter2  primary_key=True",
    "api_key = abcdefghijklmnop1234  gsk_" + "Z" * 24 + " csk-" + "Y" * 22,
    "seed: deadbeefdeadbeefdeadbeefdeadbeef  " + "f" * 64,
    "/home/x/.willow/vault/key",
    "plain words, nothing to find, caf\u00e9",
]


def section_scrub() -> dict:
    """``scrub.apply`` over fixed samples in every named set and in a fixed
    order, ``found`` printed sorted, and ``redact_url``."""
    out = {}
    for name in sorted(scrub.SETS):
        found: set = set()
        out[name] = [scrub.apply(x, (name,), found=found) for x in SAMPLES] + [
            sorted(found)
        ]
    found = set()
    out["all"] = [scrub.apply(x, tuple(scrub.SETS), found=found) for x in SAMPLES]
    out["all_found"] = sorted(found)
    out["url"] = [
        scrub.redact_url(u)
        for u in (
            "https://u:p@hooks.example.invalid:8443/Ab12Cd34Ef56Gh78Ij90Kl12/x?t=1#f",
            "http://[::1]:9000/webhook",
            "nope",
        )
    ]
    out["keys"] = [
        scrub.sensitive_key(k) for k in ("api_key", "filename", "Cookie", "dsn")
    ]
    return out


def section_sign() -> dict:
    """hmac, and ed25519 sign/verify with a fixed throwaway key (RFC 8032 test
    key 1). Without the ``cryptography`` package the ed25519 half says so, the
    same on every run."""
    key, msg = b"throwaway-hmac-key", b"the message"
    out = {
        "hmac": sign.hmac_hex(key, msg),
        "parts": sign.hmac_parts(key, "a", "\u00e9", "c"),
        "equal": [
            sign.hmac_equal("a", "a"),
            sign.hmac_equal("a", "b"),
            sign.hmac_equal("\u00e9", "\u00e9"),
            sign.hmac_equal(b"a", "a"),
        ],
        "seal_message": sign.seal_message("src", "target", "verifier").hex(),
        "pin": sign.PIN_ID,
        "ed25519": sign.have_ed25519(),
    }
    if sign.have_ed25519():
        priv = bytes.fromhex(
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
        )
        sig = sign.ed25519_sign(priv, msg)
        pub = sign.ed25519_public_hex(priv)
        out.update(
            {
                "pub": pub,
                "sig": sig,
                "verify": sign.ed25519_verify_hex(pub, msg, sig),
                "tampered": sign.ed25519_verify_hex(pub, msg + b"!", sig),
                "short_key": sign.ed25519_verify_hex("00" * 5, msg, sig),
            }
        )
    return out


SECTIONS = {
    "canon": section_canon,
    "record": section_record,
    "words": section_words,
    "pile": section_pile,
    "stream": section_stream,
    "calibration": section_calibration,
    "reach": section_reach,
    "clock": section_clock,
    "states": section_states,
    "wordrules": section_wordrules,
    "scrub": section_scrub,
    "sign": section_sign,
}


def run() -> str:
    return canon.sorted_compact_ascii({k: f() for k, f in SECTIONS.items()})


if __name__ == "__main__":
    sys.stdout.write(run() + "\n")
