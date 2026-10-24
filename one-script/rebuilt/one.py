"""one.py: the one script, rebuilt from willow's pieces. Python 3.9+, stdlib only.

    python one.py checkin  --box DIR [--pile FOLDER --key FILE] [--now TS]
    python one.py seed     --box DIR "the first bite"
    python one.py turn     --box DIR --proposal FILE.jsonl
    python one.py escalate --box DIR "the task"
    python one.py needs    --box DIR
    python one.py checkout --box DIR
    python one.py verify   --box DIR
    python one.py map

The seven parts of `onescript/`, each done by willow-memory/willow's kernel
or one of its add-ons, and nothing of its own that could approve:

    1 boot      checkin: the record first, then the law, then four gates
    2 predict   seed: what the record says the bite will come to, then the
                bite, then the grade; once per check-in
    3 record    every row through the kernel's own append; verify
    4 gate      turn: the model's rows through the kernel's door
    5 resolve   escalate: a sealed answer, your files, every match, where
                to look, and then the person, since no model is wired here
    6 view      needs: the digest add-on's page, and the sessions waiting
    7 reverse   checkout: approvals gone stale, how the predictions did,
                then the session as one bundle for the person's seal

willow is found at --willow, else $ONESCRIPT_WILLOW, else beside willow-bot.
Its kernel must be the one `../kernel.pin` names, and its CONSTITUTION.md is
the law; without either, every verb refuses. A piece that's missing says so
and the rest still run. The seal is never this script's: it prints the
kernel's command for the person to run with their key.

Exit codes, the kernel's: 0 a person said it; 42 not enough to say so, or
something waits on the person; 2 refused.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIN = HERE.parent / "kernel.pin"
WHO = "onescript"
SATISFIED, FAILING, DIFFERENTLY, UNREACHABLE = (
    "satisfied",
    "failing",
    "differently",
    "unreachable",
)
POPULATED, EMPTY = "populated", "empty"
OPTIONS = [
    "put it back and continue",
    "keep it as a standing grant",
    "leave it, block it until you decide",
    "stop here",
]

#: Each part of `onescript/`, the willow piece that does its job here, and
#: where that piece lives: KERNEL (the kernel, or this file), an add-on's
#: folder (an idea's name, while willow hasn't built it), or BOT: the part
#: is the bot's own (Nestor, the Forge, Rat, the Grove), not willow's to do.
#: A piece does the part's job at the kernel's scale, not all the part does.
KERNEL, BOT = None, ""
MAP = [
    ("__main__.py", "one.py's command line", KERNEL),
    ("api.py", "one.py's verbs, one per part", KERNEL),
    ("run.py", "one.py: check-in, each turn, check-out", KERNEL),
    ("boot.py", "checkin: kernel.read, the law, the pin, kernel.test", KERNEL),
    ("opening.py", "kernel.init: the box, the key outside it, the pile", KERNEL),
    ("record.py", "kernel.append and kernel.read: the same chain", KERNEL),
    ("chains.py", "kernel.read", KERNEL),
    ("canon.py", "kernel.canon", KERNEL),
    ("clock.py", "kernel.now and --now", KERNEL),
    ("stamp.py", "kernel.append: who, family, standing", KERNEL),
    ("words.py", "kernel.words and kernel.bite_key", KERNEL),
    ("sign.py", "kernel.proof: HMAC under the person's key", KERNEL),
    ("states.py", "populated, empty, unreachable, said by each piece", KERNEL),
    ("predict.py", "seed: the record's own count, graded after", KERNEL),
    ("seed.py", "kernel.bite", KERNEL),
    ("chunk.py", "kernel.chunks", KERNEL),
    ("pile.py", "kernel.rank: the words rung only", KERNEL),
    ("boxstore.py", "scan: a folder indexed once", "scan"),
    ("ladder.py", "again: asked, noticed, offered every third", "again"),
    ("proposals.py", "kernel.propose and kernel.door", KERNEL),
    ("gate.py", "kernel.door and kernel.sealed", KERNEL),
    ("nestor_seal.py", "kernel.seal: the person's key stands in for Nestor", KERNEL),
    ("writeback.py", "kernel.bite: a sealed answer for these words first", KERNEL),
    ("resolve.py", "escalate: kernel.bite, all, kernel.look", "all"),
    ("escalate.py", "escalate, to the person: no model is wired", "all"),
    ("prompt_path.py", "claude-code: every prompt a bite", "claude-code"),
    ("plant.py", "start: a person's first run", "start"),
    ("plant_rule.py", "start", "start"),
    ("serve.py", "the scope idea", "scope"),
    ("scrub.py", "privacy", "privacy"),
    ("view.py", "needs: digest", "digest"),
    ("reverse.py", "checkout: stale", "stale"),
    ("session.py", "checkout: session-seal", "session-seal"),
    ("xref.py", "xref", "xref"),
    ("diverge.py", "diverge", "diverge"),
    ("dice.py", "dice", "dice"),
    ("stream.py", "dice: the seeded stream, ported draw for draw", "dice"),
    ("odds.py", "odds", "odds"),
    ("life.py", "life", "life"),
    ("draw.py", "surprise", "surprise"),
    ("calibration.py", "the forecasts idea", "forecasts"),
    ("deck.py", "the predictions idea", "predictions"),
    ("market.py", "the market-calls idea", "market-calls"),
    ("beacon.py", "the beacon idea", "beacon"),
    ("dice5e.py", "the dice5e idea", "dice5e"),
    ("table.py", "the game-master idea", "game-master"),
    ("lore.py", "the lore idea", "lore"),
    ("rules.py", "the rules idea", "rules"),
    ("retire.py", "the retire idea", "retire"),
    ("snapshot.py", "the snapshot idea", "snapshot"),
    ("jeles_store.py", "the jeles idea", "jeles"),
    ("box_lookup.py", "the Forge's box seam", BOT),
    ("graph.py", "the code graph across the bot's repos", BOT),
    ("mai_shape.py", "@markdownai documents", BOT),
    ("merge_check.py", "the bot's merge gate", BOT),
    ("oauth_resource.py", "Grove and willow-mcp's tokens", BOT),
    ("pools.py", "the table's endings", BOT),
    ("reach.py", "how the bot's repos find the one script", BOT),
    ("reach_bootstrap.py", "reach", BOT),
    ("xorshift.py", "the table's second generator", BOT),
]

#: The add-ons the seven parts lean on; each one missing is said, not hidden.
PIECES = ("again", "all", "digest", "stale", "session-seal")


# ── finding willow ──────────────────────────────────────────────────────────
def find_willow(given: str | None) -> Path | None:
    for c in (given, os.environ.get("ONESCRIPT_WILLOW")):
        if c:
            return Path(c)
    for name in ("Willow", "willow"):
        p = HERE.parents[2] / name
        if (p / "kernel.py").is_file():
            return p
    return None


def load(willow: Path):
    """willow's kernel, the very module its add-ons import."""
    sys.path.insert(0, str(willow))
    import kernel

    return kernel


