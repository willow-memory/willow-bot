"""ΔΣ=42 at the exit: 0 when a human said it (the pile, a seal, a public-record
nugget), 42 when there isn't enough to say a human said it was true. The
operator, 2026-10-21: "42 just means I don't have enough information to say a
human said this was true." A 42 seed goes on Jeles's gap queue."""

from __future__ import annotations

from dataclasses import replace

from onescript import api, prompt_path
from onescript import jeles_store as js
from onescript.tests.test_api import cfg, rows  # noqa: F401
from onescript.tests.test_jeles_store import ARGUMENT, JELES_BITE, LIGHT, FakeJeles
from onescript.tests.test_store import BITE, box  # noqa: F401

MACHINE = {**LIGHT, "_id": "s9", "verification_kind": "machine"}
NOWHERE = "zorbleflux grommets wibbleton quaxle"


class GapJeles(FakeJeles):
    """FakeJeles with Jeles's gap log as a list."""

    def __init__(self, *nuggets):
        super().__init__(*nuggets)
        self.gaps: list[str] = []

    def gap(self, question):
        self.gaps.append(question)
        return {"id": "g1", "asked_count": len(self.gaps)}

    def store(self):
        return js.JelesStore(self.search, self.get, gap=self.gap)


def test_one_number_for_42_everywhere():
    assert api.ESCALATE == prompt_path.ESCALATE == 42


def test_a_machine_nugget_is_not_a_human_saying_it():
    out = FakeJeles(MACHINE).store().rank(JELES_BITE)
    assert out["state"] == js.EMPTY and out["hits"] == []
    assert js.servable(MACHINE) == (False, "not settled in Jeles (machine)")
    assert js.servable(LIGHT)[0]  # public record: "A human said it was true."


def test_the_gap_log_says_logged_or_why_not():
    j = GapJeles()
    assert j.store().log_gap("q") == {"state": "logged", "id": "g1", "asked": 1}
    assert FakeJeles().store().log_gap("q")["state"] == js.UNREACHABLE

    def broken(q):
        raise OSError("locked")

    st = js.JelesStore(j.search, j.get, gap=broken)
    assert st.log_gap("q") == {"state": js.UNREACHABLE, "why": "OSError: locked"}


def test_a_bite_the_pile_holds_is_0_and_logs_no_gap(box):  # noqa: F811
    j = GapJeles(LIGHT)
    res = api.seed(replace(box["cfg"], jeles=j.store()), BITE)
    assert res["code"] == 0 and "gap" not in res and j.gaps == []


def test_a_human_nugget_answers_a_bite_the_pile_misses(box):  # noqa: F811
    j = GapJeles(LIGHT, ARGUMENT)
    res = api.seed(replace(box["cfg"], jeles=j.store()), JELES_BITE)
    assert res["jeles"]["state"] == js.RANKED
    assert res["code"] == 0 and j.gaps == []


def test_nothing_a_human_said_is_42_and_goes_on_the_gap_queue(box):  # noqa: F811
    j = GapJeles(ARGUMENT, MACHINE)  # asserted and machine only
    c = replace(box["cfg"], jeles=j.store())
    res = api.seed(c, JELES_BITE)
    assert res["code"] == api.ESCALATE
    assert res["gap"] == {"state": "logged", "id": "g1", "asked": 1}
    assert j.gaps == [JELES_BITE]
    row = [r for r in rows(c) if r["kind"] == "seed"][-1]
    assert row["gap"] == {"state": "logged", "asked": 1}  # never the id
    assert JELES_BITE not in str(row)


def test_with_no_jeles_a_42_says_its_gap_was_not_logged(box):  # noqa: F811
    res = api.seed(box["cfg"], JELES_BITE)
    assert res["code"] == api.ESCALATE
    assert res["gap"] == {"state": js.UNREACHABLE, "why": "Jeles isn't configured"}


def test_a_lookup_that_finds_nothing_is_42(box):  # noqa: F811
    assert api.lookup(box["cfg"], BITE)["code"] == 0
    got = api.lookup(box["cfg"], NOWHERE)
    assert got["hits"] == [] and got["code"] == api.ESCALATE


def test_a_prompt_that_finds_nothing_says_42_and_the_hook_still_exits_0(box):  # noqa: F811
    res = api.prompt(box["cfg"], NOWHERE)
    assert res["code"] == 0 and res["doc"]["state"] == "empty"
    assert res["doc"]["code"] == api.ESCALATE
    assert "code" not in api.prompt(box["cfg"], BITE)["doc"]
