"""The one script, end to end. Each test names the rule it holds the run to."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import boot, gate, resolve, reverse  # noqa: E402
from onescript.run import Run  # noqa: E402

KEYS = {"hanuman": b"k-hanuman", "vishwakarma": b"k-vish"}
HUMAN_KEY = b"passkey-in-a-coat-pocket"
LAW = {
    "trace_ids": [
        "CONST-I",
        "CONST-II",
        "CONST-III",
        "CONST-IV",
        "CONST-V",
        "CONST-VI",
        "CONST-XII",
    ],
    "user_rules": ["grant:calendar-sync", "precedent:web-lookups-no"],
    "grants": [{"who": "hanuman", "where": "calendar"}],
    "min_features": 6,
}


def ident(who, family="qwen"):
    return {"who": who, "family": family, "sig": gate.sign(KEYS[who], who, family)}


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-02T00:00:{next(n):06d}Z"


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path / "box", KEYS, LAW, clock())


DOC_EDIT = "from pathlib import Path\np=Path('w.md');s=p.read_text();p.write_text(s.replace('a','b'))\n"
TOOL = (
    "import json,hashlib,os,re\nfrom pathlib import Path\n"
    "def walk(d):\n    return sorted(Path(d).rglob('*'))\n"
    "def digest(p):\n    return hashlib.sha256(Path(p).read_bytes()).hexdigest()\n"
    "for p in walk('.'):\n    if re.search('x', str(p)) and os.path.isfile(p):\n"
    "        print(json.dumps({'p': str(p), 'h': digest(p)}))\n"
)


# ── 4 gate ─────────────────────────────────────────────────────────────────
def test_unsigned_identity_is_refused_and_recorded(run):
    row = run.turn({"who": "hanuman", "family": "qwen", "sig": "copied-format"}, "edit")
    assert row["kind"] == "refused" and "unsigned" in row["reason"]


def test_the_human_seat_is_not_entered_by_signature():
    with pytest.raises(gate.Refused):
        gate.verify(
            {"who": "human", "family": "human", "sig": "x"}, {**KEYS, "human": b"k"}
        )


def test_a_change_that_cites_nothing_is_refused(run):
    out = run.turn(
        ident("hanuman"), "edit", change={"kind": "edit", "path": "a.md", "data": b"x"}
    )
    assert out["door"]["verdict"] == "refused"
    assert not (run.rec.box / "a.md").exists()


def test_cites_unknown_law_is_refused():
    d = gate.door({"kind": "edit", "cites": ["CONST-XCIX"]}, LAW)
    assert d.verdict == "refused" and "unknown" in d.reason


def test_citation_that_does_not_match_what_it_touches_is_flagged():
    d = gate.door(
        {"kind": "edit", "cites": ["CONST-VI"], "touches": ["CONST-III"]}, LAW
    )
    assert d.verdict == "flagged"


def test_amending_the_law_waits_for_the_seal_then_the_seal_clears_it(run):
    out = run.turn(
        ident("vishwakarma"),
        "amend",
        change={"kind": "edit", "amends": ["CONST-III"], "what": "widen reach"},
    )
    door = out["door"]
    assert door["verdict"] == "awaiting_seal"
    rep, _ = run.checkout()
    assert [a["hash"] for a in rep["awaiting"]] == [door["hash"]]
    bad = run.seal(door["hash"], "a-stolen-signature", HUMAN_KEY)
    assert bad["kind"] == "refused"
    good = run.seal(door["hash"], gate.sign(HUMAN_KEY, "seal", door["hash"]), HUMAN_KEY)
    assert good["who"] == "human" and good["standing"] == "sealed"
    rep, _ = run.checkout()
    assert rep["awaiting"] == []


def test_egress_without_a_grant_writes_a_grant_card():
    d = gate.door(
        {
            "kind": "egress",
            "who": "hanuman",
            "what": "summarize the folder",
            "where": "cloud",
            "bytes": 2_300_000,
            "cites": ["CONST-III"],
        },
        LAW,
    )
    assert d.verdict == "awaiting_grant"
    assert d.card == {
        "who": "hanuman",
        "what": "summarize the folder",
        "where": "cloud",
        "bytes": 2_300_000,
    }


def test_the_gate_fails_closed_on_its_own_error():
    d = gate.door({"cites": ["CONST-VI"]}, LAW)  # no 'kind': a malformed change
    assert d.verdict == "refused" and "failing closed" in d.reason


# ── 3 record ───────────────────────────────────────────────────────────────
def test_the_stamp_comes_from_the_gate_not_the_caller(run):
    from onescript.record import Verified

    with pytest.raises(PermissionError):
        run.rec.append("note", Verified("willow", "self-declared", object()))


def test_every_row_carries_the_run_version_and_the_chain_holds(run):
    run.turn(
        ident("hanuman"),
        "edit",
        change={"kind": "edit", "path": "a.md", "data": b"x", "cites": ["CONST-VI"]},
    )
    rows = run.rec.rows()
    assert {r["version"] for r in rows} == {run.version}
    assert run.rec.verify_chain() == []


def test_editing_a_past_row_breaks_the_chain_loudly(run):
    run.turn(ident("hanuman"), "edit")
    lines = run.rec.path.read_text().splitlines()
    row = json.loads(lines[0])
    row["intent"] = "something else"
    lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    run.rec.path.write_text("\n".join(lines) + "\n")
    assert run.rec.verify_chain()


def test_every_hash_keeps_all_256_bits(run):
    from onescript.record import GENESIS, h256

    assert len(h256(b"x")) == 64 and len(GENESIS) == 64
    run.turn(
        ident("hanuman"),
        "edit",
        change={"kind": "edit", "path": "a.md", "data": b"x", "cites": ["CONST-VI"]},
    )
    rows = run.rec.rows()
    assert rows[0]["prev"] == GENESIS
    assert all(len(r["hash"]) == 64 and len(r["prev"]) == 64 for r in rows)
    assert all(len(i["sha"]) == 64 for i in run.rec.pile()["items"])


def test_a_hash_cut_to_64_bits_is_a_chain_break(run):
    run.turn(ident("hanuman"), "edit")
    lines = run.rec.path.read_text().splitlines()
    row = json.loads(lines[0])
    row["hash"] = row["hash"][:16]
    lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    run.rec.path.write_text("\n".join(lines) + "\n")
    assert run.rec.verify_chain()


# ── 3 record: the sealed tip, and lines that aren't rows ─────────────────────
@pytest.fixture
def anchored(tmp_path):
    keys = {**KEYS, gate.HUMAN: HUMAN_KEY}  # where the human's key is present
    return Run(tmp_path / "box", keys, LAW, clock(), anchor=tmp_path / "anchor.json")


def seal_the_tip(run):
    from onescript.record import tip_subject

    tip = run.rec.tip()
    return run.seal_tip(gate.sign(HUMAN_KEY, "seal", tip_subject(tip)), HUMAN_KEY)


def rewrite(run, n, **change):
    """What a forger with write access does: edit row n, rehash every row after."""
    from onescript.record import GENESIS, canon, h256

    rows, prev = run.rec.rows(), GENESIS
    for r in rows:
        if r["n"] == n:
            r.update(change)
        r["prev"] = prev
        r["hash"] = h256(canon({k: v for k, v in r.items() if k != "hash"}))
        prev = r["hash"]
    run.rec.path.write_text("".join(canon(r) + "\n" for r in rows))


def test_cutting_rows_the_human_sealed_is_a_hard_close(anchored):
    anchored.turn(ident("hanuman"), "edit")
    sealed = seal_the_tip(anchored)
    assert sealed["standing"] == "sealed" and anchored.rec.anchor_state() == "sealed"
    assert not anchored.checkin()["hard_close"]
    lines = anchored.rec.path.read_text().splitlines()
    cut = [x for x in lines if json.loads(x)["n"] < sealed["n"] - 1]
    anchored.rec.path.write_text("\n".join(cut) + "\n")
    assert anchored.rec.verify_chain() == []  # the chain alone can't see it
    b = anchored.checkin()
    assert b["hard_close"] and any("rows were cut" in x for x in b["lines"])


def test_rehashing_rows_the_human_sealed_is_a_hard_close(anchored):
    anchored.turn(ident("hanuman"), "edit")
    seal_the_tip(anchored)
    rewrite(anchored, 1, kind="FORGED")
    assert anchored.rec.verify_chain() == []  # the chain alone can't see it
    b = anchored.checkin()
    assert b["hard_close"]
    assert any("not the row the human sealed" in x for x in b["lines"])


def test_a_forged_anchor_does_not_verify_where_the_key_is(anchored):
    anchored.turn(ident("hanuman"), "edit")
    seal_the_tip(anchored)
    rewrite(anchored, 1, kind="FORGED")
    anchored.rec.set_anchor(anchored.rec.tip(), "made-without-the-key")
    b = anchored.checkin()
    assert b["hard_close"] and "anchor: its seal proof does not verify" in b["lines"]


def test_a_deleted_anchor_is_seen_from_the_seal_row(anchored):
    anchored.turn(ident("hanuman"), "edit")
    seal_the_tip(anchored)
    anchored.rec.anchor_path.unlink()
    b = anchored.checkin()
    assert b["hard_close"] and any("anchor: missing" in x for x in b["lines"])


def test_an_unreadable_anchor_is_a_hard_close_not_a_crash(anchored):
    anchored.turn(ident("hanuman"), "edit")
    seal_the_tip(anchored)
    anchored.rec.anchor_path.write_text("{not json")
    b = anchored.checkin()
    assert b["hard_close"] and b["anchor"] == "unreadable"
    assert any("anchor: unreadable" in x for x in b["lines"])


def test_a_tip_is_sealed_only_by_the_humans_key(anchored):
    anchored.turn(ident("hanuman"), "edit")
    row = anchored.seal_tip("a-stolen-signature", HUMAN_KEY)
    assert row["kind"] == "refused" and row["at"] == "seal_tip"
    assert anchored.rec.anchor_state() == "never"


def test_a_broken_chain_is_never_sealed(anchored):
    anchored.turn(ident("hanuman"), "edit")
    with anchored.rec.path.open("a") as f:
        f.write("garbage\n")
    row = seal_the_tip(anchored)
    assert row["kind"] == "refused" and "chain is broken" in row["reason"]
    assert anchored.rec.anchor_state() == "never"


def test_nothing_sealed_says_never_and_does_not_close(run):
    run.turn(ident("hanuman"), "edit")
    b = run.checkin()
    assert not b["hard_close"] and b["anchor"] == "never"


def test_the_anchor_never_lives_in_the_box(tmp_path):
    with pytest.raises(PermissionError):
        Run(tmp_path / "box", KEYS, LAW, clock(), anchor=tmp_path / "box" / "a.json")


@pytest.mark.parametrize(
    "line", [b"{not json", b"[1, 2]", b'{"kind": "boot"}', b"\xff\xfe"]
)
def test_a_garbled_line_is_a_hard_close_not_a_crash(run, line):
    run.turn(ident("hanuman"), "edit")
    with run.rec.path.open("ab") as f:
        f.write(line + b"\n")
    n = len(run.rec.path.read_text(errors="replace").splitlines())
    assert f"line {n}: garbled, not a record row" in run.rec.verify_chain()
    b = run.checkin()
    assert (
        b["hard_close"]
        and f"record: line {n}: garbled, not a record row" in (b["lines"])
    )


def test_every_write_adds_its_own_pointer_and_files_are_0644(run):
    run.turn(
        ident("hanuman"),
        "edit",
        change={
            "kind": "edit",
            "path": "notes/a.md",
            "data": b"hi",
            "cites": ["CONST-VI"],
        },
    )
    assert [i["where"] for i in run.rec.pile()["items"]] == ["notes/a.md"]
    assert oct((run.rec.box / "notes/a.md").stat().st_mode & 0o777) == "0o644"


def test_nothing_kept_is_written_to_temp(run):
    with pytest.raises(PermissionError):
        run.rec.write_file(gate.system(), "tmp/keep.py", b"x")


# ── 1 boot ─────────────────────────────────────────────────────────────────
def test_a_crash_leaves_an_open_row_and_boot_hard_closes(run):
    who = gate.verify(ident("hanuman"), KEYS)
    run.rec.open_turn(who, 214, "tag the 43 new photos")  # and then the process dies
    b = run.checkin()
    assert (
        b["hard_close"]
        and "turn 214 never closed; it was doing: tag the 43 new photos" in b["lines"]
    )
    assert b["options"]


def test_a_change_with_no_cause_hard_closes_and_an_expected_one_passes(run):
    w = gate.system()
    run.rec.write_file(w, "printer.conf", b"stats=off")
    run.rec.write_file(w, "scratch.out", b"x", expect_vanish=True)
    (run.rec.box / "scratch.out").unlink()  # expected: recorded at write
    assert not run.checkin()["hard_close"]
    (run.rec.box / "printer.conf").write_bytes(b"stats=on")  # nobody recorded this
    b = run.checkin()
    assert b["hard_close"] and any("printer.conf" in x for x in b["lines"])


def test_all_probes_hold_against_the_real_gate():
    assert all(p["held"] for p in boot.probes(KEYS, LAW))


def test_if_a_probe_gets_through_the_box_does_not_open(run, monkeypatch):
    monkeypatch.setattr(
        gate, "door", lambda change, law, index=None: gate.Decision("pass")
    )
    with pytest.raises(boot.BoxWontOpen):
        run.checkin()


# ── 5 resolve ──────────────────────────────────────────────────────────────
def M(name, family, local=True, cost=1, says="x"):
    return resolve.Model(name, family, f"sha-{name}", local, cost, lambda q, s=says: s)


def test_the_users_sources_answer_before_any_model(run):
    out = run.turn(
        ident("hanuman"),
        "ask",
        question="rally date?",
        sources=[("soil", lambda q: "1998-06")],
        models=[M("q3b", "qwen")],
        budget=resolve.Budget(5),
    )
    assert out["answer"]["rung"] == "source" and out["answer"]["ground"] == "cited"


def test_model_is_last_local_first_and_ungrounded(run):
    out = run.turn(
        ident("hanuman"),
        "ask",
        question="rally date?",
        sources=[("soil", lambda q: None)],
        models=[M("cloud", "big", local=False), M("l3b", "llama")],
        budget=resolve.Budget(5),
    )
    a = out["answer"]
    assert (a["from"], a["rung"], a["ground"]) == ("l3b", "model", "ungrounded")


def test_cloud_without_a_grant_is_skipped_and_no_model_parks_the_web_card():
    a = resolve.answer(
        "q", [], [M("cloud", "big", local=False)], resolve.Budget(5), LAW
    )
    assert a["rung"] == "web" and a["parked"] == "awaiting_grant"


def test_budget_stops_the_model_rung():
    a = resolve.answer("q", [], [M("l3b", "llama", cost=9)], resolve.Budget(5), LAW)
    assert a.get("stopped") == "budget"


def test_the_night_yields_the_moment_the_human_is_present(run):
    seen = iter([False, False, True])
    how = run.night(
        ["q1", "q2"],
        [M("a", "qwen"), M("b", "llama")],
        resolve.Budget(10),
        lambda: next(seen),
    )
    assert how.startswith("yielded") and len(run.answers) == 2


# ── 7 reverse ──────────────────────────────────────────────────────────────
def test_three_way_finds_what_the_pile_never_listed(run):
    run.rec.write_file(gate.system(), "listed.md", b"a")
    (run.rec.box / "heredoc_output.py").write_text("x")  # written outside the run
    v = {
        i["where"]: i
        for i in reverse.three_way(run.rec.box, run.rec.pile(), run.rec.rows())
    }
    assert v["listed.md"]["verdict"] == "satisfied"
    assert v["heredoc_output.py"]["verdict"] == "failing"
    assert "never listed" in v["heredoc_output.py"]["why"]


def test_the_third_repeat_is_an_offer_not_an_apply(run):
    for _ in range(3):
        run.turn(
            ident("hanuman"),
            "tool",
            change={
                "kind": "script",
                "source": TOOL,
                "none_because": "a one-off lookup",
            },
        )
    offers = run.checkout()[0]["offers"]
    assert len(offers) == 1 and offers[0]["seen"] == 3 and "?" in offers[0]["offer"]


def test_small_scripts_do_not_over_group():
    import script_match as sm

    other = (
        "from pathlib import Path\nfor n in ['x.md','y.md']:\n    q=Path(n)\n"
        "    q.write_text(q.read_text().replace('c','d'))\n"
    )
    raw = sm.match([sm.entry(DOC_EDIT, "a", None), sm.entry(other, "b", None)])[-1]
    assert raw["verdict"] == "similar"  # check 4 alone over-groups (tonight's finding)
    d = gate.door(
        {"kind": "script", "source": other, "none_because": "x"},
        LAW,
        [sm.entry(DOC_EDIT, "a", None)],
    )
    assert d.verdict == "pass"  # min_features, the user's number, holds it back


def test_witnesses_are_counted_by_family_and_splits_come_first():
    ans = [
        {"q": "same procedure?", "answer": True, "family": "qwen", "from": "q1"},
        {
            "q": "same procedure?",
            "answer": True,
            "family": "qwen",
            "from": "q2",
        },  # same family: one witness
        {"q": "same procedure?", "answer": False, "family": "llama", "from": "l1"},
        {"q": "date?", "answer": "1998", "family": "qwen", "from": "q1"},
        {"q": "date?", "answer": "1998", "family": "llama", "from": "l1"},
    ]
    w = reverse.witnesses(ans)
    assert [a["q"] for a in w["agreed"]] == ["date?"] and w["agreed"][0][
        "standing"
    ] == "witnessed"
    assert w["split"][0]["q"] == "same procedure?"


def test_law_change_and_version_change_surface_old_rows(run):
    run.turn(
        ident("hanuman"),
        "edit",
        change={"kind": "edit", "path": "a.md", "data": b"x", "cites": ["CONST-VI"]},
    )
    hit = reverse.surfaced(run.rec.rows(), {"CONST-VI"}, run.version)
    assert hit and all("CONST-VI" in s["why"] for s in hit)
    assert any(
        "now new-version" in s["why"]
        for s in reverse.surfaced(run.rec.rows(), set(), "new-version")
    )


def test_grades_only_count_what_a_human_sealed():
    ans = [{"q": "a", "answer": 1, "from": "q1"}, {"q": "b", "answer": 2, "from": "q1"}]
    g = reverse.grades(ans, sealed={"a": 1}, task_of={"a": "date"})
    assert g == {"q1": {"by_task": {"date": 1.0}, "floor": 1.0, "floor_task": "date"}}


# ── the whole run: deterministic ────────────────────────────────────────────
def _session(box: Path) -> str:
    r = Run(box, KEYS, LAW, clock())
    b = r.checkin()
    r.turn(
        ident("hanuman"),
        "tag photos",
        change={
            "kind": "edit",
            "path": "tags.json",
            "data": b"{}",
            "cites": ["grant:calendar-sync"],
        },
    )
    r.turn(ident("hanuman"), "tag photos")
    r.turn(
        ident("vishwakarma"),
        "amend",
        change={"kind": "edit", "amends": ["CONST-III"], "what": "w"},
    )
    r.night(
        ["same procedure?"],
        [M("q", "qwen", says="yes"), M("l", "llama", says="no")],
        resolve.Budget(10),
        lambda: False,
    )
    return r.checkout(sealed={}, task_of={}, boot_report=b)[1]


def test_same_inputs_same_screen_bytes(tmp_path):
    a, b = _session(tmp_path / "one"), _session(tmp_path / "two")
    assert a == b
    order = [x for x in a.splitlines() if not x.startswith(" ")]
    assert order == [
        "NEEDS YOU",
        "DISAGREEMENTS",
        "AGREED, NOT SEALED",
        "GRADES",
        "QUIET",
    ]
