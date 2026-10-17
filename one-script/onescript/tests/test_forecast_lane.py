"""The forecast lane, J1 (SPEC W1-forecast-lane; rulings R1-R5, R14).

`forecast` writes a wager row; `seal_outcome` is the human's proof over
`reverse.outcome_subject`; `seal_tip` seals a named tip; `reconcile` and
`read_misses` are read-only and take the human's key from a file outside the
box. An outcome counts only by chain order under the human's sealed tip:
`wager.n <= tip.n < tip_seal.n < outcome.n` (R3), with the outcome seal itself
covered by a later sealed tip. Code scores (Brier, log score); the grade is
the human's, `graded_by` stays None (R4).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate, record, reverse  # noqa: E402

HUMAN_KEY = b"a-throwaway-human-key-for-tests"
CAP = 10**6
YES_NO = [{"path": "yes", "p": 0.62}, {"path": "no", "p": 0.38}]


@pytest.fixture
def cfg(tmp_path):
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    keys = tmp_path / "keys" / "keys.json"
    keys.parent.mkdir(mode=0o700)
    keys.write_text(json.dumps({"desk": "aa" * 32, "human": HUMAN_KEY.hex()}))
    keys.chmod(0o600)
    return api.Config(
        box=tmp_path / "box",
        keys=keys,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now="2026-10-17T00:00:00Z",
        no_tests=True,
        phone=True,
    )


@pytest.fixture
def key_file(tmp_path):
    """The human's key, outside the box, 0600 (SPEC H4)."""
    f = tmp_path / "human" / "key.hex"
    f.parent.mkdir(mode=0o700)
    f.write_text(HUMAN_KEY.hex() + "\n")
    f.chmod(0o600)
    return f


def rows(cfg) -> list[dict]:
    return [json.loads(x) for x in (cfg.box / "record.jsonl").read_text().splitlines()]


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def _tip_now(cfg) -> str:
    last = rows(cfg)[-1]
    return record.tip_subject(last)


def seal_tip(cfg, subject: str) -> dict:
    return api.seal_tip(cfg, subject, proof=proof(subject))


def seal_out(cfg, wager: str, outcome: dict) -> dict:
    sub = reverse.outcome_subject(wager, outcome)
    return api.seal_outcome(cfg, wager, outcome, proof=proof(sub))


def committed_round_trip(cfg, wid="f1", dist=YES_NO, outcome=None, by="desk"):
    """forecast -> seal its tip -> seal the outcome -> seal that tip."""
    f = api.forecast(cfg, wid, dist, by=by)
    assert f["code"] == 0, f
    assert seal_tip(cfg, f["tip"])["sealed"]
    s = seal_out(cfg, f["subject"], outcome or {"path_index": 0})
    assert s["sealed"], s
    assert seal_tip(cfg, s["tip"])["sealed"]
    return f, s


def wager_row(rep, wid):
    return next(r for r in rep["wagers"]["rows"] if r["id"] == wid)


# ── forecast ─────────────────────────────────────────────────────────────────
def test_forecast_writes_a_committed_wager_row(cfg):
    assert api.checkin(cfg)["code"] == 0
    f = api.forecast(cfg, "f1", YES_NO, by="desk")
    assert f["code"] == 0
    row = f["row"]
    assert row["kind"] == "wager" and row["who"] == "run" and row["id"] == "f1"
    assert row["distribution"] == YES_NO and row["by"] == "desk"
    assert row["commit"] == reverse.COMMIT and row["committed"] is None
    assert f["subject"] == row["hash"]
    assert f["tip"] == record.tip_subject(row)


