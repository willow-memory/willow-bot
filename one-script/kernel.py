"""kernel.py: the smallest script that runs the whole loop, for anyone. Python 3.9+, stdlib only.

    python kernel.py init    --box DIR --pile FOLDER --key FILE
    python kernel.py bite    --box DIR "your words"
    python kernel.py propose --box DIR --file ROWS.jsonl
    python kernel.py seal    --box DIR proposal:<sha256>
    python kernel.py verify  --box DIR
    python kernel.py out     --box DIR
    python kernel.py test

The operator, 2026-10-22: "the smallest script the system can run for any
native user, so their first bite isn't filled with mine." Its pile is the
person's own folder and nothing else: no law, no corpus, no ledger, no key but
theirs. It was found, not designed: merged into one box, every demo in the
fleet (Nestor's, the Grove's, the Forge's, 24 Python demos in 9 repos) turned
out to be the same small loop in costume (add a pair, look it up, serve it only
when a human sealed it, keep every step on a chain), and Nestor's
`demo/sixty_seconds.py` tells it in eight beats. `test` runs those eight beats.

The loop:
    init     a box, and a key that is theirs (a file outside the box, 0600)
    bite     their words against their own files. A sealed answer for these
             words is served (exit 0: a human said it). Else their files'
             chunks that hold the words are served (exit 0: their pile is
             things they already said yes to); a chunk that only asks the
             question back is marked `echo` and answers nothing. Else nothing
             (exit 42). Pages are read as the text they show
    propose  a model's rows, one JSON object a line: {path, data, cites,
             claim}. Through the door: a plain relative path, a claim that says
             why, and cites only from what the last bite served. A pass waits
             on the person (exit 42); nothing is written
    seal     the person's proof over one proposal's exact hash, made from their
             key. Only then is it written, and its bite is answered
    verify   the chain, every row, and every seal's proof re-checked: a forged
             seal is never served, whatever it claims
    out      what is waiting on the person (42 while anything is)

The rows are the one script's own (`onescript/record.py`): the same keys, the
same canon (family A), hash = SHA-256 of the row without `hash`, GENESIS to
start, so `Record.verify_chain` checks a kernel box unchanged. Its record
holds hashes and places, never the person's words. Its seal is
`onescript/sign.py`'s (`hmac_parts(key, "seal", subject)`), and its bite key is
`onescript/words.py`'s (`bite_key`), byte for byte. Built on `genesis.py`
(willow-bot `claude/genesis`): the lock, the fsync, the torn tail refused, an
edit seen.

Exit codes, genesis's:
    0   a human said it: their pile holds it, or their seal does
    42  not enough information to say a human said this was true
    2   refused: a bad command line, a box that isn't theirs, a chain that
        doesn't verify. Nothing is appended past a break.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no advisory lock, one writer at a time by hand
    fcntl = None  # type: ignore[assignment]

GENESIS = "0" * 64
ALIVE, ESCALATE, REFUSED = 0, 42, 2
ROW_KEYS = {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}
VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
WORD = re.compile(r"[^\W_]+")  # onescript/words.py, the key rule (v1)
TOP = 3  # chunks served for a bite
HOLD = 0.5  # the share of a bite's words a chunk must hold; the operator's number
#: Words that never count toward holding a bite: "how do I bake sourdough
#: bread" is not held by a chunk that says "how" and "do". English, and a
#: placeholder list, the operator's to name; a bite in another language keeps
#: every word.
FUNCTION = frozenset(
    "a an the and or but if of to in on at by for from with about as into than"
    " then so is are was were be been being am do does did done have has had i"
    " me my we our you your he she it its they them their this that these those"
    " what which who whom whose when where why how much many can could would"
    " should will shall may might must not no yes there here s t".split()
)
SNIFF = 8192  # bytes looked at to tell text from binary: a NUL byte means binary
MAX_FILE = 1_000_000  # bytes read from one file of the pile
MAX_CHUNK = 500  # characters in one chunk: the one script's pile.MAX


# ── the record: genesis's, in the one script's format ───────────────────────
def canon(row: dict) -> str:
    """Family A, byte for byte (`onescript/canon.py` sorted_compact_utf8)."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def h256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read(path: Path) -> tuple[list[dict], list[str]]:
    """(rows, breaks). Every break is said; an empty list means the chain holds."""
    if not path.exists():
        return [], []
    data = path.read_bytes().decode("utf-8")
    if data and not data.endswith("\n"):
        return [], ["torn tail: the last line is unterminated; a human looks first"]
    rows, breaks, prev = [], [], GENESIS
    # "\n" only, as onescript/record.py reads it: canon keeps a raw U+2028,
    # U+2029 or U+0085 in a string (a file name in a served `where`), and
    # str.splitlines() would cut the row there and brick the box
    lines = data.split("\n")[:-1] if data else []
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict) or not ROW_KEYS <= row.keys():
            breaks.append(f"line {i}: garbled, not a record row")
            continue
        body = {k: v for k, v in row.items() if k != "hash"}
        if row["prev"] != prev or h256(canon(body)) != row["hash"]:
            breaks.append(f"row {row['n']}: chain broken")
        if isinstance(row["n"], bool) or row["n"] != len(rows):
            breaks.append(f"row {row['n']!r}: out of place, it is row {len(rows)}")
        rows.append(row)
        prev = row["hash"]
    return rows, breaks