def piece(willow: Path, folder: str):
    """An add-on's module, or None when its folder isn't in this willow."""
    d = willow / "adapters" / "stdlib" / folder
    files = sorted(p for p in d.glob("*.py") if not p.name.startswith("test_"))
    if len(files) != 1:
        return None
    mod = types.ModuleType("onescript_" + folder.replace("-", "_"))
    mod.__file__ = str(files[0])
    exec(
        compile(files[0].read_text(encoding="utf-8"), str(files[0]), "exec"),
        mod.__dict__,
    )
    return mod


def quiet(fn, *args) -> tuple[object, str]:
    """A verb run with its printing caught: (what it returned, what it said)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        got = fn(*args)
    return got, out.getvalue()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 1 boot ──────────────────────────────────────────────────────────────────
def gates(k, willow: Path, box: Path, rows: list[dict]) -> list[dict]:
    """The four gates, each `satisfied`, `failing`, `differently` or
    `unreachable`, and why when it isn't satisfied."""
    out = []
    pin = json.loads(PIN.read_text(encoding="utf-8"))["sha256"]
    got = sha(willow / "kernel.py")
    out.append(
        {"gate": "pin", "state": SATISFIED}
        if got == pin
        else {
            "gate": "pin",
            "state": FAILING,
            "why": f"willow's kernel is {got[:12]}, the pin says {pin[:12]}",
        }
    )
    law = sha(willow / "CONSTITUTION.md")
    last = next((r for r in reversed(rows) if r["kind"] == "checkin"), None)
    if last is None or last.get("law") == law:
        out.append({"gate": "law", "state": SATISFIED})
    else:
        out.append(
            {
                "gate": "law",
                "state": DIFFERENTLY,
                "why": "the law changed since the last check-in",
            }
        )
    try:  # in the kernel's own temporary folder, never this box
        quiet(k.test)
        out.append({"gate": "beats", "state": SATISFIED})
    except Exception as e:  # noqa: BLE001  an assert, or anything the beats raised
        out.append(
            {
                "gate": "beats",
                "state": FAILING,
                "why": f"the eight beats broke: {type(e).__name__}",
            }
        )
    good = k.sealed(box, rows)
    forged = [
        r["n"] for r in rows if r["kind"] == "seal" and r.get("subject") not in good
    ]
    out.append(
        {"gate": "seals", "state": SATISFIED}
        if not forged
        else {
            "gate": "seals",
            "state": FAILING,
            "why": f"seal rows {forged} don't prove under the person's key",
        }
    )
    return out


