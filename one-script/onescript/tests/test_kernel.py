"""kernel.py, the smallest script for anyone: its eight beats hold, and it is
still the one script's record, seal and bite key, byte for byte, so the one
script can check a kernel box and a kernel box can grow into a one-script box."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
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


def test_a_strangers_files_are_read_by_what_they_hold_not_their_names(tmp_path):
    """quick-stupids' the-box: the answers lived in TODO, login.php, deploy.bat,
    error_log and a .doc that is plain text, none of them on a list of suffixes."""
    pile = tmp_path / "pile"
    pile.mkdir()
    (pile / "TODO").write_text("TODO: pick a license -- rm, 3/9/1998\n")
    (pile / "deploy.bat").write_text("REM run the deploy from daves machine\r\n")
    (pile / "TEST_PROCEDURE.doc").write_text("compare the screens by eye\n")
    (pile / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0\0\0license deploy\0")
    got = {c["path"] for c in k.chunks(pile)}
    assert got == {"TODO", "deploy.bat", "TEST_PROCEDURE.doc"}  # a NUL is binary


def test_a_42_says_where_to_look():
    pile = [
        {"path": "login.php", "line": 3, "text": "function check_login($u) { }"},
        {"path": "app.py", "line": 1, "text": "def serve():\n    return 1"},
        {"path": "main.py", "line": 9, "text": "serve()"},
    ]
    why = [h["why"] for h in k.look(pile, "where is the database", [])]
    assert why == ["no file in your folder has database"]
    defs = {h["why"]: h["where"] for h in k.look(pile, "login serve", pile[:2])}
    assert defs == {
        "check_login is defined at login.php:3": "nothing else in your folder names it",
        "serve is defined at app.py:1": "also named in main.py",
    }
    assert k.look(pile, "what does this do", [])[0]["why"].startswith("every word")
    made = k.look(pile, "make a login page", [])
    assert made[-1]["why"] == "this asks for something made"


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


# The kernel holds a newcomer's own folder and never dials out. The scan is
# homestead-ledger's (I-17: "a money ledger must never dial out"), itself
# ported from marching-arts: no network module, and no eval/exec/compile/
# __import__ that could smuggle one past the import scan.
NET = {
    "socket",
    "ssl",
    "urllib",
    "http",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib3",
    "socketserver",
    "ftplib",
    "telnetlib",
    "smtplib",
    "xmlrpc",
    "poplib",
    "imaplib",
    "nntplib",
    "asyncio",
}
DYNAMIC = {"eval", "exec", "compile", "__import__"}


def reaches(source: str) -> set[str]:
    """The network modules a source imports, and the dynamic calls it makes."""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names} & NET
        elif isinstance(node, ast.ImportFrom) and node.module:
            out |= {node.module.split(".")[0]} & NET
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out |= {node.func.id} & DYNAMIC
    return out


def test_it_never_dials_out():
    assert reaches(HERE.read_text(encoding="utf-8")) == set()
    planted = (
        "import json\ndef f(u):\n    from urllib import request\n    return eval(u)\n"
    )
    assert reaches(planted) == {"urllib", "eval"}


def test_a_file_name_with_a_line_separator_doesnt_brick_the_box(tmp_path):
    """A served `where` is a file name, kept raw (canon is ensure_ascii=False):
    U+2028, U+2029 and U+0085 in it must not cut the row when it's read back,
    the way onescript/record.py reads it ("\\n" only)."""
    pile, box, key = tmp_path / "pile", tmp_path / "box", tmp_path / "keys" / "me.key"
    pile.mkdir()
    for sep in (" ", " ", "\u0085"):
        (pile / f"tomato{sep}notes.md").write_text("Tomatoes want six hours of sun.\n")
    b = ["--box", str(box), "--now", "2026-10-22T00:00:00Z"]
    assert cli("init", *b, "--pile", str(pile), "--key", str(key)).returncode == 0
    assert cli("bite", *b, "how much sun do tomatoes want").returncode == 0
    assert cli("verify", *b).returncode == 0
    rec = record.Record(box, lambda: "", "", object())  # the one script reads it too
    assert rec.verify_chain() == [] and [r["kind"] for r in rec.rows()][-1] == "bite"
    assert cli("bite", *b, "how much sun do tomatoes want").returncode == 0


#: A pile found by search (2026-10-21) where the float score's last bit, and so
#: the order of two chunks, followed the hash seed on Python 3.11: 13 runs in
#: 400 ranked it 11, 9, 5 instead of 11, 5, 9.
SEED_PILE = [
    "w1 w6",
    "w3 w1 w0 w2",
    "w4 w2 w0 w5 w3",
    "w2 w3 w0 w4 w1",
    "w4 w1 w3 w5",
    "w2 w6 w1 w3 w0",
    "w5 w0 w2 w1",
    "w5 w1 w0",
    "w2 w4 w1",
    "w3 w6 w1 w4 w5 w2",
    "w5 w3 w6",
    "w1 w2 w3 w6 w0 w4",
    "w5 w2 w4",
]
SEED_BITE = "w4 w2 w6 w1 w3 w0"


def test_the_rank_is_the_same_under_every_hash_seed():
    """Scores are exact (Fraction), so the set's walk order can't move them."""
    pile = [
        {"path": f"f{i:02d}.md", "line": 1, "text": t, "id": str(i)}
        for i, t in enumerate(SEED_PILE)
    ]
    code = (
        "import json,sys,importlib.util as u;"
        f"s=u.spec_from_file_location('k',{str(HERE)!r});k=u.module_from_spec(s);"
        "s.loader.exec_module(k);"
        "print(','.join(c['id'] for c in k.rank(json.loads(sys.argv[1]),sys.argv[2])))"
    )
    # the old Pythons are where a float sum walks in set order: use any here
    pys = [sys.executable] + [
        p for v in ("3.9", "3.10", "3.11") if (p := shutil.which(f"python{v}"))
    ]
    got = {
        subprocess.run(
            [py, "-c", code, json.dumps(pile), SEED_BITE],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": str(seed)},
        ).stdout.strip()
        for seed in (0, 80, 101, 102, 126, 156)
        for py in pys
    }
    assert got == {"11,5,9"}
