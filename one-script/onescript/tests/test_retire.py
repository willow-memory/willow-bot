"""retire turns a named served row into one seal-gated proposal, refuses a cite
that was not served, and proposes nothing unless the scope is populated. Same
doc and same select give the same rows every run; nothing is written, and each
proposal stands unsealed (the door's pass is not a seal)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, proposals, retire  # noqa: E402
from onescript.tests.test_api import cfg, proof, served  # noqa: E402,F401

A = "a" * 64
B = "b" * 64


def doc(state: str = "populated") -> dict:
    return {
        "state": state,
        "return": "return rows of {path,data,cites,claim}",
        "tables": [
            {"id": A, "group": {"who": "run"}, "rows": [], "trust": "untrusted"},
            {"id": B, "group": {"who": "desk"}, "rows": [], "trust": "untrusted"},
        ],
    }


class FakeRun:
    """A stand-in Run: hands back a served doc and records what `turn` is given,
    judging each proposed row a pass with its real subject (the shape `pooled`
    and `seal` read). It never writes."""

    def __init__(self, served: dict | None):
        self._served = served
        self.taken: list | None = None
        self.bite: str | None = None

    def served_file(self):
        return self._served

    def turn(self, ident, bite, proposed=None):
        self.taken = list(proposed or [])
        self.bite = bite
        return {
            "turn": 1,
            "proposals": [
                {"subject": proposals.subject(p), "verdict": "pass", **p}
                for p in self.taken
            ],
        }


IDENT = {"who": "desk"}


def pick(cite: str, reason: str = "superseded by a later row", detail: str = "drop it"):
    return lambda _doc: [{"cite": cite, "reason": reason, "detail": detail}]


def test_a_named_served_row_becomes_one_seal_gated_proposal():
    run = FakeRun(doc())
    out = retire.propose(run, IDENT, select=pick(A))
    assert len(out["proposals"]) == 1
    row = run.taken[0]
    assert set(row) == set(proposals.KEYS)
    assert row["cites"] == [A] and row["path"] == f"retire/{A}"
    assert row["claim"] == "superseded by a later row"
    sealed = out["proposals"][0]
    assert sealed["verdict"] == "pass"
    assert sealed["subject"].startswith("proposal:")


def test_the_candidates_are_deterministic():
    one = retire.propose(FakeRun(doc()), IDENT, select=pick(A))["candidates"]
    two = retire.propose(FakeRun(doc()), IDENT, select=pick(A))["candidates"]
    assert one == two


def test_nothing_served_as_populated_proposes_nothing():
    run = FakeRun(doc(state="empty"))
    out = retire.propose(run, IDENT, select=pick(A))
    assert out["proposals"] == [] and out["candidates"] == []
    assert run.taken is None  # the door never runs on an unpopulated scope


def test_an_unreachable_scope_proposes_nothing():
    run = FakeRun(None)
    out = retire.propose(run, IDENT, select=pick(A))
    assert out["proposals"] == [] and run.taken is None


def test_a_cite_not_served_is_refused_before_the_door():
    run = FakeRun(doc())
    with pytest.raises(ValueError, match="not in the served scope"):
        retire.propose(run, IDENT, select=pick("c" * 64))
    assert run.taken is None  # nothing reached the door


def test_a_candidate_without_a_reason_is_refused():
    run = FakeRun(doc())
    with pytest.raises(ValueError, match="give its reason"):
        retire.propose(run, IDENT, select=pick(A, reason="  "))


def test_proposal_row_fits_the_contract():
    row = retire.proposal_row(A, "a reason", "the bytes to drop")
    checked, why = proposals.check(row)
    assert checked is not None, why
    assert set(row) == set(proposals.KEYS)


# ── end to end, over a real check-in / scope / seal / serve (reusing test_api) ──


def test_retire_proposes_a_drop_at_42_and_a_seal_writes_it(cfg):  # noqa: F811
    _, ids = served(cfg)
    res = api.retire(
        cfg,
        [{"cite": ids[0], "reason": "superseded by a later row", "detail": "drop it"}],
    )
    assert res["code"] == retire_escalate()  # 42: a proposal stands, unsealed
    (p,) = res["out"]["proposals"]
    assert p["verdict"] == "pass"
    assert p["path"] == f"retire/{ids[0]}" and p["cites"] == [ids[0]]
    assert p["subject"].startswith("proposal:")
    # only the human's seal acts on it
    sealed = api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert sealed["sealed"]


def test_retire_with_no_candidate_is_zero(cfg):  # noqa: F811
    served(cfg)
    res = api.retire(cfg, [])
    assert res["code"] == 0 and res["out"]["proposals"] == []


def test_retire_a_cite_not_served_is_refused(cfg):  # noqa: F811
    served(cfg)
    res = api.retire(cfg, [{"cite": "f" * 64, "reason": "stale", "detail": "drop"}])
    assert "refused" in res and res["code"] == 1


def retire_escalate() -> int:
    """The one script's exit 42, read from api so the test never hard-codes it."""
    return api.ESCALATE
