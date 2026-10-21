"""deposits' row_hash bytes, pinned and held to the one script's family B
(flatten-2 F7).

``deposits._canonical`` is ``sorted_compact_ascii`` of the row without its
chain keys (`onescript/canon.py`). It keeps its own ``json.dumps``: willow_bot
ships as a wheel that carries no ``one-script/``, so a published install has no
one script to import, and an unreachable one script must not stop CI deposits.
This holds the two to the same bytes. D-e: bytes already persisted never change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from willow_bot import deposits

ONE = Path(__file__).resolve().parents[1] / "one-script"

NA = {"name": "héllo ☃ 日本", "k": "\U0001d11e", "e": ""}
NEST = {"z": {"y": [1, 2, {"b": None, "a": True}], "x": ""}, "a": [], "n": -0.5}
CASES = {
    "ascii": {"b": 1, "a": "x", "prev_hash": "p", "row_hash": "r"},
    "nonascii": dict(NA, row_hash="x"),
    "nested": NEST,
    "empty": {},
    "order": {"b": 1, "a": 2, "B": 3, "é": 4, "10": 5, "9": 6, "prev_hash": "z"},
}
CANONICAL = {
    "ascii": b'{"a":"x","b":1}',
    "nonascii": b'{"e":"","k":"\\ud834\\udd1e","name":"h\\u00e9llo \\u2603 \\u65e5\\u672c"}',
    "nested": b'{"a":[],"n":-0.5,"z":{"x":"","y":[1,2,{"a":true,"b":null}]}}',
    "empty": b"{}",
    "order": b'{"10":5,"9":6,"B":3,"a":2,"b":1,"\\u00e9":4}',
}
ROW_HASH = {
    "ascii": "a5b532d1cb99be914bfa55726b1ecf3104019c9293a02055f7ad098125ab1b2d",
    "nonascii": "2cb67a91617b4306e7202cdb2d6ce205c7470706590f7e6da722de9bcbaf938a",
    "nested": "ae294bcffe4389222579d267ab8f83b0b2866b0c147af2bb9c662e4f74861fc2",
    "empty": "60c228feecae04d231619927bfe4254f97d30008dcebe783e4db5a92e731ad92",
    "order": "df6dd666f859d8d69b617752ba084f866d0da44b04fdd68af5a2667c4c4cbced",
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_canonical_and_row_hash_are_pinned(case):
    assert deposits._canonical(CASES[case]) == CANONICAL[case]
    assert deposits.compute_row_hash(CASES[case], "0" * 64) == ROW_HASH[case]


@pytest.mark.parametrize("case", sorted(CASES))
def test_it_is_the_one_scripts_family_b(case):
    spec = importlib.util.spec_from_file_location(
        "_canon_for_deposits", ONE / "onescript" / "canon.py"
    )
    canon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = canon
    spec.loader.exec_module(canon)
    body = {k: v for k, v in CASES[case].items() if k not in deposits._CHAIN_KEYS}
    assert deposits._canonical(CASES[case]) == canon.sorted_compact_ascii.bytes(body)