def test_forecast_refuses_mass_not_one_and_duplicate_id(cfg):
    """Break (SPEC-documented J1): p sum 1.1, a path twice, a reused id: each
    refused, nothing appended but the refusal."""
    api.checkin(cfg)
    assert api.forecast(cfg, "f1", YES_NO)["code"] == 0
    bad = {
        "sum": [{"path": "yes", "p": 0.7}, {"path": "no", "p": 0.4}],
        "dup": [{"path": "yes", "p": 0.5}, {"path": "YES", "p": 0.5}],
    }
    for wid, dist in (("f2", bad["sum"]), ("f3", bad["dup"]), ("f1", YES_NO)):
        before = len(rows(cfg))
        res = api.forecast(cfg, wid, dist)
        assert res["code"] == 1 and "refused" in res
        new = rows(cfg)[before:]
        assert [r["kind"] for r in new] == ["invocation", "refused"]
    assert sum(r["kind"] == "wager" for r in rows(cfg)) == 1


@pytest.mark.parametrize(
    "dist, why",
    [
        ([{"path": "yes", "p": 1.2}, {"path": "no", "p": -0.2}], "outside [0, 1]"),
        ([{"path": "yes", "p": float("nan")}, {"path": "no", "p": 1}], "outside"),
        ([{"path": "yes", "p": True}, {"path": "no", "p": 0}], "not a number"),
        ([{"path": "yes", "p": 0.5}, {"path": "no", "p": 0.4}], "sum to"),
        ([{"path": "yes", "p": 0.5}, {"path": "no", "p": 0.5000001}], "sum to"),
        ([], "non-empty"),
        ([{"path": "", "p": 1}], "no path"),
        ([{"path": "a", "p": 1, "secret": 1}], "unknown keys"),
        ([{"path": "a", "p": 1, "tags": "north"}], "tags"),
    ],
)
def test_forecast_refuses_a_distribution_that_does_not_stand(cfg, dist, why):
    """Adversarial: p outside [0, 1]; ps not summing to 1."""
    api.checkin(cfg)
    res = api.forecast(cfg, "f1", dist)
    assert res["code"] == 1 and why in res["refused"]
    assert not any(r["kind"] == "wager" for r in rows(cfg))


def test_forecast_takes_a_role_never_a_name_and_a_well_formed_commitment(cfg):
    api.checkin(cfg)
    assert "role" in api.forecast(cfg, "f1", YES_NO, by="sean")["refused"]
    assert "committed" in api.forecast(cfg, "f1", YES_NO, committed={"x": 1})["refused"]
    c = {"sha256": "ab" * 32, "beacon_then": 6558954, "target": 6558962}
    ok = api.forecast(cfg, "f1", YES_NO, by="human", committed=c)
    assert ok["code"] == 0 and ok["row"]["committed"] == c


def test_nothing_is_forecast_before_a_checkin(cfg):
    res = api.forecast(cfg, "f1", YES_NO)
    assert res["code"] == 1 and "check-in" in res["refused"]


# ── seal_outcome, seal_tip ───────────────────────────────────────────────────
def test_seal_outcome_signs_the_outcome_and_hands_out_the_next_tip(cfg):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    s = seal_out(cfg, f["subject"], {"path": "no"})
    assert s["sealed"] and s["code"] == 0
    row = s["row"]
    assert row["kind"] == "seal" and row["who"] == "human"
    assert row["subject"] == reverse.outcome_subject(f["subject"], {"path": "no"})
    assert row["wager"] == f["subject"] and row["outcome"] == {"path": "no"}
    assert s["tip"] == record.tip_subject(row)
    assert s["rescope"]["steps"][0].startswith("python3 -m onescript scope")


