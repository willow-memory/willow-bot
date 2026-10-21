"""The canonical-bytes encodings, pinned byte for byte (flatten-2 F7).

The expected strings below were taken from the ``json.dumps`` calls the copies
made BEFORE they were repointed here (D-e: bytes already persisted never
change). They are literals on purpose: if one moves, a persisted chain moved.
This is not JCS; see ``onescript/canon.py``.
"""

from __future__ import annotations

import pytest

from onescript import canon

CASES = {
    "ascii_flat": {"b": 1, "a": "x"},
    "non_ascii": {"name": "héllo ☃ 日本", "k": "𝄞", "e": ""},
    "nested": {"z": {"y": [1, 2, {"b": None, "a": True}], "x": ""}, "a": [], "n": -0.5},
    "empty": {},
    "key_order": {"b": 1, "a": 2, "B": 3, "é": 4, "10": 5, "9": 6},
    "array": ["session_bind", "é☃", 3, None, []],
}

GOLDEN = {
    "sorted_compact_utf8": {
        "ascii_flat": '{"a":"x","b":1}',
        "non_ascii": '{"e":"","k":"𝄞","name":"héllo ☃ 日本"}',
        "nested": '{"a":[],"n":-0.5,"z":{"x":"","y":[1,2,{"a":true,"b":null}]}}',
        "empty": "{}",
        "key_order": '{"10":5,"9":6,"B":3,"a":2,"b":1,"é":4}',
        "array": '["session_bind","é☃",3,null,[]]',
    },
    "sorted_compact_ascii": {
        "ascii_flat": '{"a":"x","b":1}',
        "non_ascii": '{"e":"","k":"\\ud834\\udd1e","name":"h\\u00e9llo \\u2603 \\u65e5\\u672c"}',
        "nested": '{"a":[],"n":-0.5,"z":{"x":"","y":[1,2,{"a":true,"b":null}]}}',
        "empty": "{}",
        "key_order": '{"10":5,"9":6,"B":3,"a":2,"b":1,"\\u00e9":4}',
        "array": '["session_bind","\\u00e9\\u2603",3,null,[]]',
    },
    "sorted_compact_ascii_default_str": {
        "ascii_flat": '{"a":"x","b":1}',
        "non_ascii": '{"e":"","k":"\\ud834\\udd1e","name":"h\\u00e9llo \\u2603 \\u65e5\\u672c"}',
        "nested": '{"a":[],"n":-0.5,"z":{"x":"","y":[1,2,{"a":true,"b":null}]}}',
        "empty": "{}",
        "key_order": '{"10":5,"9":6,"B":3,"a":2,"b":1,"\\u00e9":4}',
        "array": '["session_bind","\\u00e9\\u2603",3,null,[]]',
    },
    "compact_utf8": {
        "ascii_flat": '{"b":1,"a":"x"}',
        "non_ascii": '{"name":"héllo ☃ 日本","k":"𝄞","e":""}',
        "nested": '{"z":{"y":[1,2,{"b":null,"a":true}],"x":""},"a":[],"n":-0.5}',
        "empty": "{}",
        "key_order": '{"b":1,"a":2,"B":3,"é":4,"10":5,"9":6}',
        "array": '["session_bind","é☃",3,null,[]]',
    },
    "compact_ascii": {
        "ascii_flat": '{"b":1,"a":"x"}',
        "non_ascii": '{"name":"h\\u00e9llo \\u2603 \\u65e5\\u672c","k":"\\ud834\\udd1e","e":""}',
        "nested": '{"z":{"y":[1,2,{"b":null,"a":true}],"x":""},"a":[],"n":-0.5}',
        "empty": "{}",
        "key_order": '{"b":1,"a":2,"B":3,"\\u00e9":4,"10":5,"9":6}',
        "array": '["session_bind","\\u00e9\\u2603",3,null,[]]',
    },
    "spaced_utf8": {
        "ascii_flat": '{"b": 1, "a": "x"}',
        "non_ascii": '{"name": "héllo ☃ 日本", "k": "𝄞", "e": ""}',
        "nested": '{"z": {"y": [1, 2, {"b": null, "a": true}], "x": ""}, "a": [], "n": -0.5}',
        "empty": "{}",
        "key_order": '{"b": 1, "a": 2, "B": 3, "é": 4, "10": 5, "9": 6}',
        "array": '["session_bind", "é☃", 3, null, []]',
    },
    "sorted_spaced_ascii": {
        "ascii_flat": '{"a": "x", "b": 1}',
        "non_ascii": '{"e": "", "k": "\\ud834\\udd1e", "name": "h\\u00e9llo \\u2603 \\u65e5\\u672c"}',
        "nested": '{"a": [], "n": -0.5, "z": {"x": "", "y": [1, 2, {"a": true, "b": null}]}}',
        "empty": "{}",
        "key_order": '{"10": 5, "9": 6, "B": 3, "a": 2, "b": 1, "\\u00e9": 4}',
        "array": '["session_bind", "\\u00e9\\u2603", 3, null, []]',
    },
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
@pytest.mark.parametrize("case", sorted(CASES))
def test_family_text_and_bytes_are_pinned(name, case):
    enc = canon.ENCODINGS[name]
    assert enc(CASES[case]) == GOLDEN[name][case]
    assert enc.bytes(CASES[case]) == GOLDEN[name][case].encode("utf-8")


def test_registry_names_and_versions_are_stable_and_unique():
    assert {e.version for e in canon.ENCODINGS.values()} == {
        "wgrv-canon-a/v1",
        "wgrv-canon-b/v1",
        "wgrv-canon-b-str/v1",
        "wgrv-canon-e/v1",
        "wgrv-canon-e-ascii/v1",
        "wgrv-canon-f/v1",
        "wgrv-canon-g/v1",
        "wgrv-canon-strict/v1",
    }
    assert len(canon.BY_VERSION) == len(canon.ENCODINGS)
    for e in canon.ENCODINGS.values():
        assert canon.get(e.name) is e and canon.get(e.version) is e
        assert e.persisted, f"{e.name} must say where its bytes are persisted"
    with pytest.raises(KeyError):
        canon.get("wgrv-canon-z/v1")


def test_a_and_b_differ_only_on_non_ascii():
    for case, v in CASES.items():
        a = canon.sorted_compact_utf8(v)
        b = canon.sorted_compact_ascii(v)
        assert a.isascii() is False or a == b, case
    assert canon.sorted_compact_utf8(CASES["non_ascii"]) != canon.sorted_compact_ascii(
        CASES["non_ascii"]
    )


def test_default_str_is_the_only_difference_of_b_str():
    from decimal import Decimal

    v = {"d": Decimal("1.5"), "a": 1}
    assert canon.sorted_compact_ascii_default_str(v) == '{"a":1,"d":"1.5"}'
    with pytest.raises(TypeError):
        canon.sorted_compact_ascii(v)


def test_the_loose_families_keep_what_the_copies_kept():
    """The copies let these through; changing it would move persisted bytes or
    start refusing rows already on disk (D-e). The strict entry is for new code."""
    nan = float("nan")
    assert canon.sorted_compact_utf8({"x": nan}) == '{"x":NaN}'
    assert canon.sorted_compact_utf8({1: "a"}) == '{"1":"a"}'
    # ... and the collision the strict entry exists to refuse (sorting a mixed
    # int/str key set raises, so the unsorted families show it)
    assert canon.compact_utf8({1: "a", "1": "b"}) == '{"1":"a","1":"b"}'
    lone = "\ud800"
    assert canon.sorted_compact_ascii({"k": lone}) == '{"k":"\\ud800"}'
    with pytest.raises(UnicodeEncodeError):
        canon.sorted_compact_utf8.bytes({"k": lone})


# -- the strict entry --------------------------------------------------------

S = canon.strict_sorted_compact_utf8


def test_strict_is_a_for_what_it_accepts():
    for case, v in CASES.items():
        if case == "nested":
            continue  # holds a float
        assert S(v) == canon.sorted_compact_utf8(v), case
        assert S.bytes(v) == canon.sorted_compact_utf8.bytes(v), case


@pytest.mark.parametrize(
    "bad",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": [float("-inf")]},
        {1: "a"},
        {"a": {2: "b"}},
        {"k": "\ud800"},
        {"\udfff": 1},
        {"f": 0.5},
        {"o": object()},
    ],
)
def test_strict_refuses(bad):
    with pytest.raises(canon.CanonError):
        S(bad)


