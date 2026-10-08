"""Declared wagers, reconciled against what a human sealed. The script never
grades: no outcome means escalate to the human, never to a model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, reverse  # noqa: E402
from onescript.run import Run  # noqa: E402

KEYS = {"hanuman": b"k-hanuman"}
HUMAN_KEY = b"passkey-in-a-coat-pocket"
DIST = [
    {"path": "the close", "p": 0.55, "band": "likely"},
    {"path": "The pile after the session", "p": 0.25, "band": "middle"},
]


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-08T00:00:{next(n):06d}Z"


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path / "box", KEYS, {}, clock())


def seal(run, w, outcome):
    return run.seal(
        w["hash"], gate.sign(HUMAN_KEY, "seal", w["hash"]), HUMAN_KEY, outcome
    )


def test_unsealed_wager_escalates_to_human(run):
    run.wager("P1", DIST)
    out = reverse.reconcile_wagers(run.rec.rows())
    assert out["escalate"] == ["P1"] and out["escalate_to"] == "human"
    row = out["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"
    assert row["graded_by"] is None


def test_sealed_path_index_reconciles_band(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 1})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "reconciled"
    assert row["predicted_band"] == "middle"
    assert row["predicted_p"] == 0.25
    assert row["actual_path"] == "The pile after the session"
    assert row["graded_by"] is None


def test_substring_match_is_case_insensitive(run):
    w = run.wager("P2", DIST)
    seal(run, w, {"path": "PILE after"})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "reconciled" and row["path_index"] == 1


def test_outcome_naming_no_path_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path": "nothing like it"})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"


def test_wager_without_scores_escalates_even_when_sealed(run):
    w = run.wager("P3")
    seal(run, w, {"winner": "desk"})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"


def test_wager_with_scores_reconciles_winner(run):
    w = run.wager("P3", scores={"desk": 2, "operator": 3})
    seal(run, w, {"winner": "operator"})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "reconciled" and row["winner"] == "operator"
    assert row["graded_by"] is None


def test_a_non_seal_row_is_not_a_seal(run):
    w = run.wager("P1", DIST)
    hum = gate.human(w["hash"], gate.sign(HUMAN_KEY, "seal", w["hash"]), HUMAN_KEY)
    run.rec.append(  # the human's hand, same subject and outcome, but not a seal
        "note", hum, subject=w["hash"], outcome={"path_index": 1}
    )
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "escalate"


def test_a_seal_row_not_stamped_by_the_human_is_not_a_seal(run):
    w = run.wager("P1", DIST)
    run.rec.append("seal", run.sys, subject=w["hash"], outcome={"path_index": 1})
    row = reverse.reconcile_wagers(run.rec.rows())["rows"][0]
    assert row["state"] == "escalate"


def test_a_seal_over_another_subject_does_not_count(run):
    run.wager("P1", DIST)
    other = run.wager("P2", DIST)
    seal(run, other, {"path_index": 0})
    by_id = {
        r["id"]: r["state"] for r in reverse.reconcile_wagers(run.rec.rows())["rows"]
    }
    assert by_id == {"P1": "escalate", "P2": "reconciled"}


def test_reconcile_carries_wagers_beside_untouched_predictions(run, tmp_path):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    rep = reverse.reconcile(
        run.rec.box, run.rec.pile(), run.rec.rows(), run.version, predictions=[{"x": 1}]
    )
    assert rep["predictions"] == [{"x": 1}]
    assert rep["wagers"]["reconciled"] == ["P1"]
