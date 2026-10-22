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


def test_among_equal_answers_the_newest_comes_first(tmp_path):
    """Odile's bees (demo/odile/STRIP.md): every inspection says "bees", and
    the one she means is the last. A log's date heading covers the lines
    under it, even across a chunk cut, and on one date the later line wins."""
    days = [f"== 2021-0{m}-10\n" + "H1: bees calm\n" * 30 for m in (3, 4)]
    days += [f"== 2026-0{m}-10\n" + "H1: bees calm\n" * 30 for m in (3, 4)]
    (tmp_path / "hives.txt").write_text("".join(days))
    (tmp_path / "notes.md").write_text("bees, no date\n")
    pile = k.chunks(tmp_path)
    got = k.rank(pile, "how are the bees")
    last = max(c["line"] for c in pile if c["path"] == "hives.txt")
    assert (got[0]["path"], got[0]["line"]) == ("hives.txt", last)
    assert "==" not in got[0]["text"]  # dated only by the heading above it
    assert k.dates(pile)[[c["line"] for c in pile].index(last)] == 20260410
    assert k.newest("2026-10-14 20261015 12026-10-16 2026-13-01") == "20261015"


def test_her_words_list_reads_her_word_as_her_folders(tmp_path):
    """Odile asks who's due; her tuning log says `next:` (demo/odile/STRIP.md).
    Her own list, kept like her key, reads one as the other, says so when
    it did, and the record keeps the list's hash, never its words."""
    pile, box, mine = tmp_path / "pile", tmp_path / "box", tmp_path / "mine"
    pile.mkdir()
    (pile / "log.txt").write_text("2026-10-16 Calvin\n  next: Oct 2027\n")
    mine.mkdir(mode=0o700)
    wl = mine / "words.txt"
    wl.write_text("# what I call things\ndue = next, overdue\n")
    wl.chmod(0o600)
    b = ["--box", str(box), "--now", "2026-10-22T00:00:00Z"]
    key = ["--key", str(mine / "k.hex")]
    assert (
        cli("init", *b, "--pile", str(pile), *key, "--words", str(wl)).returncode == 0
    )
    got = cli("bite", *b, "who's due")
    doc = json.loads(got.stdout)
    assert got.returncode == 0 and doc["served"][0]["where"] == "log.txt:1"
    assert {"why": "read due as next, from your words list"} in doc["look"]
    row = json.loads((box / "record.jsonl").read_text().splitlines()[-1])
    assert row["words_sha256"] == k.hashlib.sha256(wl.read_bytes()).hexdigest()
    assert "next" not in json.dumps(row) and "due" not in json.dumps(row)
    wl.chmod(0o644)  # readable by others: refused, nothing written
    before = (box / "record.jsonl").read_text()
    assert cli("bite", *b, "who's due").returncode == 2
    assert (box / "record.jsonl").read_text() == before


def test_a_words_list_line_is_one_word_then_words():
    assert k.parse_words("due = next, overdue\nDUE = Next") == (
        {"due": ["next", "overdue"]},
        None,
    )
    for bad in ("due next", "due =", "a b = c", "due = next,", "due = next soon"):
        assert k.parse_words(bad)[1] == (
            "words list line 1: one word = one or more words, a comma between"
        ), bad


def test_what_isnt_there_is_said_beside_what_was_served(tmp_path):
    """Odile's books name 21 of her 24 purchase orders (demo/odile/STRIP.md).
    Served an order, the kernel says which ones the books don't name. Things
    that only share a shape (a Kawai's K-300, a van's E-150) never are."""
    pos = [f"2025-{n:03d}" for n in range(10, 22)]
    (tmp_path / "books.csv").write_text(
        "".join(f"DEP PO {p}\n" for p in pos if p not in ("2025-013", "2025-019"))
    )
    (tmp_path / "calendar.txt").write_text(" ".join(f"PO {p}" for p in pos) + "\n")
    (tmp_path / "po.txt").write_text("PURCHASE ORDER PO 2025-010\n")
    (tmp_path / "piano.txt").write_text("Kawai K-300 order\n")
    (tmp_path / "van.txt").write_text("Econoline E-150 rider\n")
    pile = k.chunks(tmp_path)
    served = [c for c in pile if c["path"] in ("po.txt", "piano.txt")]
    assert k.missing(pile, served) == [
        {
            "why": "2025-013, 2025-019: not in books.csv",
            "where": "books.csv names 10 of the 12 like them",
        }
    ]
    books = [c for c in pile if c["path"] == "books.csv"]
    assert k.missing(pile, books) == []  # the file being read isn't checked
    assert k.ids("PO 2025-063 (575) 555-0161 2026-10-16 -x- 1/300") == [
        "2025-063",
        "555-0161",
        "1/300",
    ]
    assert k.shape("WR-2026-0117") == "AA-9999-9999"


