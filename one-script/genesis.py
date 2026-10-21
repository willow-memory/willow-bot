"""genesis.py: the smallest one script, the reference. Python 3.9+, stdlib only.

    python genesis.py run    --box DIR [--check "CMD"]... [--now TS]
    python genesis.py verify --box DIR
    python genesis.py test

`run` verifies the box's record, runs each check in order, and appends one row
saying what it found. The first run starts the chain from GENESIS (open loop);
every later run starts from the last row's hash (closed loop). The rows are the
one script's own (`onescript/record.py`): the same keys, the same canon (family
A: sorted, compact, non-ASCII literal), hash = SHA-256 of the row without
`hash`, so `Record.verify_chain` checks a genesis box unchanged.

Exit codes:
    0   alive: the record verifies and every check passed.
    42  not enough information to say a human said this was true: a check
        failed, or couldn't run. The row says which. Escalating is the step a
        42 asks for, the return to 0 for the next tier, never a state.
    2   refused: the record doesn't verify, or ends in a torn line. Nothing is
        appended, and the chain never restarts from GENESIS to get past it.

Why 42. The operator chose it; 520 documents across the repos carry the stamp
`ΔΣ=42`. What it means, in their words (2026-10-21): "42 just means I don't
have enough information to say a human said this was true." And before:
  * "ΔΣ=42 — the sum of acknowledged gaps. Zero gaps means lying. A system that
    reports no unknowns has stopped looking." (willow-mcp
    docs/repatriation/VOICES.md)
  * "ΔΣ=42 literally = what the judge cannot evaluate." (willow-mcp
    docs/repatriation/COLLABORATION_VOICES.md)
  * "What the box can't compute is everything under NEEDS YOU. Your answers,
    sealed, are the next one script. ΔΣ=42" (one-script/deep_thought.py)
  * The constitution's meaning: the tamper-evidence seal, "a checksum asserting
    that the sum of all changes (Δ, delta) aggregated (Σ, sigma) resolves to a
    fixed invariant constant — every change accounted-for and verifiable"
    (willows-grove governance/CONSTITUTION.md, Open Operator Decisions #3).
So exit 42 is the gap acknowledged, never reported as nothing; the row's hash
is the checksum over change. Open: a 0 here says the checks passed, not that a
human said so; what a human said is a seal (`onescript`'s, or Nestor's).
Open: how 42 is stamped on documents (the constitution's enforcing artifact
"remains to be located or built"), and the stamp for a finished document.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
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


def canon(row: dict) -> str:
    """Family A, byte for byte (`onescript/canon.py` sorted_compact_utf8)."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def h256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def now() -> str:
    """The clock, injected by `--now`; this is only its default."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read(path: Path) -> tuple[list[dict], list[str]]:
    """(rows, breaks). Every break is said; an empty list means the chain holds."""
    if not path.exists():
        return [], []
    data = path.read_bytes().decode("utf-8")
    if data and not data.endswith("\n"):
        return [], ["torn tail: the last line is unterminated; a human looks first"]
    rows, breaks, prev = [], [], GENESIS
    for i, line in enumerate(data.splitlines()):
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


def check(cmd: str) -> dict:
    """One check: its command's exit 0 passes; anything else, or not running at
    all, is a gap to escalate, with why."""
    try:
        p = subprocess.run(shlex.split(cmd), capture_output=True, timeout=600)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"check": cmd, "passed": False, "why": f"didn't run: {type(e).__name__}"}
    return {"check": cmd, "passed": p.returncode == 0, "why": f"exit {p.returncode}"}


def append(path: Path, ts: str, **fields) -> dict:
    """One row on the chain, under a lock, fsynced with its directory."""
    with path.open("a+", encoding="utf-8", newline="") as f:  # "\n", on Windows too
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        rows, breaks = read(path)  # the true tail, read under the lock
        if breaks:
            raise RuntimeError("; ".join(breaks))
        row = {"n": len(rows), "who": "genesis", "family": "genesis"}
        row.update(standing="unattested", version=VERSION, ts=ts, where=None)
        row.update(fields, prev=rows[-1]["hash"] if rows else GENESIS)
        row["hash"] = h256(canon(row))
        f.write(canon(row) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if row["n"] == 0 and hasattr(os, "O_DIRECTORY"):  # the new name survives a crash
        d = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    return row


def run(box: Path, checks: list[str], ts: str) -> int:
    path = box / "record.jsonl"
    rows, breaks = read(path)
    if breaks:
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return REFUSED
    loop = "closed" if rows else "open"
    print(f"{loop} loop, from {rows[-1]['hash'][:16] if rows else 'GENESIS'}")
    results = []
    for cmd in checks:  # in order; the first that fails escalates
        results.append(check(cmd))
        print(f"  {'pass' if results[-1]['passed'] else 'FAIL'}  {cmd}")
        if not results[-1]["passed"]:
            break
    code = ALIVE if all(r["passed"] for r in results) else ESCALATE
    box.mkdir(parents=True, exist_ok=True)
    try:
        row = append(path, ts, kind="kernel", loop=loop, checks=results, exit=code)
    except RuntimeError as e:  # another writer tore or broke it since the read
        print(f"refused: {e}", file=sys.stderr)
        return REFUSED
    print(f"row {row['n']} {row['hash'][:16]}  exit {code}")
    return code


def test() -> None:
    """Open loop, closed loop, an escalation, an edit and a torn line refused.
    Runs in a temporary directory, never in a ledger of yours."""
    py = shlex.quote(sys.executable)
    with tempfile.TemporaryDirectory() as tmp:
        box, ts = Path(tmp) / "box", "2026-10-21T00:00:00Z"
        assert run(box, [f"{py} -c pass"], ts) == ALIVE
        assert (
            run(box, [f"{py} -c 'raise SystemExit(1)'", "never-runs"], ts) == ESCALATE
        )
        assert run(box, ["no-such-command-genesis"], ts) == ESCALATE
        rows, breaks = read(box / "record.jsonl")
        assert not breaks and [r["loop"] for r in rows] == ["open", "closed", "closed"]
        assert rows[1]["prev"] == rows[0]["hash"] and len(rows[1]["checks"]) == 1
        path, good = box / "record.jsonl", (box / "record.jsonl").read_bytes()
        path.write_bytes(good.replace(b'"exit":42', b'"exit":0', 1))
        assert read(path)[1] and run(box, [], ts) == REFUSED  # an edit is seen
        path.write_bytes(good + b'{"n": 3')
        assert run(box, [], ts) == REFUSED and path.read_bytes() == good + b'{"n": 3'
    print("genesis: open, closed, escalate, edit and torn tail all held")


def main(argv: list[str]) -> int:
    if argv[:1] == ["test"]:
        test()
        return ALIVE
    verb, args = (argv[0], argv[1:]) if argv else ("", [])
    opts: dict[str, list[str]] = {"--box": [], "--check": [], "--now": []}
    for flag, value in zip(args[::2], args[1::2]):
        if flag not in opts:
            break
        opts[flag].append(value)
    else:
        if verb in ("run", "verify") and len(args) % 2 == 0 and len(opts["--box"]) == 1:
            box = Path(opts["--box"][0])
            if verb == "verify":
                _, breaks = read(box / "record.jsonl")
                print("\n".join(breaks) or "the chain holds")
                return REFUSED if breaks else ALIVE
            return run(box, opts["--check"], (opts["--now"] or [now()])[-1])
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
