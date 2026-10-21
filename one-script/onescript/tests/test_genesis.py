"""genesis.py, the smallest one script's reference: its own test holds, and
its rows are the one script's rows, so `Record.verify_chain` checks a genesis
box unchanged, and sees the same edits."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from onescript import canon, record

HERE = Path(__file__).resolve().parents[2] / "genesis.py"


def load():
    spec = importlib.util.spec_from_file_location("genesis", HERE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_its_own_test_holds_and_exits_0():
    p = subprocess.run([sys.executable, str(HERE), "test"], capture_output=True)
    assert p.returncode == 0, p.stderr


def test_its_rows_verify_under_the_one_script_record(tmp_path):
    g = load()
    ok, bad = f"{sys.executable} -c pass", f"{sys.executable} -c 'raise SystemExit(3)'"
    assert g.run(tmp_path, [ok], "2026-10-21T00:00:00Z") == g.ALIVE
    assert g.run(tmp_path, [ok, bad], "2026-10-21T00:00:01Z") == g.ESCALATE
    rec = record.Record(tmp_path, lambda: "", "", object())
    assert rec.verify_chain() == [] and len(rec.rows()) == 2
    assert record.GENESIS == g.GENESIS and record.ROW_KEYS == g.ROW_KEYS
    row = {"z": "ΔΣ", "a": [1, {"b": None}]}
    assert g.canon(row) == canon.sorted_compact_utf8(row)
    path = tmp_path / "record.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"exit":42', b'"exit":0'))
    assert rec.verify_chain() and g.read(path)[1]  # both see the edit


def test_the_exit_codes_and_why_42_are_said():
    g = load()
    assert (g.ALIVE, g.ESCALATE, g.REFUSED) == (0, 42, 2)
    for words in ("sum of acknowledged gaps", "what the judge cannot evaluate"):
        assert words in g.__doc__


def test_a_bad_command_line_is_refused_never_run(tmp_path):
    g = load()
    for argv in (
        [],
        ["run"],
        ["run", "--box"],
        ["run", "--box", str(tmp_path), "--x", "1"],
    ):
        assert g.main(argv) == g.REFUSED
    assert not (tmp_path / "record.jsonl").exists()
