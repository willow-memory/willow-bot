"""kernel.py, the smallest script for anyone: its eight beats hold, and it is
still the one script's record, seal and bite key, byte for byte, so the one
script can check a kernel box and a kernel box can grow into a one-script box."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from onescript import canon, gate, record, sign, words

HERE = Path(__file__).resolve().parents[2] / "kernel.py"
spec = importlib.util.spec_from_file_location("kernel", HERE)
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


def cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE), *argv], capture_output=True, text=True
    )


def test_its_eight_beats_hold():
    p = cli("test")
    assert p.returncode == 0 and "eight beats held" in p.stdout, p.stderr


def test_the_whole_loop_on_the_command_line_and_the_one_script_verifies_it(tmp_path):
    pile, box, key = tmp_path / "pile", tmp_path / "box", tmp_path / "keys" / "me.key"
    pile.mkdir()
    (pile / "notes.md").write_text("The bus leaves at seven from the square.\n")
    b = ["--box", str(box), "--now", "2026-10-22T00:00:00Z"]
    assert cli("init", *b, "--pile", str(pile), "--key", str(key)).returncode == 0
    assert key.stat().st_mode & 0o077 == 0  # theirs alone
    assert cli("bite", *b, "when does the ferry sail").returncode == 42
    got = cli("bite", *b, "when does the bus leave the square")
    assert got.returncode == 0
    cite = json.loads(got.stdout)["served"][0]["id"]
    row = {"path": "bus.md", "data": "Seven.", "cites": [cite], "claim": "notes.md"}
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps(row) + "\n")
    assert cli("propose", *b, "--file", str(rows)).returncode == 42
    assert cli("out", *b).returncode == 42
    subj = k.subject_of(row)
    assert cli("seal", *b, subj).returncode == 0
    again = cli("bite", *b, "When does the bus leave the square?")
    assert again.returncode == 0 and json.loads(again.stdout)["answer"] == "Seven."
    assert cli("out", *b).returncode == 0 and cli("verify", *b).returncode == 0
    rec = record.Record(box, lambda: "", "", object())
    assert rec.verify_chain() == [] and [r["kind"] for r in rec.rows()][0] == "boot"
    text = (box / "record.jsonl").read_text()
    assert "bus" not in text and "Seven" not in text  # hashes and places, never words


def test_it_is_the_one_scripts_record_seal_and_bite_key():
    assert k.GENESIS == record.GENESIS and k.ROW_KEYS == record.ROW_KEYS
    row = {"z": "ΔΣ", "a": [1, {"b": None}]}
    assert k.canon(row) == canon.sorted_compact_utf8(row)
    key, subj = b"a key", "proposal:" + "ab" * 32
    assert k.proof(key, subj) == gate.sign(key, "seal", subj)
    assert sign.verify_seal(subj, k.proof(key, subj), key)
    for bite in ("How much SUN?!", "the forge's first bite_2", "café"):
        assert k.bite_key(bite) == words.bite_key(bite)


def test_a_key_inside_the_box_and_a_box_not_theirs_are_refused(tmp_path):
    pile = tmp_path / "pile"
    pile.mkdir()
    box = tmp_path / "box"
    inside = cli(
        "init", "--box", str(box), "--pile", str(pile), "--key", str(box / "k")
    )
    assert inside.returncode == 2 and not (box / "record.jsonl").exists()
    assert cli("bite", "--box", str(tmp_path / "nobox"), "hello").returncode == 2
    assert cli("bite").returncode == 2  # a bad command line, never run
