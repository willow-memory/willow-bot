"""The Nestor ledger and side-runner readers split on "\n" only (gap 6d5a986ce99f).

str.splitlines() also cuts at U+2028 / U+2029 / U+0085, which a JSON row can
hold raw when its writer used ensure_ascii=False; one row then reads as two
unparseable fragments.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2]
SEPS = [" ", " ", "\u0085"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dt(tmp_path, monkeypatch):
    mod = _load("deep_thought_under_test", HERE / "deep_thought.py")
    monkeypatch.setattr(mod, "BOX", tmp_path)
    return mod


def _ledger(box: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (box / "nestor.db.ledger.jsonl").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("sep", SEPS)
def test_measure_ledger_keeps_a_row_holding_a_separator(dt, tmp_path, sep):
    _ledger(
        tmp_path,
        [{"kind": "passage", "state": "draft", "pair_id": "p1", "text": f"a{sep}b"}],
    )
    _, quiet, choices = dt.measure_ledger()
    assert "unparseable" not in quiet[0]["why"]
    assert quiet[0]["subject"] == "Nestor ledger: 1 rows"
    assert choices[0]["subject"] == "offered (drafts): 1"


def test_measure_ledger_empty_file_stays_empty(dt, tmp_path):
    (tmp_path / "nestor.db.ledger.jsonl").write_text("", encoding="utf-8")
    _, quiet, _ = dt.measure_ledger()
    assert quiet[0]["verdict"] == "empty"


@pytest.mark.parametrize("sep", SEPS)
def test_measure_pairs_sees_a_seal_row_holding_a_separator(dt, tmp_path, sep):
    pair = dt.SEALS[0]["pair"]
    _ledger(tmp_path, [{"kind": "seal", "pair_id": pair, "note": f"x{sep}y"}])
    held, *_ = dt.measure_pairs()
    assert any("ledger sealed" in r["subject"] for r in held)


@pytest.mark.parametrize("sep", SEPS)
def test_side_runner_read_record_keeps_a_row_holding_a_separator(tmp_path, sep):
    sr = _load("side_runner_under_test", HERE / "scripts" / "flow" / "side_runner.py")
    rec = tmp_path / "rec.jsonl"
    rec.write_text(
        json.dumps({"t": f"a{sep}b"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    assert sr.read_record(rec) == [{"t": f"a{sep}b"}]