def open_checkin(rows: list[dict]) -> dict | None:
    """The check-in this session runs under, or None: none yet, checked out,
    or closed hard."""
    for r in reversed(rows):
        if r["kind"] == "checkout":
            return None
        if r["kind"] == "checkin":
            return None if r.get("hard_close") else r
    return None


def checkin(k, willow: Path, box: Path, opts: dict, ts: str) -> int:
    if not (box / "kernel.json").exists():
        if not (opts.get("--pile") and opts.get("--key")):
            print(
                "refused: not a kernel box; check in with --pile and --key "
                "the first time",
                file=sys.stderr,
            )
            return k.REFUSED
        code, said = quiet(k.init, box, Path(opts["--pile"]), Path(opts["--key"]), ts)
        print(said.strip())
        if code != k.ALIVE:
            return code
    rows, _ = k.read(box / "record.jsonl")
    if open_checkin(rows):
        print("refused: a session is open; check out first", file=sys.stderr)
        return k.REFUSED
    gs = gates(k, willow, box, rows)
    there = {p: piece(willow, p) is not None for p in PIECES}
    hard = [g for g in gs if g["state"] == FAILING]
    row = k.append(
        box,
        ts,
        "checkin",
        who=WHO,
        law=sha(willow / "CONSTITUTION.md"),
        kernel=k.VERSION,
        gates=gs,
        pieces=there,
        hard_close=bool(hard),
    )
    print(f"CHECK-IN (row {row['n']})")
    for g in gs:
        print(
            f"  {g['state']:<12} {g['gate']}" + (f" — {g['why']}" if "why" in g else "")
        )
    for p, ok in there.items():
        if not ok:
            print(f"  {UNREACHABLE:<12} {p} — not in this willow; its part says so")
    if hard:
        print("HARD CLOSE:")
        for g in hard:
            print(f"  {g['gate']}: {g['why']}")
        print("options: " + " | ".join(OPTIONS))
        return k.REFUSED
    print("open: one seed this session, then turn, escalate, needs, checkout")
    return k.ALIVE