def test_numbers_over_time_say_what_moved_and_never_judge(tmp_path):
    """Laszlo's care log (demo/odile): the latest week can sit under the
    cardiologist's 2 kg line while an earlier week crossed it, so both are
    said. A ledger repeats its dates and is never read as a series."""
    days = [("2026-10-0" + str(d) if d < 10 else f"2026-10-{d}") for d in range(1, 22)]
    kg = [71.2] * 8 + [71.3, 71.9, 72.3, 72.5, 72.8, 73.2, 73.4, 73.8, 74.0]
    kg += [74.3, 74.6, 75.0, 75.1]
    note = {"2026-10-16": '"ankles, puffy"'}
    log = "date,weight_kg,notes\n" + "".join(
        f"{d},{w:.1f},{note.get(d, '')}\n" for d, w in zip(days, kg)
    )
    assert k.series("dad/care-log.csv", log) == [
        {
            "why": "weight_kg: 75.1 on 2026-10-21, up 1.9 from 73.2 on 2026-10-14;"
            " the most in one week of the last 4: up 2.5, 2026-10-09 to 2026-10-16",
            "where": "dad/care-log.csv: 21 dated rows, 2026-10-01 to 2026-10-21",
        }
    ]
    ledger = "date,what,amount\n2026-10-01,a,5\n2026-10-01,b,6\n2026-10-09,c,7\n"
    assert k.series("books.csv", ledger) == []
    assert k.number("-0.30") == (-30, 2) and k.number("1.") is None
    assert k.day("20240307") - k.day("20240229") == 7


def test_a_folders_name_is_her_word(tmp_path):
    """ "Dad's weight": Laszlo's care log never says Dad, its folder does, and
    a client named Weight shouldn't outrank it (demo/odile, T1)."""
    (tmp_path / "dad").mkdir()
    (tmp_path / "dad" / "care-log.csv").write_text("date,weight_kg\n2026-10-21,75.1\n")
    (tmp_path / "books.csv").write_text("2026-06-08,check Harriet Weight,110\n")
    pile = k.chunks(tmp_path)
    assert k.rank(pile, "Dad's weight")[0]["path"] == "dad/care-log.csv"
    assert not any("dad" in h["why"] for h in k.look(pile, "Dad's weight", []))


def test_a_date_gone_by_with_nothing_since_is_said(tmp_path):
    """Who's due (demo/odile, N1): the latest entry under each heading whose
    next date has passed, with nothing written under it since. A log kept a
    file a year is read as one."""
    (tmp_path / "tuning").mkdir()
    (tmp_path / "tuning" / "2025.txt").write_text(
        "2025-05-02  Ann - upright\n  next: May 2026\n\n"
        "2025-06-01  Bo - grand\n  next: Jan 2026\n"
    )
    (tmp_path / "tuning" / "2026.txt").write_text(
        "2026-02-10  Bo - grand\n  next: Feb 2027\n\n"
        "2026-03-01  Cy - console\n  next: Dec. 1, 2026\n"
    )
    pile = k.chunks(tmp_path)
    served = [c for c in pile if c["path"] == "tuning/2026.txt"]
    assert k.gone_by(pile, served, "20261022", {"next"}, {}) == [
        {
            "why": "gone by, nothing written since: Ann - upright (May 2026)",
            "where": "tuning/: 1 of 3 headings, as of 2026-10-22",
        }
    ]
    assert k.gone_by(pile, served, "20260430", {"next"}, {}) == []
    # said only where a date's own line holds the bite's words (or her list's)
    assert k.gone_by(pile, served, "20261022", {"tired"}, {}) == []
    assert k.gone_by(pile, served, "20261022", {"due"}, {"due": ["next"]})
    assert k.written("next: Oct 2027; March 20, 2026; Octo 2026") == [
        ("20271001", "Oct 2027"),
        ("20260320", "March 20, 2026"),
    ]


def test_said_once_before_said_every_week_and_never_twice(tmp_path):
    """Odile's jar order, dictated every month, pushed out the van memo she
    still has to act on (demo/odile/STRIP.md): one text is one place, and on
    equal scores what was said once comes before what is routine."""
    (tmp_path / "memos").mkdir()
    for m in (3, 5, 7, 9):
        (tmp_path / "memos" / f"2026-0{m}-01.txt").write_text("need forty jars\n")
    (tmp_path / "memos" / "2021-05-14.txt").write_text("need to call the insurer\n")
    (tmp_path / "memos" / "2026-03-20.txt").write_text("you would need a permit\n")
    got = [c["path"] for c in k.rank(k.chunks(tmp_path), "what do I need to do")]
    assert got == [
        "memos/2026-03-20.txt",
        "memos/2021-05-14.txt",
        "memos/2026-09-01.txt",
    ]