def test_a_proofless_seal_is_refused_and_writes_no_seal_row(cfg):
    """Adversarial: proofless seal. Code never writes a seal row."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    res = api.seal_outcome(cfg, f["subject"], {"path_index": 0})
    assert res["code"] == 1 and not res["sealed"]
    bad = api.seal_outcome(cfg, f["subject"], {"path_index": 0}, proof="00" * 32)
    assert bad["code"] == 1 and not bad["sealed"]
    assert not any(r["kind"] == "seal" for r in rows(cfg))
    assert api.seal_tip(cfg, f["tip"])["code"] == 1  # no proof: refused too
    assert not any(r["kind"] == "seal" for r in rows(cfg))


def test_an_outcome_naming_no_declared_path_is_refused_before_any_seal(cfg):
    """Adversarial: outcome naming no declared path (SPEC-documented J1)."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    for outcome in ({"path": "maybe"}, {"path_index": 2}, {"path_index": -1}):
        res = seal_out(cfg, f["subject"], outcome)
        assert res["code"] == 1 and "names no path" in res["refused"]
    for outcome in ({}, {"path": "yes", "winner": "x"}, {"guess": 1}):
        assert seal_out(cfg, f["subject"], outcome)["code"] == 1
    assert seal_out(cfg, "ab" * 32, {"path_index": 0})["code"] == 1  # no such wager
    assert not any(r["kind"] == "seal" for r in rows(cfg))


def test_seal_tip_refuses_a_tip_that_is_not_on_record_or_older_than_the_anchor(cfg):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    fake = f"{record.TIP}{f['row']['n']}:{'cd' * 32}"
    assert seal_tip(cfg, fake)["code"] == 1
    assert seal_tip(cfg, "serve:abc")["code"] == 1
    later = _tip_now(cfg)
    assert seal_tip(cfg, later)["sealed"]
    assert seal_tip(cfg, f["tip"])["code"] == 1  # older than the anchored tip
    assert json.loads((cfg.keys.parent / api.ANCHOR).read_text())["n"] == int(
        later.split(":")[1]
    )


# ── reconcile, read_misses ───────────────────────────────────────────────────
def test_reconcile_scores_a_committed_forecast_and_never_grades(cfg, key_file):
    api.checkin(cfg)
    committed_round_trip(cfg, outcome={"path": "yes"})
    before = rows(cfg)
    rep = api.reconcile(cfg, human_key=key_file)
    assert [r["kind"] for r in rows(cfg)[len(before) :]] == ["invocation"]  # read-only
    w = wager_row(rep, "f1")
    assert w["state"] == "reconciled" and w["graded_by"] is None
    assert math.isclose(w["brier"], (0.62 - 1) ** 2)
    assert math.isclose(w["log_score"], -math.log(0.62))
    assert rep["calibration"]["by"]["desk"]["n"] == 1
    assert rep["misses"] == {"misses": [], "clusters": []}


def test_unsealed_outcome_has_no_score(cfg, key_file):
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate" and "brier" not in w


def test_reconcile_without_the_key_counts_no_outcome(cfg):
    api.checkin(cfg)
    committed_round_trip(cfg)
    rep = api.reconcile(cfg)
    assert rep["wagers"]["escalate"] == ["f1"] and "no outcome counts" in rep["why"]


def test_reconcile_refuses_human_key_inside_box(cfg, key_file):
    """Break (SPEC-documented J1): a key file under the box, or mode 0644."""
    api.checkin(cfg)
    inside = cfg.box / "key.hex"
    inside.write_text(HUMAN_KEY.hex())
    inside.chmod(0o600)
    assert api.reconcile(cfg, human_key=inside)["code"] == 2
    key_file.chmod(0o644)
    res = api.reconcile(cfg, human_key=key_file)
    assert res["code"] == 2 and "beyond its owner" in res["refused"]


def test_read_misses_reads_through_reconcile(cfg, key_file):
    api.checkin(cfg)
    committed_round_trip(cfg, "a", outcome={"path": "no"})
    committed_round_trip(cfg, "b", outcome={"path": "no"})
    res = api.read_misses(cfg, human_key=key_file)
    assert [m["id"] for m in res["misses"]] == ["a", "b"]
    assert res["clusters"] == [{"key": "yes->no", "ids": ["a", "b"]}]


