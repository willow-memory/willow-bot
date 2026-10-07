"""The one script, run on this box.

    cd docs/design/one-script
    python3 -m onescript checkin            # boot: record, probes, the four gates
    python3 -m onescript turn "the bite"    # one turn as the desk
    python3 -m onescript checkout           # reverse, then the morning screen

The box defaults to the repo's `.flow/onescript/` (excluded from git). The
record persists there between commands, so check-in, turns and check-out are
one run across several invocations.

Every command first writes an `invocation` row: the argv, the root, the box,
and the hash of every input it read. A replay reads that row back; when the
bytes differ, the row names which input moved.

Honest about itself: the keys are the skeleton's HMAC secrets, kept beside the
box in `.flow/onescript-keys/` (0600), not passkeys, and still inside what the
sandbox can see. The desk signs its own identity with a key it can read,
so a turn proves the record's chain, not who sat at the keyboard.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import boot, gate, record
from .run import PKG, Run

ROOT = PKG.parents[3]  # onescript -> one-script -> design -> docs -> repo
CONSTITUTION = ROOT / "governance" / "CONSTITUTION.md"
CI = ROOT / ".github" / "workflows" / "tests.yml"
DESK = ("desk", "claude")
ANCHOR = "anchor.json"  # the sealed tip, beside the keys: outside the box


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KeysExposed(Exception):
    """A keys file anyone but the owner can read is refused, not used."""


def _keys(path: Path) -> dict[str, bytes]:
    """The signing secrets live beside the box, never in it: the record's own
    three-way check flags any file in the box the run didn't write."""
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({DESK[0]: secrets.token_hex(32)}, f)
    if path.stat().st_mode & 0o077:
        raise KeysExposed(f"{path} is readable beyond its owner; chmod 600 it")
    return {k: bytes.fromhex(v) for k, v in json.loads(path.read_text()).items()}


def _law(text: str) -> dict:
    """Trace IDs as the constitution writes them: CONST-0, CONST-0-1, CONST-I-1."""
    ids = sorted(set(re.findall(r"CONST-(?:0|[IVXL]+)(?:-\d+)*", text)))
    return {"trace_ids": ids, "grants": []}


_STAMP = {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}


def _last_boot(rows: list[dict]) -> dict | None:
    """The boot report as the record holds it. A hard close outlives the
    invocation that found it: nothing in memory carries it to the next one."""
    i = next(
        (k for k in range(len(rows) - 1, -1, -1) if rows[k]["kind"] == "boot"), None
    )
    if i is None:
        return None
    b = rows[i]
    before = [r for r in rows[:i] if r["kind"] == "reconcile"]
    return {
        "hard_close": b["hard_close"],
        "lines": b["lines"],
        "options": b.get("options", boot.OPTIONS if b["hard_close"] else []),
        # as boot() wrote it: the last reconcile before this check-in, and when
        "report": {"state": "current", "at": before[-1]["ts"]}
        if before
        else {"state": "never"},
        # a checkout after this check-in ends the run; a turn needs a new one
        "checked_out": any(r["kind"] == "reconcile" for r in rows[i + 1 :]),
        "probes": b.get("probes", []),
        "gates": b.get("gates", []),
        "egress": b.get("egress", []),
        "anchor": b.get("anchor"),
    }


def _from_record(run: Run) -> None:
    """Grades, claims and acts back from the record, for a checkout that runs
    in a different invocation from the turns that made them."""
    rows = run.rec.rows()
    run.graded = [
        {k: v for k, v in r.items() if k not in _STAMP | {"turn"}}
        for r in rows
        if r["kind"] == "grade"
    ]
    run.claims = [c for r in rows if r["kind"] == "claims" for c in r["claims"]]
    run.acts = [
        {"kind": r["act_kind"], **{k: r[k] for k in ("verdict", "reason", "card")}}
        for r in rows
        if r["kind"] == "act"
    ]


def _in_venv(venv: Path):
    """The toolchain gate reads the bot's venv, where the one script's tools
    live (D2), never whatever happens to be first on PATH."""

    def version_of(tool: str) -> str | None:
        exe = venv / "bin" / tool
        if not exe.exists():
            return None
        _, last = boot._run([str(exe), "--version"], ".", timeout=30)
        m = re.search(r"\d+\.\d+(?:\.\d+)?", last)
        return m.group(0) if m else last

    return version_of


#: Set in the tests gate's child. A checkin started under it never runs the
#: tests gate again: the gate runs the suite, the suite runs checkin, and an
#: unguarded loop forked until the box ran out of memory (2026-10-06, Kart
#: Z7X2TJQX, a mutant with the keys refusal removed).
NESTED = "ONESCRIPT_IN_TESTS_GATE"


def _gate_cfg(no_tests: bool, ci_text: str, venv: Path) -> dict:
    pin = re.search(r"ruff==([\d.]+)", ci_text)
    cfg: dict = {
        "pins": {"tools": {"ruff": pin.group(1)}} if pin else {},
        "version_of": _in_venv(venv),
        "found_in": f"in {venv}",
        "repos": [str(ROOT)],
    }
    if not no_tests and not os.environ.get(NESTED):
        os.environ[NESTED] = "1"  # inherited by the suite the gate runs
        cfg["tests"] = [
            {
                "name": "onescript",
                "argv": [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "onescript/tests",
                ],
                "cwd": str(PKG.parent),
                "needs": ["pytest"],
            }
        ]
    return cfg