def test_strict_floats_only_when_asked_but_never_nan():
    assert S({"f": 0.5}, allow_floats=True) == '{"f":0.5}'
    with pytest.raises(canon.CanonError):
        S({"f": float("nan")}, allow_floats=True)


def test_strict_loads_refuses_duplicate_keys_nan_and_lone_surrogates():
    assert S.loads('{"b":1,"a":[true,null,"é"]}') == {"b": 1, "a": [True, None, "é"]}
    assert S.loads(b'{"a":1}') == {"a": 1}
    for bad in (
        '{"a":1,"a":2}',
        '{"o":{"a":1,"a":1}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"k":"\\ud800"}',
        '{"x":1.5}',
        "{not json",
    ):
        with pytest.raises(canon.CanonError):
            S.loads(bad)
    assert S.loads('{"x":1.5}', allow_floats=True) == {"x": 1.5}


def test_strict_round_trips_its_own_bytes():
    v = {"b": [1, "é☃", None, True], "a": {"z": "", "y": 3}}
    assert S.loads(S(v)) == v
    assert S(S.loads(S(v))) == S(v)


# -- the one script's own copies, repointed here -----------------------------


@pytest.mark.parametrize("case", sorted(CASES))
def test_record_xref_and_session_bytes_are_family_a(case):
    """Taken from the three copies before they imported this (record.canon,
    xref.canon, session.canonical): A, byte for byte."""
    from onescript import record, session, xref

    want = GOLDEN["sorted_compact_utf8"][case]
    assert record.canon(CASES[case]) == want
    assert xref.canon(CASES[case]) == want
    assert session.canonical(CASES[case]) == want.encode("utf-8")


def test_nestor_seal_message_is_pinned_and_is_family_e():
    from onescript import nestor_seal

    assert (
        nestor_seal.message("src é", "tgt ☃", "ver")
        == b'["src \xc3\xa9","tgt \xe2\x98\x83","ver"]'
    )
    assert nestor_seal.message("", "", "") == b'["","",""]'
    assert nestor_seal.message("a", "b", "c") == canon.compact_utf8.bytes(
        ["a", "b", "c"]
    )


def test_foundation_probe_keeps_its_own_canon_and_agrees_with_a():
    """foundation/probe.py is stdlib-only and runs under bare interpreters to
    compare them; it reproduces the old hook's bytes on purpose and so keeps its
    own copy. This holds it to family A."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "foundation" / "probe.py"
    spec = importlib.util.spec_from_file_location("_probe_for_canon", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    for case, v in CASES.items():
        assert probe.canon(v) == GOLDEN["sorted_compact_utf8"][case], case