# ── chain order (R3): anything else escalates ────────────────────────────────
def test_an_outcome_sealed_before_its_forecast_was_committed_escalates(cfg, key_file):
    """Adversarial: the outcome seal comes before any tip holds the wager."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    s = seal_out(cfg, f["subject"], {"path_index": 0})
    seal_tip(cfg, s["tip"])  # one tip over both: no tip between them
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate" and "wager.n <= tip.n" in w["reason"]
    assert "brier" not in w


def test_a_wager_written_after_the_tip_escalates(cfg, key_file):
    """Adversarial: wager after tip. The tip sealed before it holds nothing."""
    api.checkin(cfg)
    seal_tip(cfg, _tip_now(cfg))
    f = api.forecast(cfg, "late", YES_NO)
    s = seal_out(cfg, f["subject"], {"path_index": 0})
    seal_tip(cfg, s["tip"])
    w = wager_row(api.reconcile(cfg, human_key=key_file), "late")
    assert w["state"] == "escalate"


def test_a_forged_tip_seal_between_wager_and_outcome_commits_nothing(cfg, key_file):
    """A proofless tip seal row over the wager's tip, placed before the
    outcome, while the human's real seal of that tip comes only later: the
    subject is held, but the early row is no seal of its own."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    _forge_row(cfg, subject=f["tip"])
    s = seal_out(cfg, f["subject"], {"path_index": 0})
    seal_tip(cfg, s["tip"])
    seal_tip(cfg, _tip_now(cfg))
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate"


def _forge_row(cfg, **fields) -> None:
    """A seal row by hand, stamped `who: human`, chained the way the record
    chains: what anyone with write access to the record can append."""
    rs = rows(cfg)
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
    with (cfg.box / "record.jsonl").open("a") as f:
        f.write(record.canon(row) + "\n")


