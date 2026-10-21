"""Nestor's frozen encodings, held by parity, not repointed (flatten-2 LE7, D-e).

The operator: "Nestor's frozen encodings are held by parity tests, not
repointed." Nestor is an org repo and keeps its own bytes; the one script
mirrors them. Three pairs are held byte-for-byte to Nestor's own functions:

    onescript.sign.seal_message / nestor_seal.message  <->  nestor.signing._message
    onescript.session.canonical                         <->  nestor.session_seal.canonical
    onescript.canon.compact_utf8 / sorted_compact_utf8  <->  the two json.dumps shapes above (family E, A)

One pair is held as NOT equal on purpose: the one script's `words` (the bite
rule) against Nestor's `StringMatcher.normalize`. They answer different
questions; the named differences are pinned so a drift on either side shows.

Nestor is found as a sibling of willow-bot, or at $ONESCRIPT_NESTOR. Its
modules load by file path under a throwaway package name (no install, and
nestor/__init__ is not run). Not beside: skip locally, FAIL when CI is set.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import canon, nestor_seal, session, sign, words  # noqa: E402

ONE = Path(__file__).resolve().parents[2]  # willow-bot/one-script
_PKG = "_nestor_beside"


def _nestor_dir() -> Path | None:
    env = os.environ.get("ONESCRIPT_NESTOR")
    for root in ([Path(env)] if env else []) + [ONE.parents[1] / "nestor"]:
        if (root / "nestor" / "signing.py").is_file():
            return root / "nestor"
    return None


def _load(name: str):
    """One Nestor module, by file path, under a stub package so its relative
    imports (`from .errors import ...`) resolve without running its __init__."""
    here = _nestor_dir()
    if here is None:
        if os.environ.get("CI"):
            pytest.fail(
                "CI is set but Nestor is not beside willow-bot (or $ONESCRIPT_NESTOR)"
            )
        pytest.skip("Nestor is not beside willow-bot and ONESCRIPT_NESTOR is unset")
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(here)]
        sys.modules[_PKG] = pkg
    try:
        return importlib.import_module(f"{_PKG}.{name}")
    except ImportError as exc:  # a dependency Nestor needs is missing here
        if os.environ.get("CI"):
            pytest.fail(f"Nestor's {name} did not import: {exc}")
        pytest.skip(f"Nestor's {name} did not import (missing dependency): {exc}")


TEXTS = [
    "",
    "a",
    "approve this scope",
    "serve:" + "ab" * 32,
    'quote " backslash \\ slash / tab \t nl \n',
    "\x1f unit separator \x00 nul \x7f del",
    "café",  # composed
    "café",  # decomposed
    "naïve über ß",
    "中文 العربية हिन्दी",
    "ư ư",
    "emoji \U0001f600 \U0001f469‍\U0001f4bb \U0001f1fa\U0001f1f8 ❤️",
    "      ﻿",
    "\U0001d11e astral",
]


def _triples():
    for a in TEXTS:
        for b in (TEXTS[3], TEXTS[7], TEXTS[11]):
            for c in ("sean campbell", "rémi \U0001f469"):
                yield a, b, c


def test_seal_message_matches_nestor_signing():
    nestor = _load("signing")
    n = 0
    for a, b, c in _triples():
        want = nestor._message(a, b, c)
        assert sign.seal_message(a, b, c) == want, (a, b, c)
        assert nestor_seal.message(a, b, c) == want, (a, b, c)
        n += 1
    assert n > 50


def test_seal_message_known_bytes():
    # the encoding spelled out, so a drift on both sides at once still shows
    assert (
        sign.seal_message("aé", "\U0001f600", "v") == '["aé","\U0001f600","v"]'.encode()
    )


def test_swapped_fields_and_delimiter_shifts_stay_distinct_like_nestors():
    nestor = _load("signing")
    one = ("ok\x1fadmin", "alice")
    two = ("ok", "admin\x1falice")
    assert nestor._message("n", *one) != nestor._message("n", *two)
    assert sign.seal_message("n", *one) != sign.seal_message("n", *two)


BUNDLES = [
    {},
    {"a": 1},
    {"b": [1, 2, {"z": None, "a": True}], "a": "café"},
    {"é": "\U0001f600", "é": "decomposed", "z": 1.5},
    {"actions": [{"kind": "pin", "text": "中文"}], "origin": " "},
    {"k": 'q"\\\n\t', "n": {"y": {"x": [None, False]}}},
]


def test_session_canonical_matches_nestor_session_seal():
    nestor = _load("session_seal")
    for bundle in BUNDLES:
        want = nestor.canonical(bundle)
        assert session.canonical(bundle) == want, bundle
        assert canon.sorted_compact_utf8.bytes(bundle) == want, bundle


def test_session_subject_is_over_the_same_bytes():
    nestor = _load("session_seal")
    for bundle in BUNDLES:
        data = nestor.canonical(bundle)
        assert nestor.subject(data).endswith(hashlib.sha256(data).hexdigest())


def test_canon_family_e_matches_nestors_positional_array():
    nestor = _load("signing")
    for a, b, c in _triples():
        # family E on any positional array equals Nestor's message for the same three
        assert canon.compact_utf8.bytes([a, b, c]) == nestor._message(a, b, c)


def test_canon_family_e_other_shapes_match_json_dumps_compact_utf8():
    import json

    for v in (["x"], [], [1, [2, ["\U0001f600"]]], ["a", None, True, 3]):
        want = json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert canon.compact_utf8.bytes(v) == want


# ---- held as NOT equal, on purpose --------------------------------------------


def _words_key(text: str) -> str:
    return " ".join(words.words(text))


def test_words_and_nestor_normalize_differ_on_purpose():
    """The bite rule (`words`) is not Nestor's `StringMatcher.normalize`.

    Each is frozen for its own bytes: `words` keys a bite (v1: lowercase, then
    letter-or-digit runs, no normalization form); Nestor's keys a translation
    pair (NFC first, then keep word/space/emoji, collapse space). The named
    differences below are the known ones. If a pair here starts to agree, or a
    named one changes, one side drifted: look before editing the expectation.
    """
    normalize = _load("matcher").StringMatcher().normalize

    # agree on plain ASCII words
    assert (
        _words_key("Hello World 42") == normalize("Hello World 42") == "hello world 42"
    )

    # 1. NFC: Nestor folds composed/decomposed together; `words` does not.
    composed, decomposed = "café", "café"
    assert normalize(composed) == normalize(decomposed) == "café"
    assert _words_key(composed) == "café"
    assert _words_key(decomposed) == "cafe"  # the mark is not a letter: v1 drops it

    # 2. Marks: a combining mark that survives NFC (no composed form) is kept by
    #    Nestor (it is \w), but splits a word in `words`.
    lone = "ư"  # u + horn: composes to U+01B0 under NFC
    assert normalize(lone) == "ư"
    assert _words_key("á́b") != normalize("á́b")

    # 3. Apostrophes: Nestor strips them (one word); `words` splits there.
    assert normalize("don't") == "dont"
    assert words.words("don't") == ["don", "t"]
    assert normalize("it’s") == "its"
    assert words.words("it’s") == ["it", "s"]

    # 4. Underscore: `words` splits on it, Nestor keeps it (\w).
    assert words.words("x_y") == ["x", "y"]
    assert normalize("x_y") == "x_y"

    # 5. Emoji: Nestor keeps symbol emoji as keys; `words` finds none.
    assert normalize("\U0001f600") == "\U0001f600"
    assert words.words("\U0001f600") == []
    assert words.bite_key("\U0001f600") is None
