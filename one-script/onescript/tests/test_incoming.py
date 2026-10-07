"""What the two incoming passes (2026-10-02) changed in the one script, held by tests.

Opus pass: queue age as reported state (P3), no verdict for a missing report (P5),
the absence of dissent recorded (P6). Haiku pass: the quote checker it exposed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate  # noqa: E402
from onescript.run import Run  # noqa: E402

KEYS = {"vishwakarma": b"k-vish"}
LAW = {"trace_ids": ["CONST-III", "CONST-VI"], "grants": []}
INCOMING = Path(__file__).resolve().parents[2] / "incoming"


def ident(who="vishwakarma", family="qwen"):
    return {"who": who, "family": family, "sig": gate.sign(KEYS[who], who, family)}


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-02T00:00:{next(n):06d}Z"


def test_backpressure_reports_queue_depth_and_oldest(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    for what in ("widen reach", "open a port"):
        r.turn(
            ident(),
            "amend",
            change={"kind": "edit", "amends": ["CONST-III"], "what": what},
        )
    rep, screen = r.checkout()
    assert rep["backpressure"]["open"] == 2
    assert rep["backpressure"]["oldest"] == rep["awaiting"][0]["ts"]
    assert screen.splitlines()[1].startswith(
        "  · queue: 2 waiting on you, oldest since"
    )


def test_backpressure_authorises_nothing(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    r.turn(
        ident(), "amend", change={"kind": "edit", "amends": ["CONST-III"], "what": "w"}
    )
    rep, _ = r.checkout()
    assert [a["verdict"] for a in rep["awaiting"]] == ["awaiting_seal"]  # still waiting


def test_no_reconcile_on_record_is_unknown_not_satisfied(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    first = r.checkin()
    assert first["report"] == {"state": "never"}
    _, screen = r.checkout(boot_report=first)
    assert "enforcement status unknown" in screen
    again = r.checkin()
    assert again["report"]["state"] == "current"


def test_agreement_records_that_no_dissent_was_recorded(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    from onescript import resolve

    def M(name, family):
        return resolve.Model(name, family, f"sha-{name}", True, 1, lambda q: "1998")

    r.night(
        ["date?"], [M("q", "qwen"), M("l", "llama")], resolve.Budget(5), lambda: False
    )
    rep, screen = r.checkout()
    assert rep["witness"]["agreed"][0]["dissent"] == "none recorded"
    assert "dissent: none recorded" in screen


def test_the_misattributed_quote_in_the_haiku_pass_is_caught():
    text = (
        INCOMING / "haiku-2026-10-02" / "proposal-overnight-and-morning-screen.md"
    ).read_text()
    said = "We are building a python system. Every gate fails closed, and loud."
    rows = [
        r
        for r in gate.check_claims(text, {"operator_text": said})
        if r["kind"] == "quote"
    ]
    assert any(
        r["claim"] == "the night has a budget" and r["verdict"] == "unverified"
        for r in rows
    )
    # It used to pair quote marks across code blocks and tables (18 rows, most of
    # them fragments of code). Now every row is a quote in prose.
    assert len(rows) == 6
    assert not any(c in r["claim"] for r in rows for c in ("\n", "|", "self."))


def test_the_operators_real_words_in_the_haiku_pass_verify():
    text = (
        INCOMING / "haiku-2026-10-02" / "proposal-heartbeat-and-loops.md"
    ).read_text()
    said = (
        "when a system can ask 13 honest questions of itself, then the system "
        "is at a stable point."
    )
    rows = [
        r
        for r in gate.check_claims(text, {"operator_text": said})
        if r["kind"] == "quote" and r["verdict"] == "verified"
    ]
    assert any("13 honest questions" in r["claim"] for r in rows)