def test_a_forged_outcome_row_without_proof_counts_for_nothing(cfg, key_file):
    """Adversarial: forged outcome row without proof (T1 on the forecast lane)."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])
    out = {"path_index": 1}
    _forge_row(
        cfg,
        subject=reverse.outcome_subject(f["subject"], out),
        wager=f["subject"],
        outcome=out,
        proposed_outcome=out,  # code's reading of a beacon is never a seal
    )
    seal_tip(cfg, _tip_now(cfg))
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate" and "brier" not in w


def _rewrite(cfg, edit) -> None:
    """Edit rows and re-hash the whole chain: internally consistent."""
    prev, lines = record.GENESIS, []
    for r in rows(cfg):
        edit(r)
        r["prev"] = prev
        r["hash"] = record.h256(
            record.canon({k: v for k, v in r.items() if k != "hash"})
        )
        prev = r["hash"]
        lines.append(record.canon(r))
    (cfg.box / "record.jsonl").write_text("\n".join(lines) + "\n")


def test_a_distribution_tampered_after_sealing_counts_for_nothing(cfg, key_file):
    """Adversarial: tampered distribution after sealing, in place (the chain
    breaks) and re-hashed (the chain holds, the sealed tip does not)."""
    api.checkin(cfg)
    committed_round_trip(cfg, outcome={"path": "yes"})
    assert wager_row(api.reconcile(cfg, human_key=key_file), "f1")["state"] == (
        "reconciled"
    )
    text = (cfg.box / "record.jsonl").read_text()
    (cfg.box / "record.jsonl").write_text(text.replace('"p":0.62', '"p":0.99', 1))
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate" and "brier" not in w

    (cfg.box / "record.jsonl").write_text(text)

    def fatten(r):
        if r["kind"] == "wager":
            r["distribution"] = [{"path": "yes", "p": 0.99}, {"path": "no", "p": 0.01}]

    _rewrite(cfg, fatten)
    w = wager_row(api.reconcile(cfg, human_key=key_file), "f1")
    assert w["state"] == "escalate" and "brier" not in w


# ── R14: the outcome seal moves the human's pile; rescope before serving ─────
def test_after_an_outcome_seal_a_fresh_scope_serves_empty_until_resealed(cfg):
    """SPEC R14 / rival T11: `seal_outcome` ends with the rescope step, and
    until it is done a scope at the new tip serves `empty`."""
    api.checkin(cfg)
    f = api.forecast(cfg, "f1", YES_NO)
    seal_tip(cfg, f["tip"])  # the human's pile now holds a row
    card = api.scope(cfg, by=("who",), match={"who": "human"})
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "populated"
    # control: rows that are not the human's leave the scope where it was
    again = api.scope(cfg, by=("who",), match={"who": "human"})
    assert again["subject"] == card["subject"]

    s = seal_out(cfg, f["subject"], {"path_index": 0})
    assert s["rescope"]["steps"][0] == (
        "python3 -m onescript scope --by who --match who=human"
    )
    moved = api.scope(cfg, by=("who",), match={"who": "human"})
    assert moved["subject"] != card["subject"]
    assert api.serve(cfg, moved["spec"], max_chars=CAP)["state"] == "empty"
    # the rescope step done: the human seals the new subject
    assert api.seal_scope(cfg, moved["subject"], proof=proof(moved["subject"]))[
        "sealed"
    ]
    assert api.serve(cfg, moved["spec"], max_chars=CAP)["state"] == "populated"


# ── the CLI: the four verbs end to end ───────────────────────────────────────
def test_forecast_seal_outcome_seal_tip_reconcile_round_trip(
    cfg, key_file, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    base = [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests", "--phone",
    ]  # fmt: skip

    def go(*cmd) -> tuple[int, str]:
        rc = cli.main([*base, *cmd])
        return rc, capsys.readouterr().out

    def said(out: str) -> dict:
        return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)

    assert go("checkin")[0] == 0
    dist = tmp_path / "dist.json"
    dist.write_text(json.dumps(YES_NO))
    rc, out = go("forecast", "f1", "--dist", str(dist), "--by", "desk")
    assert rc == 0, out
    wager, first = said(out)["wager"], said(out)["tip"]
    rc, out = go("seal-tip", first, "--proof", proof(first))
    assert rc == 0 and json.loads(out)["sealed"] is True
    outcome = {"path": "yes"}
    sub = reverse.outcome_subject(wager, outcome)
    rc, out = go(
        "seal-outcome", wager, "--outcome", json.dumps(outcome), "--proof", proof(sub)
    )
    assert rc == 0, out
    lines = out.splitlines()
    assert lines[0] == f"sealed {sub}"
    assert lines[2].startswith("rescope: ")  # it ends with the rescope step
    assert lines[-1].strip().startswith("python3 -m onescript serve")
    second = said(out)["tip"]
    assert go("seal_tip", second, "--proof", proof(second))[0] == 0
    rc, out = go("reconcile", "--human-key", str(key_file))
    assert rc == 0
    rep = json.loads(out)
    assert rep["wagers"]["reconciled"] == ["f1"]
    assert rep["wagers"]["rows"][0]["graded_by"] is None
    rc, out = go("read-misses", "--human-key", str(key_file))
    assert rc == 0 and json.loads(out) == {"clusters": [], "misses": []}
    rc, out = go("read_misses")
    assert rc == 0

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"path": "yes", "p": 0.9}]))
    rc, out = go("forecast", "f2", "--dist", str(bad))
    assert rc == 1 and out.startswith("refused:")
    rc, out = go("seal-outcome", wager, "--outcome", '{"path": "nope"}', "--proof", "x")
    assert rc == 1 and "names no path" in out


# ── cross-repo: the forecasting game's seven rounds, reproduced ──────────────
GROVE = Path(
    os.environ.get("ONESCRIPT_GROVE")
    or Path(__file__).resolve().parents[3].parent / "willows-grove"
).expanduser()
GAME = GROVE / "seat" / "willow" / "experiments" / "predictions-2026-10-17"
ROUND_FILES = sorted((GAME / "rounds").glob("*.json"))
#: predictions-2026-10-17/README.md §5, as recorded
RECORDED = {"human": 0.15971, "oracle": 0.15988, "desk": 0.15991, "coin": 0.250}
WHO = {"human": "human", "willow": "desk"}  # a role, never a name


def _canon(obj) -> str:  # play.py's own `canon`
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _play():
    spec = importlib.util.spec_from_file_location("_play", GAME / "play.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _exported(d: dict) -> list[dict]:
    """One wager per (forecaster, question) of a revealed round, as
    SPEC-documented J1's `play.py export` shape: a yes/no distribution, the
    role, the foreign commitment, and code's reading of the beacon (never a
    seal)."""
    rev = d["reveal"]
    rows = {WHO[w]: f for w, f in d["forecasts"].items()}
    rows["oracle"] = {"p": rev["oracle"]}
    rows["coin"] = {"p": {q: 0.5 for q in d["questions"]}}
    out = []
    for by, f in sorted(rows.items()):
        committed = None
        if "sha256" in f:
            # the forecast is what was committed: re-hash it from the round file
            assert hashlib.sha256(_canon(f["p"]).encode()).hexdigest() == f["sha256"]
            committed = {
                "sha256": f["sha256"],
                "beacon_then": f["beacon_then"],
                "target": d["target"],
            }
        for q in sorted(d["questions"]):
            p = f["p"][q]
            out.append(
                {
                    "id": f"r{d['round']:03d}-{q}-{by}",
                    "distribution": [
                        {"path": "yes", "p": p},
                        {"path": "no", "p": 1 - p},
                    ],
                    "by": by,
                    "committed": committed,
                    "proposed_outcome": {"path": "yes" if rev["answers"][q] else "no"},
                }
            )
    return out


@pytest.mark.skipif(
    len(ROUND_FILES) != 7, reason=f"the seven round files are not at {GAME}"
)
def test_brier_matches_play_board(cfg, key_file):
    """The 28 questions of rounds 001..007, forecast, committed under a sealed
    tip, outcome-sealed with a throwaway human key and tip-sealed again, give
    the mean Brier the README records (predictions-2026-10-17/README.md §5)."""
    api.checkin(cfg)
    wagers = [w for f in ROUND_FILES for w in _exported(json.loads(f.read_text()))]
    assert len(wagers) == 28 * 4
    made = {}
    for w in wagers:
        f = api.forecast(
            cfg, w["id"], w["distribution"], by=w["by"], committed=w["committed"]
        )
        assert f["code"] == 0, f
        made[w["id"]] = f["subject"]
    assert seal_tip(cfg, _tip_now(cfg))["sealed"]  # every forecast committed
    for w in wagers:  # the human seals each outcome code proposed (SPEC H3)
        assert seal_out(cfg, made[w["id"]], w["proposed_outcome"])["sealed"]
    assert seal_tip(cfg, _tip_now(cfg))["sealed"]

    rep = api.reconcile(cfg, human_key=key_file)
    assert rep["wagers"]["escalate"] == []
    by = rep["calibration"]["by"]
    assert {k: v["n"] for k, v in by.items()} == dict.fromkeys(RECORDED, 28)
    for role, want in RECORDED.items():
        assert round(by[role]["brier"], 5 if role != "coin" else 3) == want, role
    assert all(r["graded_by"] is None for r in rep["wagers"]["rows"])

    # and the same numbers play.py's own `brier` gives, question by question
    play = _play()
    for f in ROUND_FILES:
        d = json.loads(f.read_text())
        for w in _exported(d):
            q = w["id"].split("-")[1]
            got = wager_row(rep, w["id"])["brier"]
            p = w["distribution"][0]["p"]
            assert got == play.brier(p, d["reveal"]["answers"][q])
    # round 1's 3d6: the human said 12% yes and it came no, so far from the
    # oracle's 26% but not a miss; a miss is the path given less p happening
    assert "r001-3d6-human" not in {m["id"] for m in rep["misses"]["misses"]}
    bins = rep["calibration"]["bins"]
    assert sum(b["n"] for b in bins) == 28 * 4
