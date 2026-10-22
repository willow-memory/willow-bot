"""Hold a port to kernel.py: every vector, whole sessions, and each other's boxes.

    python3 compare.py [--port java] [--keep DIR]

Three checks, each said as a count; "unexplained" is the number to read, as
in `foundation/compare.py`:

1. **vectors.** The port answers `cases.json` (`<port> vectors cases.json
   results.json`); every answer is lined up against kernel.py's.
2. **sessions.** The same scripted session (init, bites, proposals, a seal,
   out, verify, a forged seal, an edit) run through kernel.py's command line
   and the port's, on the same paths with the same key, so each box is
   directly comparable: exit codes, what the person was shown, kernel.json,
   proposal files, and every record row but `version`, `hash` and `prev`
   (`version` is each implementation's own hash, so the chain hashes differ
   by design).
3. **both ways.** Each box is verified by the other implementation: a box
   the port wrote verifies under kernel.py, and kernel.py's under the port.

Exit 0 when nothing is unexplained, 1 otherwise. Needs a JDK (`javac`,
`java`) for the Java port, and a UTF-8 locale: the JVM can't name a
non-ASCII file without one (it runs here with LC_ALL=C.UTF-8).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KERNEL = HERE.parent / "kernel.py"
_spec = importlib.util.spec_from_file_location("kernel", KERNEL)
k = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k)

TS = "2026-10-22T00:00:00Z"
KEY_HEX = "4b" * 32  # a throwaway, the same for both sides so their seals match
ENV = {**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


# ── the ports ────────────────────────────────────────────────────────────────
class Java:
    name = "java"

    def __init__(self, work: Path):
        self.out = work / "java-out"
        src = HERE / "java" / "Kernel.java"
        got = subprocess.run(
            ["javac", "-nowarn", "-d", str(self.out), str(src)],
            capture_output=True,
            text=True,
            env=ENV,
        )
        if got.returncode != 0:
            raise SystemExit(f"javac failed:\n{got.stderr}")

    def argv(self, *args: str) -> list[str]:
        tables = f"-Dkernel.tables={HERE / 'kernel_tables.json'}"
        return ["java", tables, "-cp", str(self.out), "Kernel", *args]


class Python:
    name = "kernel.py"

    def argv(self, *args: str) -> list[str]:
        return [sys.executable, str(KERNEL), *args]


def run(impl, *args: str) -> tuple[int, str]:
    got = subprocess.run(impl.argv(*args), capture_output=True, text=True, env=ENV)
    out = "\n".join(x for x in got.stdout.splitlines() if not x.startswith("Picked up"))
    return got.returncode, out


# ── 1. vectors ───────────────────────────────────────────────────────────────
PARTS = {
    "canon": ["canon", "sha256"],
    "words": ["words", "bite_key"],
    "html": ["out"],
    "holds": ["out"],
    "echo": ["out"],
    "chunks": ["chunks"],
    "rank": ["ids"],
    "look": ["look"],
    "door": ["why"],
    "subject": ["subject"],
}


def vectors(port, work: Path) -> int:
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    results = work / "results.json"
    code, out = run(port, "vectors", str(HERE / "cases.json"), str(results))
    if code != 0:
        print(f"  the port's vectors run failed (exit {code})\n{out}")
        return 1
    got = json.loads(results.read_text())
    flat = {**{p: (cases[p], got[p], ks) for p, ks in PARTS.items()}}
    flat["hmac rfc4231"] = (cases["hmac"]["rfc4231"], got["hmac"]["rfc4231"], ["mac"])
    flat["hmac seal"] = (cases["hmac"]["seal"], got["hmac"]["seal"], ["proof"])
    bad_total = 0
    for part, (want, have, keys) in flat.items():
        bad = [
            i
            for i, (w, h) in enumerate(zip(want, have))
            if any(w[x] != h.get(x) for x in keys)
        ]
        bad += list(range(len(have), len(want)))  # answers that never came
        bad_total += len(bad)
        mark = "ok" if not bad else f"UNEXPLAINED {len(bad)}, first at {bad[:3]}"
        print(f"  {part:<13} {len(want) - len(bad):>5}/{len(want):<5} {mark}")
        for i in bad[:2]:
            print(
                f"      case {i}: want {json.dumps({x: want[i][x] for x in keys})[:200]}"
            )
            if i < len(have):
                print(
                    f"              got  {json.dumps({x: have[i].get(x) for x in keys})[:200]}"
                )
    return bad_total


# ── 2. sessions ──────────────────────────────────────────────────────────────
def _pile(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


def _key(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_text(KEY_HEX + "\n")
    path.chmod(0o600)


GARDEN = {
    "garden.md": "Tomatoes want six hours of sun.\n\nWater at the root.\n",
    "lot.html": "<html><script>var lot = 1;</script>\n<p>The blue Civic, VIN 1HGEJ6672841, one owner.</p></html>\n",
    "faq.md": "Has the shed been painted this year?\n",
    "util.py": "def frost_date(zone):\n    return zone\n",
    "\u2028names \u0085 kept.md": "Tomatoes want shade by four.\n",  # a raw separator in a served where
}


def session(impl, root: Path) -> dict:
    """One whole session through `impl`'s command line. Returns what it showed
    and what it left. Steps that need a subject compute it the kernel's way."""
    pile, box, key = root / "pile", root / "box", root / "keys" / "me.key"
    _pile(pile, GARDEN)
    _key(key)
    b = ["--box", str(box), "--now", TS]
    shown: list = []

    def step(*args: str) -> int:
        code, out = run(impl, *args)
        if args[0] == "init":  # the boot row's hash: its own version is in it
            out = re.sub(r"row (\d+) [0-9a-f]{16}", r"row \1 <hash>", out)
        try:
            body = json.loads(out)
        except ValueError:
            body = out
        shown.append([list(args[:1]), code, body])
        return code

    step("init", *b, "--pile", str(pile), "--key", str(key))
    step("bite", *b, "when does the frost come")
    step("bite", *b, "what does this do")
    step("bite", *b, "the one ending 672841")
    step("bite", *b, "has the shed been painted this year")
    step("bite", *b, "frost_date")
    step("bite", *b, "how much sun do tomatoes want")
    rows = k.read(box / "record.jsonl")[0]
    cite = rows[-1]["served"][0]["id"]
    good = {
        "path": "notes/sun.md",
        "data": "Six hours.",
        "cites": [cite],
        "claim": "from garden.md",
    }
    rows_file = root / "rows.jsonl"
    rows_file.write_text(
        "\n".join(
            [
                json.dumps(good),
                json.dumps({**good, "cites": ["nope"]}),
                json.dumps({**good, "path": "../escape"}),
                "not json",
            ]
        )
        + "\n"
    )
    step("propose", *b, "--file", str(rows_file))
    step("seal", *b, k.subject_of(good))
    step("bite", *b, "How much SUN do tomatoes want?!")  # sealed now
    step("bite", *b, "tomatoes want how much sun")  # other words: not sealed
    step("bite", *b, "water them at the root?")
    rows = k.read(box / "record.jsonl")[0]
    water = rows[-1]["served"][0]["id"]
    unsealed = {
        **good,
        "path": "notes/water.md",
        "data": "At the root.",
        "cites": [water],
    }
    rows_file.write_text(json.dumps(unsealed) + "\n")
    step("propose", *b, "--file", str(rows_file))
    step("out", *b)
    step("verify", *b)
    step("seal", *b, "proposal:" + "0" * 64)  # not a proposal here
    return {"shown": shown, "box": box, "key": key}


