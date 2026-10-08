"""Declared wagers, reconciled against what a human sealed. The script never
grades: no outcome means escalate to the human, never to a model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, record, reverse  # noqa: E402
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
    return Run(tmp_path / "box", KEYS, {}, clock(), anchor=tmp_path / "anchor.json")


def seal(run, w, outcome):
    return run.seal(
        w["hash"], gate.sign(HUMAN_KEY, "seal", w["hash"]), HUMAN_KEY, outcome
    )


def anchor(run):
    """The human seals the tip: every row so far is now covered."""
    tip = record.tip_subject(run.rec.tip())
    return run.seal_tip(gate.sign(HUMAN_KEY, "seal", tip), HUMAN_KEY)


def recon(run, key=HUMAN_KEY):
    """Reconcile the way check-out does: through what the anchor provably covers."""
    return reverse.reconcile_wagers(
        run.rec.rows(), reverse.covered_through(run.rec, key)
    )


def test_unsealed_wager_escalates_to_human(run):
    run.wager("P1", DIST)
    anchor(run)
    out = recon(run)
    assert out["escalate"] == ["P1"] and out["escalate_to"] == "human"
    row = out["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"
    assert row["graded_by"] is None


def test_sealed_path_index_reconciles_band(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 1})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "reconciled"
    assert row["predicted_band"] == "middle"
    assert row["predicted_p"] == 0.25
    assert row["actual_path"] == "The pile after the session"
    assert row["graded_by"] is None


def test_substring_match_is_case_insensitive(run):
    w = run.wager("P2", DIST)
    seal(run, w, {"path": "PILE after"})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "reconciled" and row["path_index"] == 1


def test_outcome_naming_no_path_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path": "nothing like it"})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"


def test_wager_without_scores_escalates_even_when_sealed(run):
    w = run.wager("P3")
    seal(run, w, {"winner": "desk"})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "escalate" and row["escalate_to"] == "human"


def test_wager_with_scores_reconciles_winner(run):
    w = run.wager("P3", scores={"desk": 2, "operator": 3})
    seal(run, w, {"winner": "operator"})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "reconciled" and row["winner"] == "operator"
    assert row["graded_by"] is None


def test_a_non_seal_row_is_not_a_seal(run):
    w = run.wager("P1", DIST)
    hum = gate.human(w["hash"], gate.sign(HUMAN_KEY, "seal", w["hash"]), HUMAN_KEY)
    run.rec.append(  # the human's hand, same subject and outcome, but not a seal
        "note", hum, subject=w["hash"], outcome={"path_index": 1}
    )
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "escalate"


def test_a_seal_row_not_stamped_by_the_human_is_not_a_seal(run):
    w = run.wager("P1", DIST)
    run.rec.append("seal", run.sys, subject=w["hash"], outcome={"path_index": 1})
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "escalate"


def test_a_seal_over_another_subject_does_not_count(run):
    run.wager("P1", DIST)
    other = run.wager("P2", DIST)
    seal(run, other, {"path_index": 0})
    anchor(run)
    by_id = {r["id"]: r["state"] for r in recon(run)["rows"]}
    assert by_id == {"P1": "escalate", "P2": "reconciled"}


def test_reconcile_carries_wagers_beside_untouched_predictions(run, tmp_path):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    rep = reverse.reconcile(
        run.rec.box,
        run.rec.pile(),
        run.rec.rows(),
        run.version,
        predictions=[{"x": 1}],
        wagers_through=reverse.covered_through(run.rec, HUMAN_KEY),
    )
    assert rep["predictions"] == [{"x": 1}]
    assert rep["wagers"]["reconciled"] == ["P1"]


# ── seal-binding: the outcome counts only under the human's sealed anchor tip ──


def test_outcome_sealed_but_anchor_does_not_cover_it_escalates(run):
    a = run.wager("P1", DIST)
    seal(run, a, {"path_index": 0})
    anchor(run)  # covers P1's seal
    b = run.wager("P2", DIST)
    seal(run, b, {"path_index": 1})  # sealed after the anchor: not covered
    by_id = {r["id"]: r for r in recon(run)["rows"]}
    assert by_id["P1"]["state"] == "reconciled"
    assert by_id["P2"]["state"] == "escalate"
    assert by_id["P2"]["escalate_to"] == "human" and by_id["P2"]["graded_by"] is None


def test_outcome_with_no_anchor_at_all_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    assert reverse.covered_through(run.rec, HUMAN_KEY) is None
    assert recon(run)["rows"][0]["state"] == "escalate"


def test_outcome_covered_by_the_anchor_reconciles(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    out = recon(run)
    assert out["reconciled"] == ["P1"] and out["escalate"] == []


def _edit_first_outcome(run):
    path = run.rec.path
    path.write_text(path.read_text().replace('"path_index":0', '"path_index":1'))


def test_outcome_altered_after_sealing_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    _edit_first_outcome(run)  # same bytes length; hash chain now broken
    assert run.rec.verify_chain()
    assert recon(run)["rows"][0]["state"] == "escalate"


def test_outcome_altered_and_chain_rehashed_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    rows = run.rec.rows()
    prev = record.GENESIS
    lines = []
    for r in rows:
        if r["kind"] == "seal" and "outcome" in r:
            r["outcome"] = {"path_index": 1}
        r["prev"] = prev
        r["hash"] = record.h256(
            record.canon({k: v for k, v in r.items() if k != "hash"})
        )
        prev = r["hash"]
        lines.append(record.canon(r))
    run.rec.path.write_text("\n".join(lines) + "\n")
    assert run.rec.verify_chain() == []  # the forgery is internally consistent
    assert run.rec.verify_anchor(HUMAN_KEY)  # but not the row the human sealed
    assert recon(run)["rows"][0]["state"] == "escalate"


def test_no_human_key_cannot_verify_coverage_and_escalates(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    assert recon(run, key=None)["rows"][0]["state"] == "escalate"
    assert recon(run)["rows"][0]["state"] == "reconciled"  # control: key present


def test_a_forged_anchor_proof_covers_nothing(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    assert recon(run, key=b"not-the-human-key")["rows"][0]["state"] == "escalate"


def test_checkout_threads_the_human_key(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    assert run.checkout()[0]["wagers"]["escalate"] == ["P1"]
    assert run.checkout(human_key=HUMAN_KEY)[0]["wagers"]["reconciled"] == ["P1"]
