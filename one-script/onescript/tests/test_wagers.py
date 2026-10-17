"""Declared wagers, reconciled against what a human sealed. The script never
grades: no outcome means escalate to the human, never to a model."""

from __future__ import annotations

import math
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


def signed(w, outcome):
    """The human's proof over this wager's outcome: the outcome is signed."""
    return gate.sign(HUMAN_KEY, "seal", reverse.outcome_subject(w["hash"], outcome))


def seal(run, w, outcome):
    return run.seal(w["hash"], signed(w, outcome), HUMAN_KEY, outcome)


def anchor(run):
    """The human seals the tip: every row so far is now covered."""
    tip = record.tip_subject(run.rec.tip())
    return run.seal_tip(gate.sign(HUMAN_KEY, "seal", tip), HUMAN_KEY)


def recon(run, key=HUMAN_KEY):
    """Reconcile the way check-out does: through what the anchor provably
    covers, over the seals whose proofs re-check."""
    return reverse.reconcile_wagers(
        run.rec.rows(),
        reverse.covered_through(run.rec, key),
        run.held(human_key=key)[0],
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
    out = {"path_index": 1}
    sub = reverse.outcome_subject(w["hash"], out)
    hum = gate.human(sub, signed(w, out), HUMAN_KEY)
    run.rec.append(  # the human's hand, same subject and outcome, but not a seal
        "note", hum, subject=sub, wager=w["hash"], outcome=out, proof=signed(w, out)
    )
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "escalate"


def test_a_seal_row_not_stamped_by_the_human_is_not_a_seal(run):
    w = run.wager("P1", DIST)
    out = {"path_index": 1}
    run.rec.append(
        "seal",
        run.sys,
        subject=reverse.outcome_subject(w["hash"], out),
        wager=w["hash"],
        outcome=out,
        proof=signed(w, out),
    )
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
        held=run.held(human_key=HUMAN_KEY)[0],
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
    # no key handed over: the seal is on record but can't be checked here,
    # which is `unreachable`, never `escalate` (INVARIANTS §1, J1 re-audit)
    assert list(run.checkout()[0]["wagers"]["unreachable"]) == ["P1"]
    assert run.checkout(human_key=HUMAN_KEY)[0]["wagers"]["reconciled"] == ["P1"]


def forge(run, **fields):
    """A seal row by hand, stamped `who: human`, chained the way the record
    chains: what anyone with write access to the record can append."""
    import json

    rs = [json.loads(x) for x in run.rec.path.read_text().splitlines()]
    row = {
        "n": len(rs),
        "kind": "seal",
        "who": "human",
        "family": "human",
        "standing": "sealed",
        "version": rs[-1]["version"],
        "ts": "t",
        "prev": rs[-1]["hash"],
        "where": fields.get("subject"),
        **fields,
    }
    row["hash"] = record.h256(record.canon(row))
    with run.rec.path.open("a") as f:
        f.write(record.canon(row) + "\n")


def test_a_forged_outcome_with_no_proof_is_no_outcome_even_inside_the_tip(run):
    """The tip vouches that rows don't change after it, not that the human
    wrote them: a proofless outcome row slipped in before it is no seal."""
    w = run.wager("P1", DIST)
    out = {"path_index": 1}
    forge(
        run,
        subject=reverse.outcome_subject(w["hash"], out),
        wager=w["hash"],
        outcome=out,
    )
    anchor(run)
    assert recon(run)["rows"][0]["state"] == "escalate"


def test_a_copied_proof_cannot_carry_another_outcome(run):
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    real = run.rec.rows()[-1]
    forge(  # the human's real proof, a later row, another outcome
        run,
        subject=real["subject"],
        wager=w["hash"],
        outcome={"path_index": 1},
        proof=real["proof"],
    )
    anchor(run)
    row = recon(run)["rows"][0]
    assert row["state"] == "reconciled" and row["path_index"] == 0


def test_an_outcome_sealed_over_the_bare_wager_hash_is_no_outcome(run):
    """The old shape: the proof signs the wager's hash only, the outcome rides
    unsigned. It counts for nothing now."""
    w = run.wager("P1", DIST)
    proof = gate.sign(HUMAN_KEY, "seal", w["hash"])
    hum = gate.human(w["hash"], proof, HUMAN_KEY)
    run.rec.append(
        "seal",
        hum,
        where=w["hash"],
        subject=w["hash"],
        proof=proof,
        outcome={"path_index": 1},
    )
    anchor(run)
    assert recon(run)["rows"][0]["state"] == "escalate"


# ── the forecast lane (SPEC W1-forecast-lane, R3-R5): scored, never graded ──


def committed(run, wid, dist, outcome, by="desk"):
    """A forecast committed under a sealed tip, then its outcome sealed, then
    a tip over the outcome: `wager.n <= tip.n < tip_seal.n < outcome.n` (R3)."""
    w = run.wager(wid, dist, by=by, commit=reverse.COMMIT)
    anchor(run)
    seal(run, w, outcome)
    anchor(run)
    return w


def recon_key(run, key=HUMAN_KEY):
    return reverse.reconcile_wagers(
        run.rec.rows(),
        reverse.covered_through(run.rec, key),
        run.held(human_key=key)[0],
        key,
    )


def test_fold_keeps_brier_and_fills_bins(run):
    """C2 / R5: a 0.12 forecast scores the same folded and unfolded, and the
    bins hold it in [0.8, 0.9)."""
    committed(run, "f", [{"path": "yes", "p": 0.12}, {"path": "no", "p": 0.88}],
              {"path": "no"})  # fmt: skip
    rep = recon_key(run)
    row = rep["rows"][0]
    assert math.isclose(row["brier"], 0.12**2)
    cal = reverse.calibration_of(rep["rows"], run.rec.rows())
    assert math.isclose(cal["all"]["brier"], 0.12**2)
    hit = [b for b in cal["bins"] if b["n"]]
    assert [(b["lo"], b["hi"], b["n"], b["hit_rate"]) for b in hit] == [
        (0.8, 0.9, 1, 1.0)
    ]


def test_n_path_wagers_score_one_against_the_rest(run):
    dist = [{"path": "a", "p": 0.5}, {"path": "b", "p": 0.3}, {"path": "c", "p": 0.2}]
    committed(run, "n3", dist, {"path_index": 1})
    row = recon_key(run)["rows"][0]
    want = ((0.5 - 0) ** 2 + (0.3 - 1) ** 2 + (0.2 - 0) ** 2) / 3
    assert math.isclose(row["brier"], want)
    assert math.isclose(row["log_score"], -math.log(0.3))


def test_unsealed_outcome_has_no_score(run):
    run.wager("f", DIST_ONE, by="desk", commit=reverse.COMMIT)
    anchor(run)
    row = recon_key(run)["rows"][0]
    assert row["state"] == "escalate" and "brier" not in row


DIST_ONE = [{"path": "yes", "p": 0.6}, {"path": "no", "p": 0.4}]


def test_an_outcome_seal_ordered_before_its_wager_escalates(run):
    """Adversarial: the outcome seal's row number before the wager's. The
    chain can't be written that way honestly (the seal names the wager's
    hash), so the rows are put in that order by hand. Refused twice over:
    the labels are out of place (`reverse.in_place`, B1) and R3's order."""
    w = committed(run, "f", DIST_ONE, {"path_index": 0})
    rows = run.rec.rows()
    s = next(r for r in rows if r["kind"] == "seal" and r.get("wager"))
    swapped = [
        {**r, "n": s["n"]} if r is w or r["hash"] == w["hash"] else
        ({**r, "n": w["n"]} if r is s else r)
        for r in rows
    ]  # fmt: skip
    out = reverse.reconcile_wagers(
        swapped,
        reverse.covered_through(run.rec, HUMAN_KEY),
        run.held(human_key=HUMAN_KEY)[0],
        HUMAN_KEY,
    )
    assert out["rows"][0]["state"] == "escalate"
    assert recon_key(run)["rows"][0]["state"] == "reconciled"  # control


def test_a_forecast_needs_the_human_key_to_count_its_tip(run):
    committed(run, "f", DIST_ONE, {"path_index": 0})
    out = reverse.reconcile_wagers(
        run.rec.rows(),
        reverse.covered_through(run.rec, HUMAN_KEY),
        run.held(human_key=HUMAN_KEY)[0],
        None,  # the tip seals can't be re-checked row by row
    )
    assert out["rows"][0]["state"] == "escalate"


def test_an_older_wager_row_keeps_its_old_rule(run):
    """A wager row without `commit` is the shape from before the forecast
    lane: lined up as before, and never scored."""
    w = run.wager("P1", DIST)
    seal(run, w, {"path_index": 0})
    anchor(run)
    row = recon_key(run)["rows"][0]
    assert row["state"] == "reconciled" and "brier" not in row


CHAPTER_EIGHT = [  # willow-mcp docs/story/chapter-08-the-table.md:184-207
    ("dry", "dry", "rain, from the south"),
    ("stay", "stay", "leave, to the north"),
    ("fear", "fear", "the children were told a story"),
    ("seed", "the seed is still there", "the seed is gone"),
]


def test_read_misses_clusters_chapter_eight(run):
    """Four forecasts tagged `north`, one for four: three misses, one cluster
    (chapter 8: "Cluster detected.")."""
    for wid, said, other in CHAPTER_EIGHT:
        dist = [
            {"path": said, "p": 0.7, "tags": ["north"]},
            {"path": other, "p": 0.3, "tags": ["north"]},
        ]
        w = run.wager(wid, dist, by="desk", commit=reverse.COMMIT)
        anchor(run)
        seal(run, w, {"path_index": 0 if wid == "seed" else 1})
    anchor(run)
    rep = recon_key(run)
    got = reverse.read_misses(rep["rows"], run.rec.rows())
    assert [m["id"] for m in got["misses"]] == ["dry", "stay", "fear"]
    assert got["misses"][0]["forecast"] == "dry"
    assert got["misses"][0]["happened"] == "rain, from the south"
    assert got["clusters"] == [{"key": "tag:north", "ids": ["dry", "stay", "fear"]}]


def test_the_morning_screen_shows_scores_beside_the_humans_grade(run):
    committed(run, "f", DIST_ONE, {"path": "no"})
    rep, screen = run.checkout(human_key=HUMAN_KEY)
    assert rep["wagers"]["rows"][0]["graded_by"] is None
    line = next(x for x in screen.splitlines() if "wager f reconciled" in x)
    assert "brier 0.360 · log 0.92 · by desk (claimed)" in line
    assert "· tip sealed " in line and line.endswith("grade is yours")
    assert "calibration desk: n 1 · brier 0.36000" in screen
    assert "misses: 1, clusters: 0" in screen
