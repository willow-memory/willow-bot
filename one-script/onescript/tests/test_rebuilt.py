"""rebuilt/one.py: the one script's seven parts done by willow's kernel and
add-ons, run on willow's own demo folder, and the record it leaves read by
this one script's own `Record`, so the two stay one record both ways.

Skips when willow isn't beside willow-bot (CI checks it out there)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from onescript import record

ONE = Path(__file__).resolve().parents[2] / "rebuilt" / "one.py"
spec = importlib.util.spec_from_file_location("rebuilt_one", ONE)
one = importlib.util.module_from_spec(spec)
spec.loader.exec_module(one)
WILLOW = one.find_willow(None)
NOW = ["--now", "2026-10-24T09:00:00Z"]

pytestmark = pytest.mark.skipif(
    WILLOW is None, reason="willow-memory/willow isn't beside willow-bot"
)


def run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ONE), *argv], capture_output=True, text=True
    )


def rows(box: Path) -> list[dict]:
    return [json.loads(x) for x in (box / "record.jsonl").read_text().splitlines()]


@pytest.fixture
def pile(tmp_path: Path) -> Path:
    p = tmp_path / "pile"
    p.mkdir()
    (p / "garden.md").write_text(
        "Tomatoes want six hours of sun.\n\nThe bees swarmed in May.\n"
    )
    return p


def opened(tmp_path: Path, pile: Path) -> Path:
    box = tmp_path / "box"
    got = run(
        "checkin", "--box", str(box), "--pile", str(pile), "--key",
        str(tmp_path / "key"), *NOW,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    return box


def test_check_in_opens_on_the_pinned_kernel_and_the_law(tmp_path, pile):
    box = opened(tmp_path, pile)
    got = run("checkin", "--box", str(box), *NOW)
    assert got.returncode == 2 and "a session is open" in got.stderr
    cin = rows(box)[-1]
    assert cin["kind"] == "checkin" and cin["who"] == "onescript"
    assert {g["gate"]: g["state"] for g in cin["gates"]} == {
        "pin": "satisfied",
        "law": "satisfied",
        "beats": "satisfied",
        "seals": "satisfied",
    }
    assert cin["law"] == one.sha(WILLOW / "CONSTITUTION.md")


def test_nothing_moves_before_a_check_in(tmp_path, pile):
    box = tmp_path / "box"
    subprocess.run(
        [sys.executable, str(WILLOW / "kernel.py"), "init", "--box", str(box),
         "--pile", str(pile), "--key", str(tmp_path / "key"), *NOW],
        check=True, capture_output=True,
    )  # fmt: skip
    for argv in (["seed", "bees"], ["escalate", "bees"], ["checkout"]):
        got = run(*argv, "--box", str(box), *NOW)
        assert got.returncode == 2 and "no open check-in" in got.stderr


def test_the_seed_is_predicted_bitten_and_graded_once_per_check_in(tmp_path, pile):
    box = opened(tmp_path, pile)
    got = run("seed", "--box", str(box), "when did the bees swarm", *NOW)
    doc = json.loads(got.stdout)
    assert got.returncode == 0 and doc["state"] == "populated"
    assert doc["served"][0]["where"] == "garden.md:3"
    assert doc["predicted"] == {"likely": None, "n": 0, "got": "populated",
                                "right": None}  # fmt: skip
    assert doc["again"] == {"times": 1, "rung": "asked"}
    again = run("seed", "--box", str(box), "bees", *NOW)
    assert again.returncode == 2 and "one seed per check-in" in again.stderr
    words = (box / "record.jsonl").read_text()
    assert "swarm" not in words and "Tomatoes" not in words


def test_the_record_predicts_from_what_bites_came_to():
    bite = {"kind": "bite"}
    rs = [{**bite, "served": [{"echo": False}]}, {**bite, "served": []}]
    assert one.declare(rs)["likely"] == "empty", "a tie asks the person"
    rs.append({**bite, "served": [{"echo": False}]})
    assert one.declare(rs) == {
        "likely": "populated",
        "n": 3,
        "seen": {"empty": 1, "populated": 2},
    }


def test_a_turn_goes_through_the_kernels_door_and_waits_on_the_seal(tmp_path, pile):
    box = opened(tmp_path, pile)
    doc = json.loads(run("seed", "--box", str(box), "bees swarm", *NOW).stdout)
    good = {"path": "notes/bees.md", "data": "May.", "claim": "her notes",
            "cites": [doc["served"][0]["id"]]}  # fmt: skip
    bad = {**good, "cites": ["0" * 64]}
    f = tmp_path / "rows.jsonl"
    f.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    got = run("turn", "--box", str(box), "--proposal", str(f), *NOW)
    assert got.returncode == 42
    assert "pass  proposal:" in got.stdout and "didn't serve" in got.stdout
    assert "the seal is yours: python kernel.py seal" in got.stdout
    assert not (box / "written").exists(), "nothing is written unsealed"


def test_escalate_climbs_the_ladder_and_ends_at_the_person(tmp_path, pile):
    box = opened(tmp_path, pile)
    got = run("escalate", "--box", str(box), "who owes me money", *NOW)
    doc = json.loads(got.stdout)
    assert got.returncode == 42 and doc["end"] == "escalated:to_the_person"
    assert [r["rung"] for r in doc["rungs"]] == [
        "sealed answer", "your files", "every match", "where to look", "a model",
    ]  # fmt: skip
    assert doc["rungs"][-1]["state"] == "unreachable"
    row = rows(box)[-1]
    assert row["kind"] == "escalate" and "why" not in json.dumps(row["rungs"])
    got = run("escalate", "--box", str(box), "tomatoes sun", *NOW)
    assert got.returncode == 0 and json.loads(got.stdout)["end"] == "answered"


def test_checkout_reverses_views_and_bundles_the_session(tmp_path, pile):
    box = opened(tmp_path, pile)
    run("seed", "--box", str(box), "bees swarm", *NOW)
    got = run("checkout", "--box", str(box), *NOW)
    assert got.returncode == 42, got.stdout + got.stderr
    out = got.stdout
    assert "CHECK-OUT" in out and "predictions: 0 of 0 right" in out
    assert "NEEDS YOU" in out and "SESSION" in out
    kinds = [r["kind"] for r in rows(box)]
    assert kinds[-1] == "session" and "checkout" in kinds
    s = rows(box)[-1]
    assert s["from"] == 0 and s["to"] == kinds.index("checkout")
    got = run("seed", "--box", str(box), "bees", *NOW)
    assert got.returncode == 2 and "no open check-in" in got.stderr


def test_the_one_scripts_own_record_reads_the_rebuilt_box(tmp_path, pile):
    """Both ways: a box the rebuilt script wrote through willow's kernel is
    this one script's record, row for row."""
    box = opened(tmp_path, pile)
    run("seed", "--box", str(box), "bees swarm", *NOW)
    run("escalate", "--box", str(box), "tomatoes", *NOW)
    run("checkout", "--box", str(box), *NOW)
    rec = record.Record(box, lambda: "", "", object())
    assert rec.verify_chain() == []
    assert [r["hash"] for r in rec.rows()] == [r["hash"] for r in rows(box)]
    assert run("verify", "--box", str(box)).returncode == 0