# ── 2 predict ───────────────────────────────────────────────────────────────
def came_to(r: dict) -> str:
    if r.get("answered_by"):
        return "sealed"
    return POPULATED if any(not s.get("echo") for s in r.get("served", [])) else EMPTY


def declare(rows: list[dict]) -> dict:
    """What the record says a bite comes to: counted, never guessed. Ties go
    to `empty`, the answer that asks the person."""
    seen = [came_to(r) for r in rows if r["kind"] == "bite"]
    if not seen:
        return {"likely": None, "n": 0}
    counts = {s: seen.count(s) for s in set(seen)}
    order = sorted(counts, key=lambda s: (-counts[s], s != EMPTY, s))
    return {"likely": order[0], "n": len(seen), "seen": dict(sorted(counts.items()))}


def seed(k, willow: Path, box: Path, rows: list[dict], text: str, ts: str) -> int:
    cin = open_checkin(rows)
    if any(r["kind"] == "predict" and r.get("checkin") == cin["n"] for r in rows):
        print("refused: one seed per check-in; it's been sown", file=sys.stderr)
        return k.REFUSED
    guess = declare(rows)
    again = piece(willow, "again")
    code, said = quiet(k.bite, box, text, ts)
    if code == k.REFUSED:
        print(said.strip(), file=sys.stderr)
        return code
    rows, _ = k.read(box / "record.jsonl")
    got = came_to(rows[-1])
    right = None if guess["likely"] is None else guess["likely"] == got
    k.append(
        box,
        ts,
        "predict",
        who=WHO,
        checkin=cin["n"],
        bite_row=rows[-1]["n"],
        likely=guess["likely"],
        got=got,
        right=right,
    )
    doc = json.loads(said)
    doc["predicted"] = {**guess, "got": got, "right": right}
    if again is None:
        doc["again"] = {"state": UNREACHABLE, "why": "the again add-on isn't here"}
    else:
        e = again.repeats(rows)[k.bite_key(text)]  # this bite is on it now
        doc["again"] = {"times": e["times"], "rung": again.rung(e)}
    print(json.dumps(doc, ensure_ascii=False))
    return code


# ── 4 gate ──────────────────────────────────────────────────────────────────
def turn(k, box: Path, path: Path, ts: str) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as e:
        print(
            f"refused: the proposal file can't be read: {type(e).__name__}",
            file=sys.stderr,
        )
        return k.REFUSED
    code, said = quiet(k.propose, box, lines, ts)
    print(said.strip())
    if code == k.ESCALATE:
        print(f"the seal is yours: python kernel.py seal --box {box} proposal:<hash>")
    return code


# ── 5 resolve ───────────────────────────────────────────────────────────────
def escalate(k, willow: Path, box: Path, text: str, ts: str) -> int:
    """The ladder, rung by rung, stopping at the first that answers. One
    vocabulary: `answered`, or `escalated:<reason>`."""
    code, said = quiet(k.bite, box, text, ts)
    if code == k.REFUSED:
        print(said.strip(), file=sys.stderr)
        return code
    doc = {"bite_key": k.bite_key(text), **json.loads(said)}
    rungs = [
        {
            "rung": "sealed answer",
            "state": POPULATED if doc["state"] == "sealed" else EMPTY,
        }
    ]
    if doc["state"] == "sealed":
        return said_and_kept(k, box, ts, doc, rungs, "answered")
    rungs.append(
        {"rung": "your files", "state": POPULATED if code == k.ALIVE else EMPTY}
    )
    if code == k.ALIVE:
        return said_and_kept(k, box, ts, doc, rungs, "answered")
    every = piece(willow, "all")
    if every is None:
        rungs.append(
            {
                "rung": "every match",
                "state": UNREACHABLE,
                "why": "the all add-on isn't here",
            }
        )
    else:
        more = every.ranked(box, text)
        rungs.append(
            {
                "rung": "every match",
                "state": POPULATED if more else EMPTY,
                "found": len(more),
            }
        )
        if more:
            doc["every"] = [
                {"where": f"{c['path']}:{c['line']}", "text": c["text"]} for c in more
            ]
            return said_and_kept(k, box, ts, doc, rungs, "answered")
    rungs.append(
        {"rung": "where to look", "state": POPULATED if doc.get("look") else EMPTY}
    )
    rungs.append(
        {
            "rung": "a model",
            "state": UNREACHABLE,
            "why": "no model is wired into the rebuilt one script",
        }
    )
    return said_and_kept(k, box, ts, doc, rungs, "escalated:to_the_person")