def one_script_check(box: Path) -> list[str]:
    """The kernel's promise: `Record.verify_chain` checks a kernel box unchanged."""
    sys.path.insert(0, str(HERE.parent))
    from onescript import record

    return record.Record(box, lambda: "", "", object()).verify_chain()


def forge_and_edit(impl, box: Path) -> list:
    """A forged seal (the right shape, the chain re-hashed), then an edit."""
    b = ["--box", str(box), "--now", TS]
    rows = k.read(box / "record.jsonl")[0]
    fake = next(r["subject"] for r in reversed(rows) if r.get("verdict") == "pass")
    k.append(box, TS, "seal", who="human", subject=fake, proof="0" * 64)
    out = [["forged", *run(impl, "verify", *b)]]
    path = box / "record.jsonl"
    path.write_bytes(
        path.read_bytes().replace(b'"verdict":"pass"', b'"verdict":"fail"', 1)
    )
    out.append(["edited", *run(impl, "verify", *b)])
    out.append(["edited bite", *run(impl, "bite", *b, "how much sun do tomatoes want")])
    return out


#: What differs by design: each implementation's own `version` hash, and every
#: field made from a row hash (`hash`, `prev`, and `out`'s `tip`)
SELF = ("version", "hash", "prev", "tip")


def rows_without_self(box: Path) -> list[dict]:
    text = (box / "record.jsonl").read_text(encoding="utf-8")
    return [
        {x: v for x, v in json.loads(line).items() if x not in SELF}
        for line in text.split("\n")[:-1]
    ]


