"""The table's results as rows (J4) and the ai-game-master head (J9).

SPEC W2-rows-and-head, rulings R9, R10, R3; SPEC-documented §9 J4 (C10, C11)
and §12 J9; SPEC-rival §2 and adversarial tests T4, T5, T7. A `verdict` row
is code's record, written as the verified `table` identity, and never a seal.
Code proposes a forecast's outcome from verdict rows written after the
forecast's tip seal and never seals it. The agm_head check is equal /
differs / unreachable, and `unanchored` apart from all three.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, beacon, gate, record, reverse, table, view  # noqa: E402

HUMAN_KEY = b"a-throwaway-human-key-for-tests"
TABLE_KEY = "bb" * 32
CAP = 10**6
NOW = "2026-10-17T00:00:00Z"
#: W4 break 1: a drand round still ahead of the box's clock, and its randomness
ROUND = beacon.round_at(NOW) + 10
RANDOMNESS = "5e" * 32
SEED_ID = beacon.seed_id(RANDOMNESS)
BAND_TOTAL = {"strong": 10, "weak": 8, "miss": 4}


def _sheet() -> dict:
    """R23a: the modifier is the sheet's, never the roll's. One seat; stat
    `S<i><band>` holds the modifier that lands roll i in that band on
    RANDOMNESS (the stream's counter at roll i does not depend on modifiers)."""
    sheet, at = {}, 0
    for i in range(6):
        a, b, at = beacon.roll_2d6(RANDOMNESS, at)
        for v, total in BAND_TOTAL.items():
            sheet[f"S{i}{v}"] = total - (a + b)
    return sheet


#: six set-piece beats: the unit sessions pick their stats (R22 lets a pick
#: stand only on a set piece), so the sheet's S<i><band> stats are allowed
BEATS = [
    {
        "id": f"b{i}",
        "kind": "action",
        "suggests": f"S{i}weak",
        "scene": 0,
        "set_piece": True,
    }
    for i in range(6)
]
INPUTS = {"sheets": [_sheet()], "stats": [], "stat_map": {}, "beats": BEATS}
WORLD = {
    "world": "hasbeen-late-shift",
    "sheets_sha": record.h256(
        json.dumps(INPUTS, sort_keys=True, separators=(",", ":"))
    ),
}
FORECAST = {**WORLD, "beacon_round": ROUND}


class Drand:
    """The operator's beacon reader, offline: the rounds up to NOW are out;
    ROUND is ahead, served only once it has fired (`play` fires it, as the
    table plays only after the round is out)."""

    def __init__(self, fired: bool = False):
        self.fired = fired

    def __call__(self, rnd) -> dict:
        if rnd == "latest":
            return {"round": ROUND if self.fired else beacon.round_at(NOW)}
        if rnd != ROUND or not self.fired:
            raise LookupError(f"round {rnd} has not fired in this reader")
        return {"round": rnd, "randomness": RANDOMNESS}


drand = Drand(fired=True)  # a reader after the round, for direct calls


OTHER = {"world": "ironworks-last-pour", "sheets_sha": "cd" * 32}
DIST = [
    {"path": "strong-weak", "p": 0.4},
    {"path": "weak-weak", "p": 0.35},
    {"path": "miss-miss", "p": 0.25},
]


def _cfg(tmp_path, keys_obj):
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    keys = tmp_path / "keys" / "keys.json"
    keys.parent.mkdir(mode=0o700)
    keys.write_text(json.dumps(keys_obj))
    keys.chmod(0o600)
    return api.Config(
        box=tmp_path / "box",
        keys=keys,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now=NOW,
        no_tests=True,
        phone=True,
        beacon=Drand(),
    )


@pytest.fixture
def cfg(tmp_path):
    c = _cfg(
        tmp_path, {"desk": "aa" * 32, "table": TABLE_KEY, "human": HUMAN_KEY.hex()}
    )
    assert api.checkin(c)["code"] == 0
    return c


@pytest.fixture
def key_file(tmp_path):
    f = tmp_path / "human" / "key.hex"
    f.parent.mkdir(mode=0o700)
    f.write_text(HUMAN_KEY.hex() + "\n")
    f.chmod(0o600)
    return f


def rows(cfg) -> list[dict]:
    return [json.loads(x) for x in (cfg.box / "record.jsonl").read_text().splitlines()]


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def seal_tip(cfg, subject: str) -> dict:
    return api.seal_tip(cfg, subject, proof=proof(subject))


def tip_now(cfg) -> str:
    return record.tip_subject(rows(cfg)[-1])


def play(
    cfg,
    session="s1",
    verdicts=("strong", "weak"),
    forecast=None,
    close="decision",
    world=WORLD,
):
    """Write one session's verdict rows as the sink would: open, a roll per
    verdict with the path so far, then a decision (or result) row."""
    opened = {"what": "open", "session": session, "verdict": "open", "reason": "opened"}
    opened.update(world or {})
    opened.update(beacon_round=ROUND, randomness=RANDOMNESS, seed_id=SEED_ID)
    opened["world_inputs"] = INPUTS
    if forecast is not None and isinstance(cfg.beacon, Drand):
        cfg.beacon.fired = True  # a forecast's table plays once its round is out
    if forecast is not None:
        opened["forecast"] = forecast
    out = [table.verdict(cfg, opened)]
    path: list[str] = []
    at = 0
    for i, v in enumerate(verdicts):
        path.append(v)
        roll, at = honest_roll(at, v, i)
        out.append(
            table.verdict(
                cfg,
                {
                    "what": "roll",
                    "session": session,
                    "turn": i,
                    "seat": 0,
                    "character": "c0",
                    "move": ["act", "Grit"],
                    "total": roll["result"],
                    "verdict": v,
                    "reason": f"2d6+{roll['stat_value']} = {v}",
                    "roll": roll,
                    "path": "-".join(path),
                },
            )
        )
    if close:
        out.append(
            table.verdict(
                cfg,
                {
                    "what": close,
                    "session": session,
                    "verdict": close,
                    "reason": close,
                    "path": "-".join(path),
                },
            )
        )
    assert all(r["code"] == 0 for r in out), out
    return out


def honest_roll(at: int, verdict: str, i: int, randomness: str = RANDOMNESS):
    """Roll i as the stream really gives it at counter `at` (R23a replay), on
    stat `S<i><verdict>`, whose sheet modifier lands it in `verdict`'s band."""
    a, b, end = beacon.roll_2d6(randomness, at)
    stat = f"S{i}{verdict}"
    mod = INPUTS["sheets"][0][stat]
    roll = {
        "generator_id": beacon.GENERATOR_ID,
        "seed_id": beacon.seed_id(randomness),
        "stat": stat,
        "stat_value": mod,
        "counter_before": at,
        "counter": end,
        "result": a + b + mod,
    }
    return roll, end


def forecast_sealed(cfg, wid="f1"):
    f = api.forecast(cfg, wid, DIST, by="oracle", table=FORECAST)
    assert f["code"] == 0, f
    assert seal_tip(cfg, f["tip"])["sealed"]
    return f


def seal_kinds(cfg) -> list[int]:
    return [r["n"] for r in rows(cfg) if r["kind"] == "seal"]


# ── verdict: code's record, as the verified table identity (C11) ─────────────
def test_a_verdict_row_is_written_as_table_and_is_unattested(cfg):
    got = play(cfg)[1]["row"]
    assert (
        got["kind"] == "verdict" and got["who"] == "table" and got["family"] == "python"
    )
    assert got["standing"] == "unattested"
    assert got["path"] == "strong" and got["roll"]["counter"] == 2


def test_verdict_refuses_unverified_table(cfg):
    """SPEC-documented §9 (d): a verdict with a wrong `table` key is refused."""
    wrong = {
        "who": "table",
        "family": "python",
        "sig": gate.sign(b"x" * 32, *table.TABLE),
    }
    res = table.verdict(cfg, {"what": "open", "session": "s"}, identity=wrong)
    assert res["code"] == 1 and "unsigned agent" in res["refused"]
    desk = {
        "who": "desk",
        "family": "claude",
        "sig": gate.sign(b"\xaa" * 32, "desk", "claude"),
    }
    res = table.verdict(cfg, {"what": "open", "session": "s"}, identity=desk)
    assert res["code"] == 1 and "not 'desk'" in res["refused"]
    assert not any(r["kind"] == "verdict" for r in rows(cfg))


def test_a_box_with_no_table_key_writes_no_verdict(tmp_path):
    c = _cfg(tmp_path, {"desk": "aa" * 32, "human": HUMAN_KEY.hex()})
    assert api.checkin(c)["code"] == 0
    res = table.verdict(c, {"what": "open", "session": "s"})
    assert res["code"] == 1 and "no table key" in res["refused"]


def test_no_verdict_before_a_checkin(tmp_path):
    c = _cfg(
        tmp_path, {"desk": "aa" * 32, "table": TABLE_KEY, "human": HUMAN_KEY.hex()}
    )
    res = table.verdict(c, {"what": "open", "session": "s"})
    assert "refused" in res and not any(r["kind"] == "verdict" for r in rows(c))


@pytest.mark.parametrize(
    "row, why",
    [
        ({"kind": "seal", "what": "open", "session": "s"}, "never 'seal'"),
        ({"what": "open", "session": "s", "proof": "ab"}, "never a seal"),
        ({"what": "open", "session": "s", "subject": "tip:1:ab"}, "never a seal"),
        (
            {"what": "open", "session": "s", "wager": "ab", "outcome": {"path": "x"}},
            "never a seal",
        ),
        ({"what": "open", "session": "s", "via": "nestor", "pair": {}}, "never a seal"),
        ({"what": "open", "session": "s", "who": "human"}, "the stamp is the record's"),
        (
            {"what": "open", "session": "s", "standing": "sealed"},
            "the stamp is the record's",
        ),
        ({"what": "grade", "session": "s"}, "what is one of"),
        ({"what": "open"}, "names its session"),
    ],
)
def test_a_verdict_forged_as_a_seal_is_refused(cfg, row, why):
    """R10, the A5 class: a verdict row can't be kind seal, carry a proof or
    any field a seal reader reads, or set its own stamp."""
    res = table.verdict(cfg, row)
    assert res["code"] == 1 and why in res["refused"]
    assert not any(
        r["kind"] in ("verdict", "seal") and r["who"] == "table" for r in rows(cfg)
    )


def test_a_hand_written_verdict_carrying_a_real_outcome_proof_counts_for_nothing(
    cfg, key_file
):
    """A verdict row appended by hand with the human's real outcome proof on
    it, re-chained so the chain holds: it is not a seal row, so no outcome
    counts and nothing is reconciled."""
    f = forecast_sealed(cfg)
    outcome = {"path": "strong-weak"}
    sub = reverse.outcome_subject(f["subject"], outcome)
    rs = rows(cfg)
    forged = {
        "n": len(rs),
        "kind": "verdict",
        "who": "human",
        "family": "human",
        "standing": "sealed",
        "version": rs[-1]["version"],
        "ts": "t",
        "prev": rs[-1]["hash"],
        "where": sub,
        "subject": sub,
        "proof": proof(sub),
        "wager": f["subject"],
        "outcome": outcome,
    }
    forged["hash"] = record.h256(record.canon(forged))
    with (cfg.box / "record.jsonl").open("a") as fh:
        fh.write(record.canon(forged) + "\n")
    assert seal_tip(cfg, tip_now(cfg))["sealed"]
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "escalate" and "brier" not in w


def test_verdict_rows_are_served_once_scoped(cfg):
    """C11: rows by a verified `table` identity are served (a `run` row never
    is), with verdict, reason and turn in the served view."""
    play(cfg)
    sc = api.scope(cfg, by=("who",), match={"who": "table"})
    assert sc["code"] == 0, sc
    assert api.seal_scope(cfg, sc["subject"], proof=proof(sc["subject"]))["sealed"]
    doc = api.serve(cfg, sc["spec"], max_chars=CAP)
    assert doc["state"] == "populated", doc
    text = json.dumps(doc["doc"])
    for word in ('"verdict"', '"reason"', '"turn"', "strong", "2d6+1"):
        assert word in text


# ── propose_outcome: from verdict rows after the tip seal (R3), never sealed ─
def test_code_proposes_the_outcome_from_verdict_rows_and_never_seals(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    before = seal_kinds(cfg)
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "proposed" and got["outcome"] == {"path": "strong-weak"}
    assert got["subject"] == reverse.outcome_subject(
        f["subject"], {"path": "strong-weak"}
    )
    assert seal_kinds(cfg) == before  # proposed, nothing sealed
    assert rows(cfg)[-1]["kind"] == "invocation"  # read-only
    w = rows(cfg)[got["under"]["tip_seal"]]
    assert w["kind"] == "seal" and got["under"]["tip_seal"] < min(got["basis"])


def test_T4_a_verdict_row_before_the_forecast_escalates(cfg, key_file):
    """T4: the session's rows started before the forecast existed; the open row
    naming the forecast came later. wager.n < verdict.n is broken."""
    play(cfg, session="s1", verdicts=("strong",), close=None)
    f = forecast_sealed(cfg)
    play(cfg, session="s1", verdicts=("strong", "weak"), forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "T4" in got["reason"]


def test_T5_verdicts_with_no_sealed_tip_between_escalate(cfg, key_file):
    """T5: the forecast is on record, but the human sealed no tip between it
    and the verdict rows; the tip sealed after them holds both at once."""
    f = api.forecast(cfg, "f1", DIST, by="oracle", table=FORECAST)
    play(cfg, forecast=f["subject"])
    assert seal_tip(cfg, tip_now(cfg))["sealed"]
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "T5" in got["reason"]


def test_without_the_human_key_the_proposal_is_unreachable_not_escalate(cfg):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"])
    assert got["state"] == "unreachable"


def test_T7_cut_rows_with_no_anchor_after_them_are_said_not_prevented(cfg, key_file):
    """T7 / A7: cut the session's last rows after the last sealed tip. The
    chain still verifies and the anchor still holds (a cut there is not seen),
    but the session never closes, so code proposes nothing and says why."""
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    rs = rows(cfg)
    keep = [
        r for r in rs if not (r["kind"] == "verdict" and r.get("what") == "decision")
    ]
    cut = keep[: keep.index(next(r for r in keep if r.get("what") == "roll")) + 1]
    (cfg.box / "record.jsonl").write_text("".join(record.canon(r) + "\n" for r in cut))
    rec = record.Record(cfg.box, lambda: "", "", object(), cfg.keys.parent / api.ANCHOR)
    assert rec.verify_chain() == [] and rec.verify_anchor(HUMAN_KEY) == []
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert (
        got["state"] == "escalate"
        and "incomplete" in got["reason"]
        and "A7" in got["reason"]
    )


def test_T7_the_same_cut_under_a_sealed_tip_is_seen(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    assert seal_tip(cfg, tip_now(cfg))["sealed"]
    rs = rows(cfg)
    cut = rs[: next(r["n"] for r in rs if r.get("what") == "decision")]
    (cfg.box / "record.jsonl").write_text("".join(record.canon(r) + "\n" for r in cut))
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    # the verb's own invocation row lands where the cut began, so the anchor
    # reads it as a rewrite of the sealed row; either way the cut is seen
    assert got["state"] == "broken"
    assert (
        "rows were cut" in got["reason"]
        or "not the row the human sealed" in got["reason"]
    )


def test_a_row_whose_path_is_not_the_rolls_so_far_escalates(cfg, key_file):
    f = forecast_sealed(cfg)
    table.verdict(
        cfg,
        {
            "what": "open",
            "session": "s1",
            "forecast": f["subject"],
            **WORLD,
            "beacon_round": ROUND,
            "randomness": RANDOMNESS,
            "seed_id": SEED_ID,
            "world_inputs": INPUTS,
        },
    )
    cfg.beacon.fired = True
    honest, _ = honest_roll(0, "strong", 0)
    table.verdict(
        cfg,
        {
            "what": "roll",
            "session": "s1",
            "seat": 0,
            "total": 10,
            "verdict": "strong",
            "roll": honest,
            "path": "weak",
        },
    )
    table.verdict(cfg, {"what": "decision", "session": "s1", "path": "weak"})
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "not the rolls so far" in got["reason"]


def test_a_path_the_forecast_never_declared_escalates(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, verdicts=("miss", "strong"), forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "does not declare" in got["reason"]


def test_two_sessions_claiming_one_forecast_escalate(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, session="a", forecast=f["subject"])
    play(cfg, session="b", forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "more than one" in got["reason"]


# ── B1: R3 through the one door (api.reconcile, needs, checkout, CLI) ─────
def _seal_and_tip(cfg, wager, outcome):
    sub = reverse.outcome_subject(wager, outcome)
    so = api.seal_outcome(cfg, wager, outcome, proof=proof(sub))
    assert so["sealed"], so
    assert seal_tip(cfg, so["tip"])["sealed"]


def t5_box(cfg):
    """T5: the dice ran before the human sealed a tip holding the forecast;
    the human then sealed one tip over everything, then the outcome."""
    f = api.forecast(cfg, "f1", DIST, by="oracle", table=FORECAST)
    play(cfg, forecast=f["subject"])
    assert seal_tip(cfg, tip_now(cfg))["sealed"]
    _seal_and_tip(cfg, f["subject"], {"path": "strong-weak"})
    return f


def test_reconcile_scores_an_outcome_the_verdicts_carry(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    _seal_and_tip(cfg, f["subject"], got["outcome"])
    for res in (
        api.reconcile(cfg, human_key=key_file),
        table.reconcile_table(cfg, human_key=key_file),
    ):
        w = res["wagers"]["rows"][0]
        assert w["state"] == "reconciled" and "brier" in w and w["graded_by"] is None
        assert res["calibration"]["all"]["n"] == 1


def test_plain_reconcile_escalates_a_sealed_outcome_the_verdicts_do_not_carry(
    cfg, key_file
):
    """The human may seal what they like (trust root); it is not counted as
    the table's outcome when the verdict rows say another path. Plain
    api.reconcile sees it: the hook is bound in Run.reconciled (B1)."""
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    _seal_and_tip(cfg, f["subject"], {"path": "weak-weak"})
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "escalate" and "R3" in w["reason"] and "brier" not in w
    assert "not what the verdict rows say" in w["reason"]


def test_T5_plain_reconcile_escalates_and_calibration_never_sees_it(cfg, key_file):
    """B1 + S1: no score, no predicted p, band or path on the row; escalate_to
    is set; calibration and misses are drawn after the demotion."""
    t5_box(cfg)
    res = api.reconcile(cfg, human_key=key_file)
    w = res["wagers"]["rows"][0]
    assert w["state"] == "escalate" and "T5" in w["reason"]
    assert w["escalate_to"] == "human"
    for k in ("brier", "log_score", "predicted_p", "predicted_band", "actual_path",
              "path_index", "committed_under"):  # fmt: skip
        assert k not in w, k
    assert (
        res["wagers"]["escalate"] == ["f1"] and res["wagers"]["escalate_to"] == "human"
    )
    assert not (res["calibration"].get("all") or {}).get("n")
    assert res["misses"].get("misses", []) == []


def test_T5_the_checkout_screen_prints_escalate(cfg, key_file):
    t5_box(cfg)
    co = api.checkout(cfg, human_key=key_file)
    w = co["report"]["wagers"]["rows"][0]
    assert w["state"] == "escalate"
    assert "wager f1 escalate to human · R3:" in co["screen"]
    assert "calibration" not in co["screen"]


def test_T5_the_cli_checkout_prints_escalate(cfg, key_file, capsys, monkeypatch):
    for k, v in (("CONSTITUTION", cfg.constitution), ("CI", cfg.ci),
                 ("ROOT", cfg.root), ("GROVE", cfg.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    t5_box(cfg)
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone"]
    assert cli.main([*base, "checkout", "--human-key", str(key_file)]) == 0
    out = capsys.readouterr().out
    assert "wager f1 escalate to human · R3:" in out and "T5" in out


def test_needs_runs_through_the_hook(cfg):
    t5_box(cfg)
    assert api.needs(cfg)["code"] == 0


def test_a_callers_hook_is_used_off_the_table_once_per_wager(cfg, key_file):
    f = api.forecast(cfg, "f1", DIST, by="oracle")  # no table: not a table box
    assert seal_tip(cfg, f["tip"])["sealed"]
    _seal_and_tip(cfg, f["subject"], {"path": "weak-weak"})
    run, _ = api._open(cfg, "probe", ("probe",))
    called = []
    rep = run.reconciled(
        human_key=HUMAN_KEY, basis=lambda w, r, o: called.append(w["id"]) or None
    )
    assert called == ["f1"]  # asked once per wager, never twice
    assert rep["wagers"]["rows"][0]["state"] == "reconciled"


def test_R23a_on_a_table_box_a_callers_hook_is_ignored(cfg, key_file):
    """The reader is the operator's (Config.beacon), never the caller's: a
    no-op or a lying hook handed to reconcile, needs, checkout or peek changes
    nothing on a box holding table rows or table wagers."""
    f = forecast_sealed(cfg)
    _seal_and_tip(cfg, f["subject"], {"path": "weak-weak"})  # no session plays it
    for hook in (lambda w, r, o: None, table.basis(HUMAN_KEY, lambda r: {})):
        w = api.reconcile(cfg, human_key=key_file, basis=hook)["wagers"]["rows"][0]
        assert w["state"] == "escalate" and "no table session" in w["reason"]
        lines = api.peek(cfg, human_key=key_file, basis=hook)["needs"]
        assert not [x for x in lines if x.startswith("wager f1 reconciled")]


def test_round3_N1_a_table_forecast_no_session_claims_escalates(cfg, key_file):
    """The hook is on with no table row at all: a `table` wager on record binds
    it, and a table forecast no session plays for gets no table outcome."""
    f = forecast_sealed(cfg)
    _seal_and_tip(cfg, f["subject"], {"path": "weak-weak"})
    assert not any(r["kind"] == "verdict" for r in rows(cfg))
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "escalate"
    assert "no table session on record plays for this forecast" in w["reason"]


def test_a_non_table_forecast_no_session_claims_is_left_alone(cfg, key_file):
    f = api.forecast(cfg, "f1", DIST, by="oracle")
    assert seal_tip(cfg, f["tip"])["sealed"]
    play(cfg)  # table rows on record: the hook is on
    _seal_and_tip(cfg, f["subject"], {"path": "weak-weak"})
    w = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "reconciled" and "brier" in w and "basis_note" not in w


# ── decision beats: a proposal the human seals (R10, C10) ──────────────────
PENDING = {
    "fact_id": "s1::b3",
    "fact": "The bridge is out.",
    "proposed_by": "world",
    "world": "hasbeen-late-shift",
}
BEAT_PATH = "world/hasbeen-late-shift/s1::b3.md"


def test_a_beat_is_proposed_as_table_and_sealed_only_by_a_proof(cfg):
    cite = play(cfg)[-1]["row"]["hash"]
    p = table.propose_beat(cfg, PENDING, [cite])
    assert p["code"] == 0 and p["subject"].startswith("proposal:")
    assert p["row"]["who"] == "table" and p["row"]["path"] == BEAT_PATH
    assert table.beat_seal(cfg, p["subject"])["state"] == "unsealed"
    done = api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert done["sealed"] and done["written"]["path"] == BEAT_PATH
    got = table.beat_seal(cfg, p["subject"])
    assert got["state"] == "sealed" and got["by"] == "human"


def test_a_beat_sealed_by_a_name_instead_of_a_proof_is_refused(cfg):
    """A `seal` row appended by hand, `who: human`, re-chained, no proof: a
    name, not a seal (A5). The beat stays unsealed and nothing is written."""
    cite = play(cfg)[-1]["row"]["hash"]
    p = table.propose_beat(cfg, PENDING, [cite])
    rs = rows(cfg)
    forged = {
        "n": len(rs),
        "kind": "seal",
        "who": "human",
        "family": "human",
        "standing": "sealed",
        "version": rs[-1]["version"],
        "ts": "t",
        "prev": rs[-1]["hash"],
        "where": p["subject"],
        "subject": p["subject"],
        "by": "Sean",
    }
    forged["hash"] = record.h256(record.canon(forged))
    with (cfg.box / "record.jsonl").open("a") as fh:
        fh.write(record.canon(forged) + "\n")
    got = table.beat_seal(cfg, p["subject"])
    assert got["state"] == "unsealed" and "no proof" in got["why"]
    bad = api.seal_proposal(cfg, p["subject"], proof="Sean")
    assert not bad["sealed"] and not (cfg.box / "world").exists()


def test_a_beat_must_cite_verdict_rows_on_record(cfg):
    assert table.propose_beat(cfg, PENDING, [])["code"] == 1
    got = table.propose_beat(cfg, PENDING, ["ab" * 32])
    assert got["code"] == 1 and "not on record" in got["refused"]


# ── J9: the agm_head row and its check (R9) ────────────────────────────────
HEAD = "cd" * 32


def test_agm_head_is_one_verdict_row_and_hands_out_the_tip(cfg):
    res = table.agm_head(cfg, n=3, head=HEAD, db_sha="ef" * 32)
    r = res["row"]
    assert r["kind"] == "verdict" and r["what"] == "agm_head" and r["who"] == "table"
    assert (r["ledger_n"], r["head"]) == (3, HEAD) and res["tip"] == record.tip_subject(
        r
    )
    assert table.agm_head(cfg, n=-1, head=HEAD)["code"] == 1
    assert table.agm_head(cfg, n=True, head=HEAD)["code"] == 1


@pytest.mark.parametrize(
    "read, state, why",
    [
        ({"state": "read", "n": 3, "head": HEAD}, "satisfied", None),
        (
            {"state": "read", "n": 2, "head": "00" * 32},
            "failing",
            "fewer rows than anchored",
        ),
        ({"state": "read", "n": 3, "head": "00" * 32}, "failing", "the ledger is at"),
        (
            {"state": "broken", "why": "broken chain at ledger id=2"},
            "failing",
            "broken",
        ),
        (
            {"state": "unreachable", "why": "no campaign.db"},
            "unreachable",
            "no campaign.db",
        ),
        ({"state": "?"}, "unreachable", "the reader said"),
    ],
)
def test_check_agm_head_states_never_collapsed(cfg, read, state, why):
    table.agm_head(cfg, n=3, head=HEAD)
    got = table.check_agm_head(rows(cfg), lambda: read)
    assert got["state"] == state
    if why:
        assert why in got["why"]


def test_check_agm_head_a_reader_that_raises_is_unreachable(cfg):
    table.agm_head(cfg, n=3, head=HEAD)

    def boom():
        raise OSError("disk gone")

    assert table.check_agm_head(rows(cfg), boom)["state"] == "unreachable"


def test_no_agm_head_row_is_unanchored_apart_from_the_three(cfg):
    got = table.check_agm_head(
        rows(cfg), lambda: {"state": "read", "n": 3, "head": HEAD}
    )
    assert got["state"] == "unanchored"


def test_the_latest_agm_head_row_counts_and_covered_needs_a_sealed_tip(cfg, key_file):
    table.agm_head(cfg, n=2, head="00" * 32)
    res = table.agm_head(cfg, n=3, head=HEAD)
    read = lambda: {"state": "read", "n": 3, "head": HEAD}  # noqa: E731
    got = table.agm_check(cfg, read, human_key=key_file)
    assert got["state"] == "satisfied" and got["covered"] is False
    assert seal_tip(cfg, res["tip"])["sealed"]
    got = table.agm_check(cfg, read, human_key=key_file)
    assert got["state"] == "satisfied" and got["covered"] is True


def test_a_hand_written_agm_head_row_not_by_table_is_not_the_anchor(cfg):
    """Only a row the gate stamped `table` is an anchor; a `desk` turn can't
    plant one."""
    table.agm_head(cfg, n=3, head=HEAD)
    rs = rows(cfg)
    planted = {
        "n": len(rs),
        "kind": "verdict",
        "who": "desk",
        "family": "claude",
        "standing": "unattested",
        "version": rs[-1]["version"],
        "ts": "t",
        "prev": rs[-1]["hash"],
        "where": None,
        "what": "agm_head",
        "ledger": "ai-game-master",
        "ledger_n": 9,
        "head": "11" * 32,
    }
    planted["hash"] = record.h256(record.canon(planted))
    with (cfg.box / "record.jsonl").open("a") as fh:
        fh.write(record.canon(planted) + "\n")
    got = table.check_agm_head(
        rows(cfg), lambda: {"state": "read", "n": 3, "head": HEAD}
    )
    assert got["state"] == "satisfied" and got["anchored"]["n"] == 3


# ── N1, N3, S2: what a session may answer for ───────────────────────────────
@pytest.mark.parametrize("kind", ["scores", "forecast"])
def test_round3_N4_a_claim_on_a_non_table_wager_is_a_note_and_the_score_stands(
    cfg, key_file, kind
):
    """A table session claiming a wager that names no table world is noted,
    never counted: the row reconciles as it would have (round 3 N4)."""
    if kind == "scores":
        run, _ = api._open(cfg, "probe", ("probe",))
        w = run.wager("s9", scores={"a": 1, "b": 0})
        outcome, wid = {"winner": "a"}, w["hash"]
        assert seal_tip(cfg, tip_now(cfg))["sealed"]
    else:
        f = api.forecast(cfg, "f1", DIST, by="oracle")
        assert seal_tip(cfg, f["tip"])["sealed"]
        outcome, wid = {"path": "weak-weak"}, f["subject"]
    play(cfg, forecast=wid)
    sub = reverse.outcome_subject(wid, outcome)
    so = api.seal_outcome(cfg, wid, outcome, proof=proof(sub))
    assert so["sealed"], so
    assert seal_tip(cfg, so["tip"])["sealed"]
    got = api.reconcile(cfg, human_key=key_file)["wagers"]["rows"][0]
    assert got["state"] == "reconciled"
    assert got["basis_note"] == "a table session claims a non-table forecast; ignored"
    if kind == "forecast":
        assert "brier" in got


def test_N3_a_reused_session_id_escalates_and_says_so(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, session="dup", verdicts=("miss",), forecast=f["subject"], close=None)
    play(cfg, session="dup", forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "reused" in got["reason"]


def test_S2_a_session_on_another_world_supplies_no_outcome(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"], world=OTHER)
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "S2" in got["reason"]


def test_S2_other_sheets_on_the_same_world_supply_no_outcome(cfg, key_file):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"], world={**WORLD, "sheets_sha": "ee" * 32})
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "S2" in got["reason"]


def test_S2_a_forecast_naming_no_world_supplies_no_outcome(cfg, key_file):
    f = api.forecast(cfg, "f1", DIST, by="oracle")
    assert seal_tip(cfg, f["tip"])["sealed"]
    play(cfg, forecast=f["subject"])
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and "names no world" in got["reason"]


@pytest.mark.parametrize(
    "bad",
    [{"world": "w"}, {"world": "w", "sheets_sha": ""}, {**WORLD, "x": "y"}, "w"],
)
def test_forecast_refuses_a_table_naming_that_is_not_world_and_sheets(cfg, bad):
    res = api.forecast(cfg, "f1", DIST, by="oracle", table=bad)
    assert res["code"] == 1 and "table names exactly" in res["refused"]


# ── B2: beat_seal names what was sealed ────────────────────────────────────
def test_B2_beat_seal_names_the_sealed_path_and_fact(cfg):
    cite = play(cfg)[-1]["row"]["hash"]
    p = table.propose_beat(cfg, {**PENDING, "world": "hasbeen-late-shift"}, [cite])
    assert p["row"]["path"] == "world/hasbeen-late-shift/s1::b3.md"
    assert api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))["sealed"]
    got = table.beat_seal(cfg, p["subject"])
    assert got["state"] == "sealed" and got["path"] == p["row"]["path"]
    assert got["data"] == PENDING["fact"] and got["fact_id"] == "s1::b3"


def test_a_beat_proposal_goes_through_the_shared_proposal_append(cfg):
    """One helper for the turn path and the table path (idea side §6.3): the
    beat row is what find_proposal and pooled read."""
    cite = play(cfg)[-1]["row"]["hash"]
    p = table.propose_beat(cfg, PENDING, [cite])
    pooled = api.pooled(cfg)["pooled"]
    assert [x["subject"] for x in pooled] == [p["subject"]]


# ── S3: seal-tip lists table rows before the human signs ───────────────────
def test_S3_seal_tip_lists_verdict_sessions_and_agm_heads(cfg):
    f = api.forecast(cfg, "f1", DIST, by="oracle", table=FORECAST)
    play(cfg, forecast=f["subject"])
    table.agm_head(cfg, n=3, head=HEAD)
    listing = api.seal_tip(cfg, tip_now(cfg))  # no proof: shown, not sealed
    assert not listing["sealed"]
    by = {c["what"]: c for c in listing["covers"]}
    assert by["forecast"]["id"] == "f1"
    s = by["verdict session"]
    assert (s["session"], s["rows"], s["forecast"], s["who"]) == (
        "s1",
        4,
        "f1",
        ["table"],
    )
    a = by["agm_head"]
    assert (a["ledger_n"], a["head"], a["who"]) == (3, HEAD, "table")


def test_S3_a_hand_appended_table_row_is_listed_not_hidden(cfg):
    """`table` gates the write, it does not sign the row: a re-chained row
    labelled who=table with no key is still shown before the human signs."""
    rs = rows(cfg)
    forged = {"n": len(rs), "kind": "verdict", "who": "table", "family": "python",
              "standing": "unattested", "version": rs[-1]["version"], "ts": "t",
              "prev": rs[-1]["hash"], "where": None, "what": "agm_head",
              "session": "agm", "ledger": "ai-game-master", "ledger_n": 99,
              "head": "ab" * 32}  # fmt: skip
    forged["hash"] = record.h256(record.canon(forged))
    with (cfg.box / "record.jsonl").open("a") as fh:
        fh.write(record.canon(forged) + "\n")
    listing = api.seal_tip(cfg, tip_now(cfg))
    assert any(
        c["what"] == "agm_head" and c["ledger_n"] == 99 for c in listing["covers"]
    )


def test_S3_the_cli_prints_the_table_rows_it_would_cover(cfg, capsys, monkeypatch):
    for k, v in (("CONSTITUTION", cfg.constitution), ("CI", cfg.ci),
                 ("ROOT", cfg.root), ("GROVE", cfg.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    play(cfg)
    table.agm_head(cfg, n=3, head=HEAD)
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone"]
    cli.main([*base, "seal-tip", tip_now(cfg)])
    out = capsys.readouterr().out
    assert (
        "verdict session 's1' · 4 row(s) · forecast none" in out
        and f"agm_head · 'ai-game-master' n 3 head '{HEAD}'" in out
    )


# ── S4: db_sha beside the state; the ledger-side A7 ────────────────────────
@pytest.mark.parametrize(
    "anchored, read, said",
    [("ee" * 32, "ee" * 32, "matches"), ("ee" * 32, "ff" * 32, "differs"),
     (None, "ff" * 32, "not compared"), ("ee" * 32, None, "not compared")],
)  # fmt: skip
def test_S4_db_sha_is_reported_beside_the_state_and_never_moves_it(
    cfg, anchored, read, said
):
    table.agm_head(cfg, n=3, head=HEAD, db_sha=anchored)
    got = table.check_agm_head(
        rows(cfg), lambda: {"state": "read", "n": 3, "head": HEAD, "db_sha": read}
    )
    assert got["state"] == "satisfied" and got["db_sha"] == said


def test_S4_ledger_side_A7_append_then_cut_back_reads_satisfied(cfg):
    """Documented limit: rows appended after the anchor and cut back leave
    (n, head) as anchored, so the state is satisfied; only db_sha can show it."""
    table.agm_head(cfg, n=3, head=HEAD, db_sha="ee" * 32)
    after = {"state": "read", "n": 3, "head": HEAD, "db_sha": "ff" * 32}
    got = table.check_agm_head(rows(cfg), lambda: after)
    assert got["state"] == "satisfied" and got["db_sha"] == "differs"


def test_N4_the_agm_head_row_records_where_the_db_was_read(cfg):
    res = table.agm_head(cfg, n=3, head=HEAD, db="/box/campaign.db")
    assert res["row"]["db"] == "/box/campaign.db"
    got = table.check_agm_head(
        rows(cfg), lambda: {"state": "read", "n": 3, "head": HEAD}
    )
    assert got["anchored"]["db"] == "/box/campaign.db"


# ── S6: the documented CLI verbs ───────────────────────────────────────────
def test_cli_verdict_and_ledger_head(cfg, tmp_path, capsys, monkeypatch):
    for k, v in (("CONSTITUTION", cfg.constitution), ("CI", cfg.ci),
                 ("ROOT", cfg.root), ("GROVE", cfg.grove)):  # fmt: skip
        monkeypatch.setattr(cli, k, v)
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone"]
    f = tmp_path / "row.json"
    f.write_text(json.dumps({"what": "open", "session": "s1", "verdict": "open"}))
    assert cli.main([*base, "verdict", "--json", str(f)]) == 0
    assert capsys.readouterr().out.startswith("verdict row ")
    f.write_text(json.dumps({"kind": "seal", "what": "open", "session": "s1"}))
    assert cli.main([*base, "verdict", "--json", str(f)]) != 0
    capsys.readouterr()
    rc = cli.main([*base, "ledger-head", "--n", "3", "--head", HEAD, "--db", "/x/c.db"])
    out = capsys.readouterr().out
    assert rc == 0 and "agm_head row" in out and "tip tip:" in out
    last = [r for r in rows(cfg) if r.get("what") == "agm_head"][-1]
    assert (last["ledger_n"], last["head"], last["db"]) == (3, HEAD, "/x/c.db")


# ── round 3: N3 plain-text ids, N5 a beat names its world ──────────────────
@pytest.mark.parametrize("sid", ["s1\nf1 reconciled", "s\u202e1", "s\u20281", "s\t1"])
def test_round3_N3_a_session_id_with_control_or_separator_is_refused(cfg, sid):
    res = table.verdict(cfg, {"what": "open", "session": sid})
    assert res["code"] == 1 and "plain text" in res["refused"]


def test_round3_N3_cover_line_reprs_ids_and_says_an_unknown_forecast(cfg):
    table.verdict(cfg, {"what": "open", "session": "s 1", "forecast": "ab" * 32})
    c = next(
        c
        for c in api.seal_tip(cfg, tip_now(cfg))["covers"]
        if c["what"] == "verdict session"
    )
    line = view.cover_line(c)
    assert "verdict session 's 1'" in line
    assert f"forecast '{'ab' * 32}' not on record" in line


def test_round3_N5_a_beat_with_no_world_is_refused(cfg):
    cite = play(cfg)[-1]["row"]["hash"]
    bare = {k: v for k, v in PENDING.items() if k != "world"}
    res = table.propose_beat(cfg, bare, [cite])
    assert res["code"] == 1 and "names its world" in res["refused"]
    assert not any(r["kind"] == "proposal" for r in rows(cfg))


# ── W4 break 1: a table forecast waits for a beacon round ──────────────────
@pytest.mark.parametrize(
    "bad, why",
    [
        (WORLD, "table names exactly"),  # no round at all
        ({**WORLD, "beacon_round": True}, "drand round number"),
        ({**WORLD, "beacon_round": beacon.round_at(NOW)}, "out already"),
        ({**WORLD, "beacon_round": 1}, "out already"),
    ],
)
def test_a_table_forecast_needs_a_round_still_ahead(cfg, bad, why):
    res = api.forecast(cfg, "f1", DIST, by="oracle", table=bad)
    assert res["code"] == 1 and why in res["refused"]


def test_R23a_the_forecast_door_asks_the_reader_never_the_box_clock(cfg):
    """A clock set back can't make a round that is out look ahead; with no
    reader nothing can be shown ahead; the reader's latest is recorded."""
    old = api.Config(**{**cfg.__dict__, "now": "2026-01-01T00:00:00Z"})
    out = beacon.round_at(NOW) - 1000  # ahead of the skewed clock, out by the reader
    res = api.forecast(
        old, "f0", DIST, by="oracle", table={**WORLD, "beacon_round": out}
    )
    assert res["code"] == 1 and "out already" in res["refused"]
    off = api.Config(**{**cfg.__dict__, "beacon": None})
    res = api.forecast(off, "f0", DIST, by="oracle", table=FORECAST)
    assert res["code"] == 1 and "no beacon reader" in res["refused"]
    f = forecast_sealed(cfg)
    assert f["row"]["beacon_latest"] == beacon.round_at(NOW)
    tip = next(
        r for r in rows(cfg) if r["kind"] == "seal" and r.get("subject") == f["tip"]
    )
    assert tip["beacon_latest"] == beacon.round_at(NOW)


def test_R23a_one_table_forecast_per_round(cfg):
    forecast_sealed(cfg)
    res = api.forecast(cfg, "f2", DIST, by="oracle", table=FORECAST)
    assert res["code"] == 1 and "one forecast per round" in res["refused"]


def test_the_round_arithmetic_is_play_pys():
    assert beacon.round_time(1) == beacon.GENESIS
    r = beacon.round_at(NOW)
    assert beacon.round_time(r) <= beacon.epoch(NOW) < beacon.round_time(r + 1)
    assert beacon.seed_id("00" * 32) == record.h256(bytes(32))[:16]


def honest_box(cfg):
    f = forecast_sealed(cfg)
    play(cfg, forecast=f["subject"])
    return f


def test_a_proposal_says_bls_is_unverified(cfg, key_file):
    f = honest_box(cfg)
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    assert got["state"] == "proposed" and got["beacon"]["round"] == ROUND
    assert "BLS" in got["beacon"]["bls"]


def _with_beacon(cfg, read):
    return api.Config(**{**cfg.__dict__, "beacon": read})


def test_no_beacon_reader_is_unreachable_never_escalate(cfg, key_file):
    f = honest_box(cfg)
    off = _with_beacon(cfg, None)
    got = table.outcome_from_verdicts(off, f["subject"], human_key=key_file)
    assert got["state"] == "unreachable" and "no beacon reader" in got["reason"]
    _seal_and_tip(cfg, f["subject"], {"path": "strong-weak"})
    w = api.reconcile(off, human_key=key_file)["wagers"]["rows"][0]
    assert w["state"] == "unreachable" and "no beacon reader" in w["why"]
    assert "brier" not in w


def test_a_reader_that_fails_is_unreachable(cfg, key_file):
    f = honest_box(cfg)

    def down(rnd):
        raise OSError("no route to drand")

    got = table.outcome_from_verdicts(
        _with_beacon(cfg, down), f["subject"], human_key=key_file
    )
    assert got["state"] == "unreachable" and "no route to drand" in got["reason"]


def test_a_seed_that_is_not_the_rounds_randomness_escalates(cfg, key_file):
    """The grind: the session's randomness is on its open row, but drand says
    the round was something else."""
    f = honest_box(cfg)
    other = _with_beacon(cfg, lambda r: {"randomness": "77" * 32})
    got = table.outcome_from_verdicts(other, f["subject"], human_key=key_file)
    assert got["state"] == "escalate" and f"not round {ROUND}'s" in got["reason"]


def test_a_round_that_fired_before_the_tip_seal_escalates():
    before = beacon.round_at(NOW)
    w = {"table": {**WORLD, "beacon_round": ROUND}, "beacon_latest": before}
    opened = {"beacon_round": ROUND, "randomness": RANDOMNESS}
    sealed = {"ts": NOW}
    assert table.beacon_problem(opened, w, sealed, drand, before) is None
    # R23a: the reader's rounds carry the order: the wager's, and the tip
    # seal's as the ANCHOR keeps it (the seal row's own field is not read)
    for tip, wager, at_seal, said in (
        (sealed, w, ROUND, "out when the tip seal"),
        ({**sealed, "beacon_latest": before}, w, ROUND, "out when the tip seal"),
        (sealed, {**w, "beacon_latest": ROUND + 5}, before, "out when the forecast"),
        (
            sealed,
            {k: v for k, v in w.items() if k != "beacon_latest"},
            before,
            "forecast holds no",
        ),
        ({"ts": "2030-01-01T00:00:00Z"}, w, before, "not after the tip seal"),
    ):
        state, why = table.beacon_problem(opened, wager, tip, drand, at_seal)
        assert state == "escalate" and said in why, (said, why)
    other = {**opened, "beacon_round": ROUND + 1}
    state, why = table.beacon_problem(other, w, sealed, drand, before)
    assert state == "escalate" and f"awaits round {ROUND}" in why
    # an anchor with no round for this tip is unchecked, never a finding
    state, why = table.beacon_problem(opened, w, sealed, drand, None)
    assert state == "unreachable" and "anchor holds no reader round" in why


def test_R23a_off_a_set_piece_the_stat_is_the_beats_suggests():
    """R22: code rolls a beat's suggested stat unless the beat is a set piece;
    a stat picked after the dice on any other beat escalates."""
    plain = [dict(b, set_piece=False) for b in BEATS]
    rows = _rolls("weak", "strong")
    sheets = INPUTS["sheets"]
    why = table.dice_problem(OPEN, rows, sheets, plain, {})
    assert "not a set piece" in why and "suggested 'S1weak'" in why
    assert table.dice_problem(OPEN, rows, sheets, BEATS, {}) is None
    on = [dict(plain[0], suggests="S0weak"), dict(plain[1], suggests="S1strong")]
    assert table.dice_problem(OPEN, rows, sheets, on, {}) is None
    # after the alias map: a world stat and its engine name are one stat
    alias = [dict(on[0], suggests="Grit"), on[1]]
    assert table.dice_problem(OPEN, rows, sheets, alias, {"Grit": "S0weak"}) is None
    assert "no action beat" in table.dice_problem(OPEN, rows, sheets, on[:1], {})


# ── W4 break 2, 3: the roll rows are one honest run of the open row's dice ──
OPEN = {"randomness": RANDOMNESS, "seed_id": SEED_ID}


def _rolls(*verdicts):
    """Honest roll rows, one per verdict, counters running on from 0."""
    out, at = [], 0
    for i, v in enumerate(verdicts):
        roll, at = honest_roll(at, v, i)
        out.append({"n": i + 1, "what": "roll", "seat": 0, "total": roll["result"],
                    "verdict": v, "roll": roll})  # fmt: skip
    return out


def _edit(rows, i, **roll):
    rows = [dict(r, roll=dict(r["roll"])) for r in rows]
    top = {k: roll.pop(k) for k in ("total", "verdict") if k in roll}
    rows[i]["roll"].update(roll)
    rows[i].update(top)
    return rows


H = _rolls("weak", "strong", "miss")


@pytest.mark.parametrize(
    "rolls, why",
    [
        (H, None),
        (_edit(H, 1, counter_before=0), "restart or a gap"),
        (
            _edit(H, 1, counter_before=H[1]["roll"]["counter_before"] + 1),
            "restart or a gap",
        ),
        (_edit(H, 0, counter_before=2), "restart or a gap"),
        (_edit(H, 1, seed_id="ff" * 8), "reseed"),
        (_edit(H, 0, total=12), "not its own dice's"),
        (_edit(H, 0, verdict="strong"), "not the dice's"),
        (_edit(H, 1, generator_id="random/global"), "rolled by generator"),
        (
            _edit(H, 1, result=H[1]["roll"]["result"] + 5, total=H[1]["total"] + 5),
            "replayed from the randomness",
        ),  # fmt: skip
        (_edit(H, 1, counter=H[1]["roll"]["counter"] + 2), "stream's two dice end"),
    ],
)
def test_dice_problem_replays_the_roll_records(rolls, why):
    got = table.dice_problem(OPEN, rolls, INPUTS["sheets"])
    assert got is None if why is None else why in got, got


def test_R23a_the_modifier_is_the_sheets_never_the_rolls():
    sheets = INPUTS["sheets"]
    # +3 on the record (result, total and verdict re-derived to match): refused
    r = H[0]["roll"]
    plus = _edit(H, 0, stat_value=r["stat_value"] + 3, result=r["result"] + 3,
                 total=r["result"] + 3, verdict=table.bucket(r["result"] + 3))  # fmt: skip
    assert "the sheet says" in table.dice_problem(OPEN, plus, sheets)
    # a stat or seat the sheet does not hold: refused
    assert "not on the forecast's sheets" in table.dice_problem(
        OPEN, _edit(H, 0, stat="Grit"), sheets
    )
    assert "not on the forecast's sheets" in table.dice_problem(
        OPEN, [dict(H[0], seat=1)], sheets
    )


def test_R23a_the_open_rows_world_inputs_must_hash_to_the_forecast():
    w = {"table": FORECAST}
    assert table.world_problem({"world_inputs": INPUTS}, w) == (INPUTS["sheets"], None)
    edited = {**INPUTS, "sheets": [{k: v + 3 for k, v in INPUTS["sheets"][0].items()}]}
    _, why = table.world_problem({"world_inputs": edited}, w)
    assert "another world or sheet" in why
    _, why = table.world_problem({}, w)
    assert "no world inputs" in why


def test_dice_problem_needs_the_beacon_seed_on_the_open_row():
    assert "no beacon randomness" in table.dice_problem({}, [], [])
    wrong = {"randomness": RANDOMNESS, "seed_id": "00" * 8}
    assert "not the id of its randomness" in table.dice_problem(wrong, [], [])


# ── W4 break 4: a session id the serve would withhold is refused ───────────
@pytest.mark.parametrize(
    "sid", ["ab" * 32, "s-" + "cd" * 20, "AB" * 20, "x" + "0" * 40]
)
def test_a_hash_shaped_session_id_is_refused(cfg, sid):
    res = table.verdict(cfg, {"what": "open", "session": sid})
    assert res["code"] == 1 and "shaped like a hash" in res["refused"]


def test_a_short_hex_session_id_stands(cfg):
    assert table.verdict(cfg, {"what": "open", "session": "a" * 39})["code"] == 0


# ── W4 break 7: a cut after the sealed tip reads as a cut ──────────────────
def test_checkout_on_a_record_cut_after_its_tip_says_rows_were_cut(cfg, key_file):
    f = honest_box(cfg)
    got = table.outcome_from_verdicts(cfg, f["subject"], human_key=key_file)
    _seal_and_tip(cfg, f["subject"], got["outcome"])
    cut = "".join(record.canon(r) + "\n" for r in rows(cfg)[:-3])
    (cfg.box / "record.jsonl").write_text(cut)
    rep = api.reconcile(cfg, human_key=key_file)
    assert any("rows were cut" in b for b in rep["record"]["breaks"])
    # each verb reads the anchor before its own rows land; on a fresh cut
    (cfg.box / "record.jsonl").write_text(cut)
    co = api.checkout(cfg, human_key=key_file)
    assert "rows were cut" in co["screen"] and "rewritten" not in co["screen"]


def test_a_served_roll_row_carries_its_own_dice_and_no_seed(cfg):
    """W4 break 2: `serve.view` puts `roll.result` beside `total` as `dice`,
    and nothing else from the roll record (no seed id, no counters)."""
    play(cfg)
    sc = api.scope(cfg, by=("who",), match={"who": "table"})
    assert api.seal_scope(cfg, sc["subject"], proof=proof(sc["subject"]))["sealed"]
    doc = api.serve(cfg, sc["spec"], max_chars=CAP)["doc"]
    served = [r for t in doc["tables"] for r in t["rows"] if "total" in r]
    assert served and all(r["dice"] == r["total"] for r in served)
    assert SEED_ID not in json.dumps(doc) and RANDOMNESS not in json.dumps(doc)


def test_R23a_peek_with_no_keys_file_makes_nothing_and_says_unreachable(cfg):
    play(cfg)
    cfg.keys.unlink()
    snap = sorted(p for p in cfg.keys.parent.parent.rglob("*"))
    got = api.peek(cfg)
    assert got["state"] == "unreachable" and "no keys file" in got["why"]
    assert not cfg.keys.exists()
    assert sorted(p for p in cfg.keys.parent.parent.rglob("*")) == snap