def said_and_kept(k, box: Path, ts: str, doc: dict, rungs: list, end: str) -> int:
    k.append(
        box,
        ts,
        "escalate",
        who=WHO,
        bite_key=doc["bite_key"],
        end=end,
        rungs=[{n: v for n, v in r.items() if n != "why"} for r in rungs],
    )
    doc["rungs"], doc["end"] = rungs, end
    print(json.dumps(doc, ensure_ascii=False))
    return k.ALIVE if end == "answered" else k.ESCALATE


# ── 6 view ──────────────────────────────────────────────────────────────────
def needs(k, willow: Path, box: Path, rows: list[dict], ts: str) -> tuple[str, int]:
    """The morning screen: the digest's page, then the sessions waiting.
    Writes nothing."""
    parts, code = ["NEEDS YOU"], k.ALIVE
    dg = piece(willow, "digest")
    if dg is None:
        parts.append(f"  {UNREACHABLE}: the digest add-on isn't here")
    else:
        today = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        doc = dg.digest(box, today)
        parts.append(dg.render(doc, 14))
        code = max(code, dg.code_of(doc))
    ss = piece(willow, "session-seal")
    if ss is None:
        parts.append(f"Sessions\n  {UNREACHABLE}: the session-seal add-on isn't here")
    else:
        c, said = quiet(ss.check, box, rows)
        parts.append(
            "Sessions\n" + "\n".join("  " + x for x in said.strip().splitlines())
        )
        code = k.REFUSED if c == k.REFUSED else max(code, c)
    return "\n".join(parts), code


# ── 7 reverse ───────────────────────────────────────────────────────────────
def checkout(k, willow: Path, box: Path, rows: list[dict], ts: str) -> int:
    cin = open_checkin(rows)
    st = piece(willow, "stale")
    if st is None:
        stale = {"state": UNREACHABLE, "why": "the stale add-on isn't here"}
    else:
        _, entries = st.check(box)
        gone = sum(1 for e in entries if e.get("gone"))
        stale = {
            "state": POPULATED if gone else EMPTY,
            "approvals": len(entries),
            "gone": gone,
        }
    graded = [
        r["right"] for r in rows if r["kind"] == "predict" and r["right"] is not None
    ]
    grades = {"predicted": len(graded), "right": sum(graded)}
    k.append(box, ts, "checkout", who=WHO, checkin=cin["n"], stale=stale, grades=grades)
    print("CHECK-OUT")
    print(
        "  stale: "
        + (
            f"{stale['gone']} of {stale['approvals']} approvals cite a "
            "passage changed or gone"
            if "gone" in stale
            else stale["why"]
        )
    )
    print(f"  predictions: {grades['right']} of {grades['predicted']} right")
    rows, _ = k.read(box / "record.jsonl")
    screen, code = needs(k, willow, box, rows, ts)
    print(screen)
    ss = piece(willow, "session-seal")
    if ss is None:
        print(f"session: {UNREACHABLE}: the session-seal add-on isn't here")
        return max(code, k.ESCALATE)
    c, said = quiet(ss.close, box, ts)
    print("SESSION\n" + "\n".join("  " + x for x in said.strip().splitlines()))
    return k.REFUSED if k.REFUSED in (c, code) else max(c, code)