def files_of(box: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(box)): p.read_bytes()
        for p in sorted(box.rglob("*"))
        if p.is_file() and p.name not in ("record.jsonl", "record.lock")
    }


def sessions(port, work: Path) -> int:
    root = work / "session"  # one path for both, so kernel.json and pile_sha256 agree
    sides = {}
    for impl in (Python(), port):
        if root.exists():
            shutil.rmtree(root)
        got = session(impl, root)
        sides[impl.name] = {
            "shown": got["shown"],
            "rows": rows_without_self(got["box"]),
            "files": files_of(got["box"]),
            "other_verify": run(
                port if impl.name == "kernel.py" else Python(),
                "verify",
                "--box",
                str(got["box"]),
            ),
            "own_verify": run(impl, "verify", "--box", str(got["box"])),
            "record": one_script_check(got["box"]),
            "attacks": forge_and_edit(impl, got["box"]),
        }
    py, jv = sides["kernel.py"], sides[port.name]
    bad = 0

    def check(what: str, a, b) -> None:
        nonlocal bad
        same = a == b
        bad += not same
        print(f"  {what:<44} {'ok' if same else 'UNEXPLAINED'}")
        if not same:
            print(f"      kernel.py: {str(a)[:300]}\n      {port.name}: {str(b)[:300]}")

    for i, (a, b2) in enumerate(zip(py["shown"], jv["shown"])):
        check(f"step {i:>2} {a[0][0]}: exit and what was shown", a[1:], b2[1:])
    check("every record row (version, hash, prev, tip aside)", py["rows"], jv["rows"])
    check("kernel.json, proposals and written files", py["files"], jv["files"])
    check("the forged seal, the edit, the bite after", py["attacks"], jv["attacks"])
    print("  both ways:")
    check(
        f"  {port.name}'s box, verified by kernel.py",
        jv["own_verify"][0],
        jv["other_verify"][0],
    )
    check(
        f"  kernel.py's box, verified by {port.name}",
        py["own_verify"][0],
        py["other_verify"][0],
    )
    check(
        "  ...and both said the chain holds",
        (py["own_verify"][0], jv["own_verify"][0]),
        (0, 0),
    )
    check(f"  {port.name}'s box, checked by the one script's Record", jv["record"], [])
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default="java", choices=["java"])
    ap.add_argument("--keep", type=Path, help="keep the work folder here")
    a = ap.parse_args(argv)
    work = a.keep or Path(tempfile.mkdtemp(prefix="kernel-compare-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        port = Java(work)
        print(f"vectors ({port.name} against kernel.py):")
        bad = vectors(port, work)
        print("sessions (the same session, both command lines, the same paths):")
        bad += sessions(port, work)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    print(f"unexplained differences: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