def test_an_edited_row_is_refused_by_every_verb(tmp_path, pile):
    box = opened(tmp_path, pile)
    rec = box / "record.jsonl"
    rec.write_text(rec.read_text().replace('"checkin"', '"checked"', 1))
    for argv in (["seed", "bees"], ["checkout"], ["needs"], ["verify"]):
        got = run(*argv, "--box", str(box), *NOW)
        assert got.returncode == 2 and "chain broken" in got.stderr
    assert record.Record(box, lambda: "", "", object()).verify_chain() != []


def test_a_kernel_off_its_pin_hard_closes(tmp_path, pile, monkeypatch):
    box = opened(tmp_path, pile)
    k = one.load(WILLOW)
    pin = tmp_path / "kernel.pin"
    pin.write_text(json.dumps({"sha256": "0" * 64}))
    monkeypatch.setattr(one, "PIN", pin)
    gs = {g["gate"]: g for g in one.gates(k, WILLOW, box, [])}
    assert (
        gs["pin"]["state"] == "failing"
        and "the pin says 000000000000" in (gs["pin"]["why"])
    )


def test_a_bad_command_line_is_refused():
    for argv in ([], ["seed", "--box", "b"], ["turn", "--box", "b"], ["fly"]):
        got = run(*argv)
        assert got.returncode == 2 and "one.py" in got.stderr