# ── 3 record ────────────────────────────────────────────────────────────────
def verify(k, willow: Path, box: Path) -> int:
    code, said = quiet(k.verify, box)
    print(said.strip())
    ss = piece(willow, "session-seal")
    if ss is not None and code != k.REFUSED:
        rows, _ = k.read(box / "record.jsonl")
        c, s = quiet(ss.check, box, rows)
        print(s.strip().splitlines()[-1])
        code = k.REFUSED if c == k.REFUSED else code
    return code


def show_map(willow: Path | None) -> int:
    """Each part, and whether willow's piece for it is here: `piece`, `idea`
    (named on willow's list, not built), or `bot` (not willow's to do)."""
    root = HERE.parent / "onescript"
    said = {"piece": 0, "idea": 0, "bot": 0}
    for part, how, folder in MAP:
        if folder == BOT:
            state = "bot"
        elif folder is KERNEL or (
            willow is not None and (willow / "adapters" / "stdlib" / folder).is_dir()
        ):
            state = "piece"
        else:
            state = "idea"
        said[state] += 1
        f = root / part
        lines = len(f.read_text(encoding="utf-8").splitlines()) if f.is_file() else 0
        print(f"{state:<6} {part:<18} {lines:>5}  {how}")
    print(
        f"{len(MAP)} parts: {said['piece']} done by willow's pieces, "
        f"{said['idea']} still ideas on willow's list, {said['bot']} the bot's own"
    )
    return 0 if not said["idea"] else 42


# ── the command line ────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    verb, rest = (argv[0], argv[1:]) if argv else ("", [])
    opts: dict[str, str] = {}
    args: list[str] = []
    it = iter(rest)
    for a in it:
        if a in ("--box", "--pile", "--key", "--now", "--willow", "--proposal"):
            opts[a] = next(it, "")
        else:
            args.append(a)
    willow = find_willow(opts.get("--willow"))
    if verb == "map" and not args and not opts.keys() - {"--willow"}:
        return show_map(willow)
    usage = __doc__.split("\n\n")[1]
    wants = {
        "checkin": 0,
        "seed": 1,
        "turn": 0,
        "escalate": 1,
        "needs": 0,
        "checkout": 0,
        "verify": 0,
    }
    if (
        verb not in wants
        or len(args) != wants[verb]
        or not opts.get("--box")
        or (verb == "turn") != bool(opts.get("--proposal"))
    ):
        print(usage, file=sys.stderr)
        return 2
    if willow is None or not (willow / "kernel.py").is_file():
        print(
            "refused: no willow; set ONESCRIPT_WILLOW or clone it beside willow-bot",
            file=sys.stderr,
        )
        return 2
    if not (willow / "CONSTITUTION.md").is_file():
        print(
            f"refused: no law; willow's constitution isn't at {willow}", file=sys.stderr
        )
        return 2
    k = load(willow)
    box, ts = Path(opts["--box"]), opts.get("--now") or k.now()
    rows, breaks = k.read(box / "record.jsonl")
    if breaks:  # the record first
        print("refused:", *breaks, sep="\n  ", file=sys.stderr)
        return k.REFUSED
    if verb == "checkin":
        return checkin(k, willow, box, opts, ts)
    if not (box / "kernel.json").exists():
        print("refused: not a kernel box; check in first", file=sys.stderr)
        return k.REFUSED
    if verb == "verify":
        return verify(k, willow, box)
    if verb == "needs":
        screen, code = needs(k, willow, box, rows, ts)
        print(screen)
        return code
    if open_checkin(rows) is None:
        print("refused: no open check-in; nothing moves before one", file=sys.stderr)
        return k.REFUSED
    if verb == "seed":
        return seed(k, willow, box, rows, args[0], ts)
    if verb == "turn":
        return turn(k, box, Path(opts["--proposal"]), ts)
    if verb == "escalate":
        return escalate(k, willow, box, args[0], ts)
    return checkout(k, willow, box, rows, ts)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
