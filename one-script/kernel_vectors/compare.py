"""Hold the ports to kernel.py: every vector, whole sessions, and each other's boxes.

    python3 compare.py [--port all|java|ts|csharp] [--keep DIR]

Three checks for each port, each said as a count; "unexplained" is the
number to read, as in `foundation/compare.py`:

1. **vectors.** The port answers `cases.json` (`<port> vectors cases.json
   results.json`); every answer is lined up against kernel.py's.
2. **sessions.** The same scripted session (init, bites, proposals, a seal,
   out, verify, a forged seal, an edit) run through kernel.py's command line
   and the port's, on the same paths with the same key, so each box is
   directly comparable: exit codes, what the person was shown, kernel.json,
   proposal files, and every record row but `version`, `hash`, `prev` and
   `tip` (`version` is each implementation's own hash, so every field made
   from a row hash differs by design).
3. **every way.** Each implementation's box is verified by every other
   implementation, and by the one script's `Record`.

A port whose toolchain isn't on the machine is skipped and said so. Exit 0
when nothing is unexplained, 1 otherwise.

Toolchains: Java needs a JDK and a UTF-8 locale (the JVM can't name a
non-ASCII file without one; it runs here with LC_ALL=C.UTF-8). TypeScript
needs Node 18+ and npm (it installs `ts/`'s two dev dependencies once).
C# needs the .NET 8 SDK.
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
ENV = {
    **os.environ,
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "KERNEL_TABLES": str(Path(__file__).resolve().parent / "kernel_tables.json"),
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
}


# ── the ports ────────────────────────────────────────────────────────────────
TABLES = HERE / "kernel_tables.json"


def _build(cmd: list[str], cwd: Path | None = None) -> None:
    got = subprocess.run(cmd, capture_output=True, text=True, env=ENV, cwd=cwd)
    if got.returncode != 0:
        raise SystemExit(
            f"{' '.join(cmd)} failed:\n{got.stdout[-2000:]}{got.stderr[-2000:]}"
        )


class Java:
    name = "java"
    tools = ("javac", "java")

    def __init__(self, work: Path):
        self.out = work / "java-out"
        _build(
            [
                "javac",
                "-nowarn",
                "-d",
                str(self.out),
                str(HERE / "java" / "Kernel.java"),
            ]
        )

    def argv(self, *args: str) -> list[str]:
        return [
            "java",
            f"-Dkernel.tables={TABLES}",
            "-cp",
            str(self.out),
            "Kernel",
            *args,
        ]


class TypeScript:
    name = "ts"
    tools = ("node", "npm", "npx")

    def __init__(self, work: Path):
        src = HERE / "ts"
        if not (src / "node_modules" / "typescript").exists():
            _build(["npm", "install", "--no-audit", "--no-fund"], cwd=src)
        self.out = work / "ts-dist"
        _build(
            ["npx", "tsc", "-p", str(src / "tsconfig.json"), "--outDir", str(self.out)],
            cwd=src,
        )

    def argv(self, *args: str) -> list[str]:
        return ["node", str(self.out / "kernel.js"), *args]


class CSharp:
    name = "csharp"
    tools = ("dotnet",)

    def __init__(self, work: Path):
        self.out = work / "cs-out"
        _build(
            [
                "dotnet",
                "build",
                str(HERE / "csharp" / "Kernel.csproj"),
                "-c",
                "Release",
                "-o",
                str(self.out),
                "-nologo",
            ]
        )

    def argv(self, *args: str) -> list[str]:
        return ["dotnet", str(self.out / "Kernel.dll"), *args]


PORTS = {"java": Java, "ts": TypeScript, "csharp": CSharp}


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


def sessions(ports: list, work: Path) -> int:
    """The same session through kernel.py and every port, one at a time on one
    path (so kernel.json and pile_sha256 agree). Each box, while it stands, is
    verified by every implementation and by the one script's Record."""
    root = work / "session"
    every = [Python(), *ports]
    sides = {}
    for impl in every:
        if root.exists():
            shutil.rmtree(root)
        got = session(impl, root)
        sides[impl.name] = {
            "shown": got["shown"],
            "rows": rows_without_self(got["box"]),
            "files": files_of(got["box"]),
            "verified_by": {
                v.name: run(v, "verify", "--box", str(got["box"]))[0] for v in every
            },
            "record": one_script_check(got["box"]),
            "attacks": forge_and_edit(impl, got["box"]),
        }
    py = sides["kernel.py"]
    bad = 0

    def check(what: str, a, b, who: str) -> None:
        nonlocal bad
        same = a == b
        bad += not same
        if not same:
            print(f"  {who:<7} {what:<46} UNEXPLAINED")
            print(f"      want: {str(a)[:300]}\n      got:  {str(b)[:300]}")

    for port in ports:
        side = sides[port.name]
        before = bad
        for i, (a, b2) in enumerate(zip(py["shown"], side["shown"])):
            check(
                f"step {i:>2} {a[0][0]}: exit and what was shown",
                a[1:],
                b2[1:],
                port.name,
            )
        check(
            "every record row (version, hash, prev, tip aside)",
            py["rows"],
            side["rows"],
            port.name,
        )
        check(
            "kernel.json, proposals and written files",
            py["files"],
            side["files"],
            port.name,
        )
        check(
            "the forged seal, the edit, the bite after",
            py["attacks"],
            side["attacks"],
            port.name,
        )
        check(
            "its box, checked by the one script's Record", [], side["record"], port.name
        )
        steps = len(py["shown"]) + 4
        print(f"  {port.name:<7} {steps - (bad - before)}/{steps} session checks ok")
    names = [i.name for i in every]
    print("  every box, verified by every implementation (0 = the chain holds):")
    print("  " + " " * 18 + "".join(f"{n:>11}" for n in names))
    for box_of in names:
        row = sides[box_of]["verified_by"]
        print(f"  {box_of + ' wrote':<18}" + "".join(f"{row[n]:>11}" for n in names))
        for n in names:
            if row[n] != 0:
                bad += 1
                print(f"      UNEXPLAINED: {n} won't verify {box_of}'s box")
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default="all", choices=["all", *PORTS])
    ap.add_argument("--keep", type=Path, help="keep the work folder here")
    a = ap.parse_args(argv)
    work = a.keep or Path(tempfile.mkdtemp(prefix="kernel-compare-"))
    work.mkdir(parents=True, exist_ok=True)
    wanted = list(PORTS) if a.port == "all" else [a.port]
    bad = 0
    try:
        ports = []
        for name in wanted:
            cls = PORTS[name]
            missing = [t for t in cls.tools if not shutil.which(t)]
            if missing:
                print(f"{name}: skipped, no {', '.join(missing)} on this machine")
                continue
            ports.append(cls(work))
        for port in ports:
            print(f"vectors ({port.name} against kernel.py):")
            bad += vectors(port, work)
        if ports:
            print(
                "sessions (the same session through every command line, the same paths):"
            )
            bad += sessions(ports, work)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    print(f"unexplained differences: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