def _read(path: Path) -> tuple[str, str | None]:
    """Text and its hash, or ("", None) when the input is missing: recorded, not guessed."""
    if not path.exists():
        return "", None
    data = path.read_bytes()
    return data.decode("utf-8"), record.h256(data)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="onescript")
    p.add_argument("--box", type=Path, default=ROOT / ".flow" / "onescript")
    p.add_argument(
        "--keys", type=Path, default=ROOT / ".flow" / "onescript-keys" / "keys.json"
    )
    p.add_argument(
        "--venv",
        type=Path,
        default=Path(os.environ.get("WILLOW_HOME", "~")).expanduser()
        / "venvs"
        / "willow-bot",
        help="the venv whose tools the toolchain gate checks (the bot's)",
    )
    p.add_argument("--now", help="fixed clock, for replays and tests")
    p.add_argument("--no-tests", action="store_true", help="skip the tests gate")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("checkin")
    t = sub.add_parser("turn")
    t.add_argument("bite")
    sub.add_parser("checkout")
    args = p.parse_args(argv)

    clock = (lambda: args.now) if args.now else _now
    law_text, law_sha = _read(CONSTITUTION)
    ci_text, ci_sha = _read(CI)
    if args.keys.resolve().is_relative_to(args.box.resolve()):
        print("refused: the keys file can't live inside the box")
        return 2
    try:
        keys = _keys(args.keys)
    except KeysExposed as e:
        print(f"refused: {e}")
        return 2
    law = _law(law_text)
    nested = bool(os.environ.get(NESTED))  # before _gate_cfg sets it for the child
    run = Run(args.box, keys, law, clock, anchor=args.keys.parent / ANCHOR)
    run.rec.append(
        "invocation",
        run.sys,
        cmd=args.cmd,
        argv=list(argv if argv is not None else sys.argv[1:]),
        root=str(ROOT),
        box=str(args.box),
        venv=str(args.venv),
        inputs={
            "governance/CONSTITUTION.md": law_sha,
            ".github/workflows/tests.yml": ci_sha,
            "onescript": run.version,
            "git_head": boot._git(str(ROOT), "rev-parse", "HEAD") or None,
            "python": f"{sys.executable} {sys.version.split()[0]}",
            "venv_ruff": _in_venv(args.venv)("ruff"),
        },
        trace_ids=len(law["trace_ids"]),
        nested=nested,  # the tests gate is skipped, and says so
    )

    if args.cmd == "checkin":
        try:
            rep = run.checkin(_gate_cfg(args.no_tests, ci_text, args.venv))
        except boot.BoxWontOpen as e:
            # Recorded as a closed boot, so no later turn opens on an older,
            # clean check-in (Loki AAEDF24D).
            run.rec.append(
                "boot",
                run.sys,
                hard_close=True,
                lines=[f"box won't open: {e}"],
                options=["stop here"],
                probes=e.probes,
                gates=[],
                egress=[],
                anchor=run.rec.anchor_state(),
            )
            print(f"BOX WON'T OPEN: {e}")
            return 3
        print(_checkin_screen(rep, nested))
        return 1 if rep["hard_close"] else 0

    last = _last_boot(run.rec.rows())
    if args.cmd == "turn":
        if last is None or last["hard_close"] or last["checked_out"]:
            why = (
                "no check-in on record"
                if last is None
                else "the last check-in hard-closed"
                if last["hard_close"]
                else "the run checked out after its last check-in"
            )
            run.rec.append("refused", run.sys, at="turn", reason=why, bite=args.bite)
            print(f"refused: {why}; nothing moves until a check-in opens")
            return 1
        who, family = DESK
        ident = {"who": who, "family": family, "sig": gate.sign(keys[who], who, family)}
        out = run.turn(ident, args.bite)
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return 0

    _from_record(run)
    _, screen = run.checkout(boot_report=last)
    print(screen)
    return 0


ANCHOR_SAYS = {
    "never": "never sealed; a cut or a rewrite of the record can't be seen",
    "sealed": "a tip is sealed; a break against it shows under HARD CLOSE",
    "unreadable": "unreadable",
}


def _checkin_screen(rep: dict, nested: bool = False) -> str:
    L = ["CHECK-IN"]
    if nested:
        L.append(f"nested: inside the tests gate ({NESTED}); tests gate skipped")
    held = sum(p["held"] for p in rep["probes"])
    L.append(f"probes: {held}/{len(rep['probes'])} held")
    L.append(f"anchor: {ANCHOR_SAYS.get(rep['anchor'], rep['anchor'])}")
    for g in rep["gates"]:
        why = f" — {g['why']}" if g["why"] else ""
        L.append(f"  {g['verdict']:12} {g['gate']}: {g['where']}{why}")
    if rep["hard_close"]:
        L.append("HARD CLOSE:")
        L += [f"  {x}" for x in rep["lines"]]
        L.append("options: " + " | ".join(rep["options"]))
    else:
        L.append("open")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