def append(box: Path, ts: str, kind: str, who: str = "kernel", **fields) -> dict:
    """One row on the chain, under a lock, fsynced with its directory."""
    path = box / "record.jsonl"
    with path.open("a+", encoding="utf-8", newline="") as f:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        rows, breaks = read(path)  # the true tail, read under the lock
        if breaks:
            raise RuntimeError("; ".join(breaks))
        sealed = kind == "seal" and who == "human"
        row = {"n": len(rows), "kind": kind, "who": who, "family": "kernel"}
        row.update(standing="sealed" if sealed else "unattested", version=VERSION)
        row.update(ts=ts, where=fields.pop("where", None), **fields)
        row["prev"] = rows[-1]["hash"] if rows else GENESIS
        row["hash"] = h256(canon(row))
        f.write(canon(row) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if row["n"] == 0 and hasattr(os, "O_DIRECTORY"):  # the new name survives a crash
        d = os.open(box, os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    return row


# ── the seal and the bite key: the one script's, byte for byte ──────────────
def proof(key: bytes, subject: str) -> str:
    """`onescript/sign.py` hmac_parts(key, "seal", subject)."""
    return hmac.new(
        key, "\x1f".join(("seal", subject)).encode(), hashlib.sha256
    ).hexdigest()


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def bite_key(text: str) -> str | None:
    """`onescript/words.py` bite_key: the words in order, one space between."""
    ws = words(text)
    return h256(" ".join(ws)) if ws else None


def subject_of(prop: dict) -> str:
    keys = ("path", "data", "cites", "claim")
    return "proposal:" + h256(canon({k: prop[k] for k in keys}))


# ── the box: theirs ─────────────────────────────────────────────────────────
def config(box: Path) -> dict:
    return json.loads((box / "kernel.json").read_text(encoding="utf-8"))


def read_key(path: Path, box: Path) -> tuple[bytes | None, str | None]:
    """Their key: hex in a file outside the box, readable only by them."""
    if path.resolve().is_relative_to(box.resolve()):
        return None, "the key can't live inside the box"
    try:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            return None, f"{path} is readable beyond its owner; chmod 600 it"
        return bytes.fromhex(path.read_text(encoding="utf-8").strip()), None
    except (OSError, ValueError) as e:
        return None, f"the key could not be read: {type(e).__name__}"


def init(box: Path, pile: Path, key: Path, ts: str) -> int:
    if (box / "record.jsonl").exists():
        print("refused: this box already has a record", file=sys.stderr)
        return REFUSED
    if not pile.is_dir():
        print(f"refused: {pile} is not a folder", file=sys.stderr)
        return REFUSED
    if key.resolve().is_relative_to(box.resolve()):
        print("refused: the key can't live inside the box", file=sys.stderr)
        return REFUSED
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
    box.mkdir(parents=True, exist_ok=True)
    cfg = {"pile": str(pile.resolve()), "key": str(key.resolve())}
    (box / "kernel.json").write_text(json.dumps(cfg, sort_keys=True) + "\n")
    row = append(box, ts, "boot", loop="open", pile_sha256=h256(cfg["pile"]))
    print(f"open loop, row {row['n']} {row['hash'][:16]}: the box is yours")
    return ALIVE


# ── the pile: their own folder, chunked by paragraph ────────────────────────
_HIDDEN = re.compile(r"<(script|style)\b.*?</\1\s*>|<!--.*?-->", re.S | re.I)
_TAG = re.compile(r"<[^>]*>", re.S)


def _lines_kept(m: re.Match) -> str:
    return " " + "\n" * m.group(0).count("\n")  # line numbers still point home


def html_text(text: str) -> str:
    """The words a page shows: scripts, styles, comments and tags out, entities
    read, every line where it was. A first bite on a page is never served
    `<button>` and `<!doctype>` (Nieves's shoebox, June's farm)."""
    return html.unescape(_TAG.sub(_lines_kept, _HIDDEN.sub(_lines_kept, text)))


def holds(w: str, toks: set[str]) -> bool:
    """A word holds where it is a token; one with a digit in it (a VIN, a
    serial, a case number) also holds where a token starts or ends with it:
    "that one ending 672841" finds 1HGEJ6672841 (Big Jim's lot)."""
    if w in toks:
        return True
    return (
        len(w) >= 4
        and any(c.isdigit() for c in w)
        and any(t.endswith(w) or t.startswith(w) for t in toks)
    )


ECHO_RUN = 8  # words in a row, in the bite's order, that make a chunk an echo


def echo(bite: str, text: str) -> bool:
    """Whether `text` holds the bite's own words in its own order: the whole
    bite, or `ECHO_RUN` of its words in a row (a long opener never fits in one
    chunk whole). The question asked back is not its answer (the Dispatches
    audit's trap: "a question nobody audited must NOT be answered")."""
    ws, ts = words(bite), words(text)
    n = min(len(ws), ECHO_RUN)
    if n < 3:
        return False
    runs = {tuple(ts[i : i + n]) for i in range(len(ts) - n + 1)}
    return any(tuple(ws[i : i + n]) in runs for i in range(len(ws) - n + 1))


def chunks(pile: Path) -> list[dict]:
    """Every paragraph of every text file under `pile` (any name; UTF-8 with no
    NUL byte in its first `SNIFF` bytes), cut into runs of at most
    `MAX_CHUNK` characters (a file with no blank lines would otherwise be one
    chunk holding every word, served for any bite): where it starts, its text,
    its SHA-256. Hidden folders, binaries and anything not UTF-8 are left out."""
    out = []
    for p in sorted(pile.rglob("*")):
        rel = p.relative_to(pile)
        if not p.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        try:  # text by what it holds, not by its name: a newcomer's TODO,
            # deploy.bat and login.php are theirs to ask about
            if p.stat().st_size > MAX_FILE or b"\0" in (raw := p.read_bytes())[:SNIFF]:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if p.suffix.lower() in (".html", ".htm"):
            text = html_text(text)
        para, start, size = [], 1, 0
        for i, line in enumerate(text.splitlines() + [""], 1):
            if para and (not line.strip() or size + len(line) > MAX_CHUNK):
                body = "\n".join(para)  # a paragraph, or a run of one under the cap
                out.append(
                    {"path": str(rel), "line": start, "text": body, "id": h256(body)}
                )
                para, size = [], 0
            if line.strip():
                if not para:
                    start = i
                para.append(line[:MAX_CHUNK])
                size += len(para[-1]) + 1
    return out


def rank(pile: list[dict], bite: str) -> list[dict]:
    """The chunks holding at least `HOLD` of the bite's content words (not
    `FUNCTION`), rarest words weighing most. A chunk sharing only "the" or
    "how" and "do" doesn't hold "when does the frost come" or "how do I bake
    sourdough bread": an answer it doesn't have is never improvised (beat 1).
    A bite of function words alone is held by nothing: say more."""
    want = set(words(bite)) - FUNCTION
    if not want:
        return []
    held = [(c, {w for w in want if holds(w, set(words(c["text"])))}) for c in pile]
    held = [(c, h) for c, h in held if len(h) >= HOLD * len(want)]
    df = {w: sum(w in h for _, h in held) for w in want}
    scored = [
        (sum(1 / df[w] for w in h), c["path"], c["line"], c) for c, h in held if h
    ]
    return [c for *_, c in sorted(scored, key=lambda s: (-s[0], s[1], s[2]))[:TOP]]


# ── where to look: what a 42 (or a served definition) can still say ─────────
# Facts about their folder first; then, at most, which kind of tool does the
# rest. A newcomer is told where the answer could be, never handed a guess.
DEFINES = re.compile(r"\b(?:def|function|sub|class|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)")
MAKE = {"make", "build", "write", "create", "generate"}
NEXT = {
    "other words": "a meaning match (the fleet's: Nestor's semantic_matcher)",
    "unused": "a call graph or a code search (the fleet's: script_match)",
    "made": "a builder (the fleet's: the Forge, forge_bite)",
}


def look(pile: list[dict], bite: str, served: list[dict]) -> list[dict]:
    """Where to look, from what the folder holds: the bite's words no file
    has; each function a served chunk defines, and whether anything else in
    the folder names it (text can show a definition, not that nothing calls
    it); a bite of common words only; a request to make something."""
    out = []
    ws = words(bite)
    want = [w for w in dict.fromkeys(ws) if w not in FUNCTION]
    if not want:
        out.append({"why": "every word is a common one: name the thing you mean"})
    every = set()
    for c in pile:
        every |= set(words(c["text"]))
    absent = [w for w in want if not holds(w, every)]
    if absent:
        out.append(
            {
                "why": "no file in your folder has " + ", ".join(absent),
                "where": "it may be called something else there",
                "next": NEXT["other words"],
            }
        )
    for c in served:
        for name in dict.fromkeys(DEFINES.findall(c["text"])):
            named = re.compile(rf"\b{re.escape(name)}\b")
            uses = sorted(
                {d["path"] for d in pile if d is not c and named.search(d["text"])}
                | ({c["path"]} if len(named.findall(c["text"])) > 1 else set())
            )
            out.append(
                {
                    "why": f"{name} is defined at {c['path']}:{c['line']}",
                    "where": "also named in " + ", ".join(uses)
                    if uses
                    else "nothing else in your folder names it",
                    "next": NEXT["unused"],
                }
            )
    if ws and ws[0] in MAKE:
        out.append({"why": "this asks for something made", "next": NEXT["made"]})
    return out


# ── the loop ────────────────────────────────────────────────────────────────
def sealed(box: Path, rows: list[dict]) -> dict[str, dict]:
    """Each proposal a seal row holds, by subject, only while its proof holds
    under the person's key. A forged seal is in neither: it is reported by
    `verify`, and never served."""
    key, _ = read_key(Path(config(box)["key"]), box)
    out = {}
    for r in rows:
        if r["kind"] == "seal" and key is not None:
            if hmac.compare_digest(
                proof(key, r.get("subject", "")), str(r.get("proof"))
            ):
                out[r["subject"]] = r
    return out


def bite(box: Path, text: str, ts: str) -> int:
    rows, breaks = read(box / "record.jsonl")
    if breaks:
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return REFUSED
    k = bite_key(text)
    if k is None:
        print("refused: a bite with no words", file=sys.stderr)
        return REFUSED
    for subj in sealed(box, rows):  # a sealed answer for these words, first
        prop = json.loads((box / "proposals" / f"{subj[9:]}.json").read_text())
        if prop.get("bite_key") == k:
            append(box, ts, "bite", bite_key=k, answered_by=subj, served=[])
            print(json.dumps({"state": "sealed", "answer": prop["data"], "by": subj}))
            return ALIVE
    pile = chunks(Path(config(box)["pile"]))
    served = rank(pile, text)
    refs = [
        {
            "id": c["id"],
            "where": f"{c['path']}:{c['line']}",
            "echo": echo(text, c["text"]),
        }
        for c in served
    ]
    append(box, ts, "bite", bite_key=k, served=refs)
    answers = [r for r in refs if not r["echo"]]
    state = "populated" if answers else "echo" if refs else "empty"
    doc = {"state": state, "bite_key": k}
    doc["served"] = [{**r, "text": c["text"]} for r, c in zip(refs, served)]
    if hints := look(pile, text, served if answers else []):
        doc["look"] = hints  # printed for them; the record keeps no words
    print(json.dumps(doc, ensure_ascii=False))
    return ALIVE if answers else ESCALATE


def door(prop: object, served: set[str]) -> str | None:
    """Why a proposed row doesn't pass, or None."""
    keys = {"path", "data", "cites", "claim"}
    if not isinstance(prop, dict) or set(prop) != keys:
        return "a row is exactly {path, data, cites, claim}"
    p = prop["path"]
    if (
        not isinstance(p, str)
        or not p
        or p.startswith(("/", "\\"))
        or ".." in Path(p).parts
    ):
        return "a path is plain and relative, inside the box"
    if not isinstance(prop["data"], str) or not isinstance(prop["cites"], list):
        return "data is text and cites a list"
    if not isinstance(prop["claim"], str) or not prop["claim"].strip():
        return "a claim says why"
    if not set(prop["cites"]) <= served:
        return "a cite that the last bite didn't serve"
    return None


def propose(box: Path, lines: list[str], ts: str) -> int:
    rows, breaks = read(box / "record.jsonl")
    if breaks:
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return REFUSED
    last = next((r for r in reversed(rows) if r["kind"] == "bite"), None)
    if last is None:
        print("refused: propose answers a bite; bite first", file=sys.stderr)
        return REFUSED
    served = {s["id"] for s in last.get("served", [])}
    waiting = 0
    for i, line in enumerate(lines):
        try:
            prop = json.loads(line)
        except ValueError:
            prop = None
        why = door(prop, served)
        if why is None:
            subj = subject_of(prop)
            (box / "proposals").mkdir(exist_ok=True)
            kept = {**prop, "bite_key": last["bite_key"]}
            (box / "proposals" / f"{subj[9:]}.json").write_text(json.dumps(kept))
            append(
                box, ts, "proposal", subject=subj, verdict="pass", bite_row=last["n"]
            )
            print(f"pass  {subj}  waiting on your seal")
            waiting += 1
        else:
            append(box, ts, "proposal", verdict="refused", line=i, why=why)
            print(f"refused  line {i}: {why}")
    return ESCALATE if waiting else ALIVE


def seal(box: Path, subj: str, ts: str) -> int:
    rows, breaks = read(box / "record.jsonl")
    if breaks:
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return REFUSED
    if not any(r["kind"] == "proposal" and r.get("subject") == subj for r in rows):
        print(f"refused: {subj} is not a proposal in this box", file=sys.stderr)
        return REFUSED
    key, why = read_key(Path(config(box)["key"]), box)
    if key is None:
        print(f"refused: {why}", file=sys.stderr)
        return REFUSED
    prop = json.loads((box / "proposals" / f"{subj[9:]}.json").read_text())
    if subject_of(prop) != subj:
        print(
            "refused: the proposal's bytes changed since it was proposed",
            file=sys.stderr,
        )
        return REFUSED
    append(box, ts, "seal", who="human", subject=subj, proof=proof(key, subj))
    out = box / "written" / prop["path"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prop["data"], encoding="utf-8")
    print(f"sealed {subj}; written to {out.relative_to(box)}")
    return ALIVE


def verify(box: Path) -> int:
    rows, breaks = read(box / "record.jsonl")
    if breaks:
        print("\n".join(breaks))
        return REFUSED
    good = sealed(box, rows)
    forged = [
        r["n"] for r in rows if r["kind"] == "seal" and r.get("subject") not in good
    ]
    if forged:
        print(f"the chain holds; seal rows {forged} don't prove under your key")
        return ESCALATE
    print(f"the chain holds: {len(rows)} rows, {len(good)} sealed")
    return ALIVE


def out(box: Path, ts: str) -> int:
    rows, breaks = read(box / "record.jsonl")
    if breaks:
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return REFUSED
    good = sealed(box, rows)
    waiting = [
        r["subject"] for r in rows
        if r["kind"] == "proposal" and r.get("verdict") == "pass" and r["subject"] not in good
    ]  # fmt: skip
    append(
        box, ts, "out", waiting=len(waiting), tip=rows[-1]["hash"] if rows else GENESIS
    )
    for s in waiting:
        print(f"waiting on your seal: {s}")
    print(f"{len(rows) + 1} rows; {len(good)} sealed; {len(waiting)} waiting")
    return ESCALATE if waiting else ALIVE


# ── the eight beats (Nestor's demo/sixty_seconds.py), as the test ───────────
def test() -> None:
    """Runs in a temporary directory, never in a box of yours."""
    import contextlib
    import io

    def quiet(fn, *a):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            with contextlib.redirect_stderr(io.StringIO()):
                return fn(*a), o.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        t, ts = Path(tmp), "2026-10-22T00:00:00Z"
        pile, box, key = t / "pile", t / "box", t / "keys" / "me.key"
        pile.mkdir()
        (pile / "garden.md").write_text(
            "Tomatoes want six hours of sun.\n\nWater at the root.\n"
        )
        assert quiet(init, box, pile, key, ts)[0] == ALIVE
        (pile / "big.json").write_text(
            "\n".join(f'"k{i}": "word{i} of {i}"' for i in range(400))
        )
        # 1. asked what nobody here has said: it says so, it doesn't improvise,
        #    and a long file with no blank line doesn't answer everything
        assert quiet(bite, box, "when does the frost come", ts)[0] == ESCALATE
        assert quiet(bite, box, "how do I bake sourdough bread", ts)[0] == ESCALATE
        assert quiet(bite, box, "what does this do", ts)[0] == ESCALATE  # say more
        # what the demos' characters needed: a page read as text, a serial by
        # its ending, and a question asked back not taken for its answer
        (pile / "lot.html").write_text(
            "<html><script>var lot = 1;</script>\n<p>The blue Civic,"
            " VIN 1HGEJ6672841, one owner.</p></html>\n"
        )
        code, said = quiet(bite, box, "the one ending 672841", ts)
        hit = json.loads(said)["served"][0]
        assert (
            code == ALIVE and hit["where"] == "lot.html:2" and "<p>" not in hit["text"]
        )
        (pile / "faq.md").write_text("Has the shed been painted this year?\n")
        code, said = quiet(bite, box, "has the shed been painted this year", ts)
        assert code == ESCALATE and json.loads(said)["state"] == "echo"
        (pile / "shed.md").write_text(
            "Has the shed out back been painted this year or not, and by whom?\n"
        )
        long = "so I was out back this morning and wondered: has the shed out back been painted this year"
        code, said = quiet(bite, box, long, ts)  # a long opener holding the question
        assert code == ESCALATE and json.loads(said)["state"] == "echo"
        assert all(len(c["text"]) <= MAX_CHUNK for c in chunks(pile))
        # their own pile answers what it holds (things they already said yes to)
        code, said = quiet(bite, box, "how much sun do tomatoes want", ts)
        assert code == ALIVE and "six hours" in said
        cite = json.loads(said)["served"][0]["id"]
        row = {
            "path": "notes/sun.md",
            "data": "Six hours.",
            "cites": [cite],
            "claim": "from garden.md",
        }
        f = t / "rows.jsonl"
        f.write_text(
            json.dumps(row) + "\n" + json.dumps({**row, "cites": ["nope"]}) + "\n"
        )
        code, said = quiet(propose, box, f.read_text().splitlines(), ts)
        assert code == ESCALATE and "refused  line 1" in said  # a cite not served
        subj = subject_of(row)
        # 2. the person verifies it, once, with their own key
        assert quiet(seal, box, subj, ts)[0] == ALIVE
        assert (box / "written" / "notes/sun.md").read_text() == "Six hours."
        # 3. the same question, retyped differently, is served as sealed
        code, said = quiet(bite, box, "How much SUN do tomatoes want?!", ts)
        assert code == ALIVE and json.loads(said)["by"] == subj
        # 4. a rewrite with other words is not served as sealed
        code, said = quiet(bite, box, "tomatoes want how much sun", ts)
        assert "sealed" not in json.loads(said)["state"]
        # 5. an unsealed proposal is not an answer: out says it waits
        code, said = quiet(bite, box, "water them at the root?", ts)
        water = json.loads(said)["served"][0]["id"]
        unsealed = {
            **row,
            "path": "notes/water.md",
            "data": "At the root.",
            "cites": [water],
        }
        f.write_text(json.dumps(unsealed) + "\n")
        assert quiet(propose, box, f.read_text().splitlines(), ts)[0] == ESCALATE
        code, said = quiet(out, box, ts)
        assert code == ESCALATE and subject_of(unsealed) in said
        # 6. a forged seal (the right shape, the chain re-hashed) is never served
        fake = subject_of(unsealed)
        append(box, ts, "seal", who="human", subject=fake, proof="0" * 64)
        assert fake not in sealed(box, read(box / "record.jsonl")[0])
        assert quiet(verify, box)[0] == ESCALATE
        # 7. the chain verifies, every step on it
        rows, breaks = read(box / "record.jsonl")
        assert not breaks and [r["n"] for r in rows] == list(range(len(rows)))
        # 8. one field of one past row edited: the chain refuses, and so does the next bite
        path = box / "record.jsonl"
        path.write_bytes(
            path.read_bytes().replace(b'"verdict":"pass"', b'"verdict":"fail"', 1)
        )
        assert quiet(verify, box)[0] == REFUSED
        assert quiet(bite, box, "how much sun do tomatoes want", ts)[0] == REFUSED
    print("kernel: the eight beats held")


# ── the command line ────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if argv[:1] == ["test"]:
        test()
        return ALIVE
    verb, rest = (argv[0], argv[1:]) if argv else ("", [])
    opts: dict[str, str] = {}
    args: list[str] = []
    it = iter(rest)
    for a in it:
        if a in ("--box", "--pile", "--key", "--file", "--now"):
            opts[a] = next(it, "")
        else:
            args.append(a)
    if "--box" not in opts or not opts["--box"]:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return REFUSED
    box, ts = Path(opts["--box"]), opts.get("--now") or now()
    if verb == "init" and opts.get("--pile") and opts.get("--key") and not args:
        return init(box, Path(opts["--pile"]), Path(opts["--key"]), ts)
    if not (box / "kernel.json").exists():
        print("refused: not a kernel box; init first", file=sys.stderr)
        return REFUSED
    if verb == "bite" and len(args) == 1:
        return bite(box, args[0], ts)
    if verb == "propose" and opts.get("--file") and not args:
        return propose(
            box, Path(opts["--file"]).read_text(encoding="utf-8").splitlines(), ts
        )
    if verb == "seal" and len(args) == 1 and args[0].startswith("proposal:"):
        return seal(box, args[0], ts)
    if verb == "verify" and not args:
        return verify(box)
    if verb == "out" and not args:
        return out(box, ts)
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