def test_the_map_names_a_willow_piece_for_every_part():
    got = run("map")
    assert got.returncode in (0, 42)
    parts = {p.name for p in (ONE.parents[1] / "onescript").glob("*.py")}
    assert {p for p, _, _ in one.MAP} == parts - {"__init__.py"}
    assert got.stdout.strip().splitlines()[-1].endswith("the bot's own")


def test_a_sealed_answer_is_served_first_by_seed_and_escalate(tmp_path, pile):
    box = opened(tmp_path, pile)
    doc = json.loads(run("seed", "--box", str(box), "bees swarm", *NOW).stdout)
    prop = {"path": "notes/bees.md", "data": "In May.", "claim": "her notes",
            "cites": [doc["served"][0]["id"]]}  # fmt: skip
    f = tmp_path / "rows.jsonl"
    f.write_text(json.dumps(prop) + "\n")
    run("turn", "--box", str(box), "--proposal", str(f), *NOW)
    subj = next(r["subject"] for r in rows(box) if r["kind"] == "proposal")
    sealed = subprocess.run(
        [sys.executable, str(WILLOW / "kernel.py"), "seal", "--box", str(box), subj,
         *NOW], capture_output=True, text=True,
    )  # fmt: skip
    assert sealed.returncode == 0, sealed.stderr  # the person's act, with their key
    got = run("escalate", "--box", str(box), "Bees, swarm?", *NOW)
    doc = json.loads(got.stdout)
    assert got.returncode == 0 and doc["end"] == "answered"
    assert doc["answer"] == "In May." and doc["rungs"][0]["state"] == "populated"
    run("checkout", "--box", str(box), *NOW)
    assert run("checkin", "--box", str(box), *NOW).returncode == 0
    got = run("seed", "--box", str(box), "bees swarm", *NOW)
    doc = json.loads(got.stdout)
    assert got.returncode == 0 and doc["state"] == "sealed"
    # seed populated, escalate sealed, the session's own bite empty: a tie
    assert doc["predicted"] == {"likely": "empty", "n": 3, "got": "sealed",
                                "right": False,
                                "seen": {"empty": 1, "populated": 1, "sealed": 1}}  # fmt: skip
    assert doc["again"] == {"times": 3, "rung": "standing"}


def test_the_rebuilt_one_script_on_willows_blind_demo(tmp_path):
    """Bernadette, built blind in willow's demo/, as the rebuilt one script's
    pile: her raw files, no readers, the way the one script reads a repo."""
    make = WILLOW / "demo" / "bernadette" / "make_life.py"
    if not make.is_file():
        pytest.skip("this willow has no demo/bernadette")
    (tmp_path / "make_life.py").write_bytes(make.read_bytes())
    subprocess.run([sys.executable, "make_life.py"], cwd=tmp_path, check=True,
                   capture_output=True)  # fmt: skip
    box = opened(tmp_path, tmp_path / "life")
    got = run("seed", "--box", str(box), "whats the deadline for the tax", *NOW)
    doc = json.loads(got.stdout)
    top = doc["served"][0]
    assert (
        got.returncode == 0 and top["where"] == "Notes/Bernie's Vault/Tax 2025-26.md:3"
    )
    assert "31 Oct" in top["text"]  # her N6, the return due 31 October
    got = run("escalate", "--box", str(box), "did i promise siobhan anything", *NOW)
    assert got.returncode == 42, "siobhan isn't siobhán: it goes to the person"
    assert json.loads(got.stdout)["end"] == "escalated:to_the_person"
    assert run("checkout", "--box", str(box), *NOW).returncode == 42
    assert record.Record(box, lambda: "", "", object()).verify_chain() == []
