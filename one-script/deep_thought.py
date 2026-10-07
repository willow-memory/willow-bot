"""deep_thought — the script that helps define the next one script.

Deep Thought had the Answer and not the Question, so it designed the computer
that could find the Question, and the people living in it were part of the
computer (next-pile.md, "What the script can't solve"). This file is that, for
the next one script:

- THE ANSWER is carried, not computed: what the human sealed on 2026-10-02
  (D1, D2, D9, Q13) and the seven purposes plus `run`. Most of the thinking is
  already done; this file only holds it.
- THE MEASURE is read-only: where each purpose lives today (the skeleton, Rat,
  the bot), whether the bot's parts can be called on their own and whether
  they write (and *how* — append vs truncate vs mkdir), which venvs sit
  outside the bot, the bot socket's mode and peer check, what the Nestor
  ledger shows was offered and chosen, whether each carried seal still sits
  on the ledger and in the store, and which docs still say a sealed thing is
  open.
- THE QUESTION is the output: every place the Answer and the box disagree, as
  a line only the human can decide. Their answers are the next one.
- THE GRADES score the Answer against the box (parts home, seal join, doc
  drift, venvs) so the morning screen matches next-pile's order.

Three improvements over the first cut (2026-10-02, first run):

1. Ledger-join the Answer — each carried seal/rejection is checked against
   the Nestor ledger by pair_id and against tm_pairs / tm_rejections; the
   signature gap is measured, not only carried as prose.
2. Write-kind triage — bot write sites are classed append / truncate / mkdir
   so D2's read-only rule asks a sharper question of each part.
3. GRADES — a scored block between AGREED NOT SEALED and CHOICES, matching
   the morning-screen order in next-pile.md.

Read only. D2, in the operator's words: "It doesn't matter who runs which part,
as long as it's read only, and the final decision goes to the human." Nothing
here moves, writes, seals or calls out. Stdlib only. Same box, same bytes: no
clock, sorted everything.

Home: D2 puts the one script inside willow-bot. This file sits beside the
skeleton in the Grove until the operator moves it.

    python3 deep_thought.py           # the screen
    python3 deep_thought.py --json    # the same, as rows

Env: ONESCRIPT_ROOT (default ~/github/willow-memory), WILLOW_HOME (the box;
unset leaves the box probes unreachable, never guessed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
SKELETON = HERE.parent / "onescript"
DESIGN = HERE.parent.parent
ROOT = Path(os.environ.get("ONESCRIPT_ROOT", "~/github/willow-memory")).expanduser()
RAT = ROOT / "ratatosk"
BOT = ROOT / "willow-bot"
_box = os.environ.get("WILLOW_HOME", "").strip()
BOX = Path(_box).expanduser() if _box else None

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "worktrees",
        "tests",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
    }
)

# ── the Answer: sealed by the human, 2026-10-02 ─────────────────────────────
SEALS = (
    {
        "id": "D1",
        "pair": "02050acf-acd9-4a45-a3df-7307b74565b8",
        "ruling": "Rat is the one canonical runtime; every shim calls it.",
        "words": "yes",
    },
    {
        "id": "D2",
        "pair": "656a352f-f19b-4677-b1e6-8cd581daf820",
        "ruling": "Everything lives inside willow-bot: the venvs and the one script. "
        "Each part is called on its own, by anyone, read-only; the final "
        "decision goes to the human.",
        "words": "All is the things should live inside the bot. All the .venv, the one "
        "script. Each part can call it on it's own. It doesn't matter who runs "
        "which part, as long as it's read only, and the final decision goes "
        "to the human.",
    },
    {
        "id": "D9",
        "pair": "01700806-1c02-4200-aaed-d117861616a0",
        "ruling": "Claude Code is the first OUT proof; Cursor is second.",
        "words": "claude, then cursor",
    },
    {
        "id": "Q13",
        "pair": "66c986e0-d90e-4720-89cd-090442e3ca8c",
        "ruling": "Every front end's prompt-submit goes through Rat.",
        "words": "every",
    },
)
REJECTED = (
    {
        "id": "D2/extract",
        "pair": "d74b0672-785a-484c-9cfd-a2b3a746c478",
        "ruling": "extract deterministic/ out of the bot",
    },
)

# ── the seven purposes plus run (next-pile.md), and where each might live ───
# rat: module basenames next-pile names as already in Rat.
# bot: files in willow-bot that serve the purpose today (candidates, not claims).
PARTS = (
    {
        "name": "boot",
        "purpose": "session record, state + attestation, egress index, hard close",
        "rat": ("seat.py", "session.py"),
        "bot": ("willow_bot/deterministic/host_probe.py",),
    },
    {
        "name": "predict",
        "purpose": "declare the next-bite distribution before the work",
        "rat": (),
        "bot": (),
    },
    {
        "name": "record",
        "purpose": "append-only turn record, the stamp, pile pointers",
        "rat": ("session.py", "traces.py"),
        "bot": ("willow_bot/deterministic/runner.py",),
    },
    {
        "name": "gate",
        "purpose": "the doors: fail closed and loud, peer uid, grant cards, egress",
        "rat": ("permission.py", "policy.py", "hooks.py"),
        "bot": (
            "willow_bot/deterministic/socket_server.py",
            "willow_bot/deterministic/policy.py",
        ),
    },
    {
        "name": "resolve",
        "purpose": "the user's sources first, then web, local model, cloud",
        "rat": ("ladder.py",),
        "bot": (
            "willow_bot/deterministic/chain.py",
            "willow_bot/deterministic/resolvers.py",
            "willow_bot/deterministic/chat_op.py",
        ),
    },
    {
        "name": "view",
        "purpose": "the morning screen from the record; marks drawn, never written",
        "rat": (),
        "bot": (),
    },
    {
        "name": "reverse",
        "purpose": "re-check old assertions; three-way, four verdicts, a why on each",
        "rat": (),
        "bot": (),
    },
    {
        "name": "run",
        "purpose": "the sequencer only; no logic of its own",
        "rat": (),
        "bot": ("willow_bot/deterministic/cli.py",),
    },
)

# ── what the record already holds as open (carried, not measured) ────────────
# Seal-signature standing is measured by measure_pairs (ledger-join), not carried.
CARRIED_NEEDS = (
    (
        "D1 next to D2",
        "D1: Rat is the runtime every shim calls. D2: the one script lives in the bot and "
        "each part is called on its own. Reading on file: Rat is the door, the bot is the "
        "home. Confirm or correct.",
    ),
    (
        "run: an eighth file, or folded into one of the seven?",
        "Open in next-pile.md; the count is of purposes, not files.",
    ),
    (
        "the numbers are yours",
        "min_features, the similarity threshold, the night budget, the repetition count. "
        "The script may propose each from the record; it never sets one.",
    ),
)
CARRIED_AGREED = (
    ("seven purposes plus run", "next-pile.md, agent-reported"),
    (
        "build order: record, reverse, view, boot, gate, predict, resolve",
        "next-pile.md, the 2026-09-02 build-order rule, agent-reported",
    ),
    (
        "four gates (tests, toolchain, freshness, reachability) and three layers "
        "(claims, boundaries, mandate)",
        "next-pile.md, agent-reported",
    ),
    (
        "push is egress: a grant card per push, never a blanket",
        "next-pile.md, agent-reported",
    ),
    (
        "record what was offered next to what the human chose",
        "next-pile.md, agent-reported",
    ),
)
STANDING = (
    "Kaggle first (deadline 2026-10-11); no Forge code changes until the day-two cloud run finishes",
    "constitution-proposal/ is built on Draft 0.7 and must be redone against 0.8",
    "the OUT door is proven on Claude Code first, then Cursor (D9, sealed)",
)

OPEN_WORDS = re.compile(
    r"\b(D1|D2|D9|Q13)\b[^\n]{0,60}?\b(unsealed|not sealed|not yet sealed|is not sealed)",
    re.IGNORECASE,
)
IN_RAT = re.compile(
    r"\b(lives?|goes|go)\s+(in|into)\s+Rat\b|Where it lives:\s*\**\s*Rat", re.IGNORECASE
)
WRITES = re.compile(
    r"\.write_text\(|\.write_bytes\(|\bopen\([^)\n]*['\"][wax]b?\+?['\"]|os\.replace\(|"
    r"\.unlink\(|\.mkdir\(|\.rename\("
)
APPEND_OPEN = re.compile(r"\bopen\([^)\n]*['\"]a")
TRUNCATE_OPEN = re.compile(r"\bopen\([^)\n]*['\"]w")


# ── small read-only helpers ──────────────────────────────────────────────────
def write_kind(line: str) -> str:
    """Triage a write site for D2: append (record-shaped), truncate, mkdir, other."""
    if ".mkdir(" in line:
        return "mkdir"
    if APPEND_OPEN.search(line):
        return "append"
    if ".write_text(" in line or ".write_bytes(" in line or TRUNCATE_OPEN.search(line):
        return "truncate"
    if ".unlink(" in line or ".rename(" in line or "os.replace(" in line:
        return "mutate"
    return "write"


def write_why(kind: str, cites: str) -> str:
    if kind == "append":
        return (
            f"D2 asks read-only of every caller; this part appends. {cites}. "
            "Keep append under a sealed mandate (record's purpose), or strip it?"
        )
    if kind == "mkdir":
        return (
            f"D2 asks read-only of every caller; this only creates directories. {cites}. "
            "Parent mkdir on whose word, or refuse and require the path to exist?"
        )
    if kind == "truncate":
        return (
            f"D2 asks read-only of every caller; this truncates or overwrites. {cites}. "
            "Does this part keep a write, and on whose word?"
        )
    return (
        f"D2 asks read-only of every caller; this mutates the box. {cites}. "
        "Does this part keep a write, and on whose word?"
    )


def sha16(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def rel(path: Path) -> str:
    for base in (ROOT, BOX):
        if base is not None:
            try:
                return str(path.relative_to(base))
            except ValueError:
                pass
    return str(path)


def row(subject: str, verdict: str, why: str) -> dict:
    return {"subject": subject, "verdict": verdict, "why": why}


def walk_for(base: Path, names: set[str]) -> dict[str, list[Path]]:
    """Every file under `base` whose basename is in `names`, skipping SKIP_DIRS."""
    found: dict[str, list[Path]] = {n: [] for n in names}
    if not base.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in filenames:
            if f in found:
                found[f].append(Path(dirpath) / f)
    return {n: sorted(v) for n, v in found.items()}


def bot_entry_files() -> set[str]:
    """Bot files reachable as a command on their own: [project.scripts] targets."""
    try:
        import tomllib

        data = tomllib.loads((BOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return set()
    out = set()
    for target in data.get("project", {}).get("scripts", {}).values():
        module = str(target).split(":", 1)[0]
        out.add(module.replace(".", "/") + ".py")
    return out


# ── the measure ──────────────────────────────────────────────────────────────
def measure_parts() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    needs: list[dict] = []
    disagree: list[dict] = []
    quiet: list[dict] = []
    table: list[dict] = []
    rat_index = walk_for(RAT, {m for p in PARTS for m in p["rat"]})
    entries = bot_entry_files()
    all_files: set[Path] = set()

    for p in PARTS:
        sk = SKELETON / f"{p['name']}.py"
        homes = {
            "skeleton": [sk] if sk.is_file() else [],
            "rat": sorted({f for m in p["rat"] for f in rat_index.get(m, [])}),
            "bot": [BOT / b for b in p["bot"] if (BOT / b).is_file()],
        }
        for files in homes.values():
            all_files.update(files)
        table.append(
            {
                "part": p["name"],
                "purpose": p["purpose"],
                **{k: [rel(f) for f in v] for k, v in homes.items()},
            }
        )
        elsewhere = [f"{k} {len(v)}" for k, v in homes.items() if v and k != "bot"]

        if not homes["bot"]:
            where = ", ".join(elsewhere) or "nowhere"
            needs.append(
                row(
                    f"{p['name']}: nothing in the bot",
                    "failing",
                    f"D2 puts every part inside willow-bot. Today it is in: {where}. "
                    "Move one in, merge them, or write it new in the bot?",
                )
            )
        elif elsewhere:
            disagree.append(
                row(
                    f"{p['name']}: {1 + len(elsewhere)} homes",
                    "differently",
                    f"bot {len(homes['bot'])}, "
                    + ", ".join(elsewhere)
                    + ". D2 says one home, the bot: which survives there?",
                )
            )
        else:
            quiet.append(
                row(
                    f"{p['name']}: in the bot only",
                    "satisfied",
                    ", ".join(rel(f) for f in homes["bot"]),
                )
            )

        for f in homes["bot"]:
            r = str(f.relative_to(BOT))
            text = f.read_text(encoding="utf-8", errors="replace")
            if r not in entries and "__main__" not in text:
                disagree.append(
                    row(
                        f"{p['name']}: {r} is not callable on its own",
                        "differently",
                        "no [project.scripts] entry and no __main__; D2: each part can be "
                        "called on its own",
                    )
                )
            hits = [
                (i, ln.strip())
                for i, ln in enumerate(text.splitlines(), 1)
                if WRITES.search(ln)
            ]
            if hits:
                kinds = Counter(write_kind(ln) for _, ln in hits)
                kind_label = "+".join(f"{k}×{n}" for k, n in sorted(kinds.items()))
                first = "; ".join(
                    f"L{i}:{write_kind(ln)} {ln[:60]}" for i, ln in hits[:3]
                )
                # Dominant kind drives the question; truncate/mutate outrank append/mkdir.
                dominant = (
                    "truncate"
                    if kinds["truncate"] or kinds["mutate"] or kinds["write"]
                    else "append"
                    if kinds["append"]
                    else "mkdir"
                    if kinds["mkdir"]
                    else "write"
                )
                needs.append(
                    row(
                        f"{p['name']}: {r} writes ({len(hits)} sites, {kind_label})",
                        "failing",
                        write_why(dominant, first),
                    )
                )

    n = len(all_files)
    line = row(
        f"{n} files for {len(PARTS)} purposes",
        "satisfied" if n <= len(PARTS) else "differently",
        "the count is of purposes, not files (next-pile.md)",
    )
    (quiet if n <= len(PARTS) else disagree).append(line)
    return needs, disagree, quiet, table


def measure_venvs() -> tuple[list[dict], list[dict]]:
    found: set[Path] = set()
    for base in (ROOT, BOX):
        if base is None or not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            if "pyvenv.cfg" in filenames:
                found.add(here)
                dirnames[:] = []
                continue
            if len(here.relative_to(base).parts) >= 3:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(
                d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}
            )
    bot_homes = [BOT] + ([BOX / "willow-bot"] if BOX else [])
    inside = sorted(v for v in found if any(v.is_relative_to(h) for h in bot_homes))
    outside = sorted(found - set(inside))
    needs, quiet = [], []
    if outside:
        needs.append(
            row(
                f"{len(outside)} venvs outside the bot",
                "failing",
                "D2: all the .venv live in the bot. Move each, or name its exemption: "
                + ", ".join(rel(v) for v in outside),
            )
        )
    quiet.append(
        row(
            f"{len(inside)} venvs inside the bot, {len(outside)} outside",
            "satisfied" if not outside else "failing",
            "searched ONESCRIPT_ROOT and WILLOW_HOME to depth 3",
        )
    )
    return needs, quiet


def measure_socket() -> list[dict]:
    if BOX is None:
        return [row("bot socket", "unreachable", "WILLOW_HOME unset; not guessed")]
    path = BOX / "willow-bot" / "deterministic.sock"
    try:
        raw = json.loads(
            (BOX / "willow-bot" / "deterministic-policy.json").read_text(
                encoding="utf-8"
            )
        )
        if isinstance(raw, dict) and raw.get("socket_path"):
            path = Path(
                str(raw["socket_path"]).replace("$WILLOW_HOME", str(BOX))
            ).expanduser()
    except (OSError, ValueError):
        pass
    server = BOT / "willow_bot/deterministic/socket_server.py"
    try:
        peer = "SO_PEERCRED" in server.read_text(encoding="utf-8")
    except OSError:
        peer = None
    try:
        st = path.stat()
    except FileNotFoundError:
        return [
            row(
                f"bot socket {rel(path)}",
                "failing",
                "absent: the bot is not serving; Q13 sends every prompt here",
            )
        ]
    except OSError as e:
        return [row(f"bot socket {rel(path)}", "unreachable", str(e))]
    mode = stat.S_IMODE(st.st_mode)
    tight = stat.S_ISSOCK(st.st_mode) and mode & 0o007 == 0
    if tight and peer:
        return [row(f"bot socket {oct(mode)}, peer-checked", "satisfied", rel(path))]
    return [
        row(
            f"bot socket {oct(mode)}, peer check {'present' if peer else 'absent' if peer is False else 'unread'}",
            "failing",
            "D1 + Q13 put every prompt from every front end through here (net_signer is "
            "the 0660 + peercred reference). Harden before that traffic? Not yet in your words.",
        )
    ]


def measure_ledger() -> tuple[list[dict], list[dict], list[dict]]:
    """Offered vs chosen, from the Nestor ledger. Three-state, never collapsed."""
    if BOX is None:
        return [], [row("Nestor ledger", "unreachable", "WILLOW_HOME unset")], []
    path = BOX / "nestor.db.ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return [], [row("Nestor ledger", "unreachable", str(e))], []
    if not lines:
        return [], [row("Nestor ledger", "empty", rel(path))], []
    kinds: Counter = Counter()
    passages = with_pair = 0
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            kinds["unparseable"] += 1
            continue
        k = str(r.get("kind", "?"))
        kinds[f"{k}:{r['state']}" if r.get("state") else k] += 1
        if k == "passage":
            passages += 1
            with_pair += bool(r.get("pair_id"))
    choices = [
        row(
            f"offered (drafts): {kinds['passage:draft']}",
            "satisfied",
            "machine-produced",
        ),
        row(
            f"chosen (sealed): {kinds['passage:sealed']}",
            "satisfied",
            "a human act each",
        ),
        row(
            f"refused (rejected): {kinds['reject_pair']}",
            "satisfied",
            "a human act each",
        ),
    ]
    disagree = []
    if passages and with_pair < passages:
        disagree.append(
            row(
                f"ledger passages carry a pair id: {with_pair} of {passages}",
                "differently",
                "a seal can't be joined to its offer from the ledger alone; the proposal "
                "'record what was offered next to what the human chose' needs that join",
            )
        )
    quiet = [
        row(
            f"Nestor ledger: {len(lines)} rows",
            "satisfied",
            ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
        )
    ]
    return disagree, quiet, choices


def measure_pairs() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Join the Answer to ledger + store. Ledger is the act; store is servable.

    Returns (held, disagree, quiet, needs). Seal-signature standing is measured
    here (improvement 1) rather than carried as prose.
    """
    want = {s["pair"]: ("sealed", s["id"]) for s in SEALS}
    want.update({s["pair"]: ("rejected", s["id"]) for s in REJECTED})
    if BOX is None:
        return (
            [],
            [],
            [row("Nestor store", "unreachable", "WILLOW_HOME unset")],
            [
                row(
                    "seal join",
                    "unreachable",
                    "WILLOW_HOME unset; carried pairs not checked against ledger or store",
                )
            ],
        )

    # ── ledger: kind seal / reject_pair by pair_id ───────────────────────────
    ledger_path = BOX / "nestor.db.ledger.jsonl"
    ledger_got: dict[str, str] = {}  # pair_id -> sealed|rejected
    try:
        for ln in ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            pid = str(r.get("pair_id") or "")
            if pid not in want:
                continue
            k = str(r.get("kind", ""))
            if k == "seal":
                ledger_got[pid] = "sealed"
            elif k == "reject_pair":
                ledger_got[pid] = "rejected"
    except OSError as e:
        return (
            [],
            [],
            [row("Nestor ledger (seal join)", "unreachable", str(e))],
            [row("seal join", "unreachable", f"ledger unreadable: {e}")],
        )

    # ── store: tm_pairs.status / tm_rejections by id ─────────────────────────
    db = BOX / "nestor.db"
    store_got: dict[str, str] = {}
    store_quiet: list[dict] = []
    if not db.is_file():
        store_quiet.append(row("Nestor store", "unreachable", f"{rel(db)} absent"))
    else:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error as e:
            store_quiet.append(row("Nestor store", "unreachable", str(e)))
            con = None
        if con is not None:
            try:
                tables = {
                    r[0]
                    for r in con.execute(
                        "select name from sqlite_master where type='table'"
                    )
                }
                if "tm_pairs" in tables:
                    for pid in want:
                        hit = con.execute(
                            "select status from tm_pairs where id=?", (pid,)
                        ).fetchone()
                        if hit:
                            store_got[pid] = str(hit[0])
                if "tm_rejections" in tables:
                    for pid, (expect, _sid) in want.items():
                        if expect != "rejected" or pid in store_got:
                            continue
                        hit = con.execute(
                            "select 1 from tm_rejections where pair_id=? or id=?",
                            (pid, pid),
                        ).fetchone()
                        if hit:
                            store_got[pid] = "rejected"
                if not store_got:
                    store_quiet.append(
                        row(
                            "Nestor store",
                            "empty",
                            "tm_pairs/tm_rejections hold none of the Answer's pair ids "
                            "(ledger may still record the act; not servable from the store)",
                        )
                    )
            except sqlite3.Error as e:
                store_quiet.append(row("Nestor store", "unreachable", str(e)))
            finally:
                con.close()

    held: list[dict] = []
    disagree: list[dict] = []
    needs: list[dict] = []
    missing_ledger: list[str] = []
    missing_store: list[str] = []
    for pid, (expect, sid) in sorted(want.items(), key=lambda kv: kv[1][1]):
        led = ledger_got.get(pid)
        st = store_got.get(pid)
        if led == expect and st == expect:
            held.append(
                row(
                    f"{sid} {pid[:8]} ledger+store {expect}",
                    "satisfied",
                    "Answer, ledger act, and store row agree",
                )
            )
        elif led == expect and st is None:
            missing_store.append(f"{sid}:{pid[:8]}")
            held.append(
                row(
                    f"{sid} {pid[:8]} ledger {expect}, store absent",
                    "witnessed at most",
                    "ledger records the act; store has no row — not servable as verified "
                    "(gap 2e290e75506e)",
                )
            )
        elif led == expect and st != expect:
            disagree.append(
                row(
                    f"{sid} {pid[:8]}",
                    "failing",
                    f"ledger {led}; store {st}; Answer carries {expect}",
                )
            )
        elif led is None:
            missing_ledger.append(f"{sid}:{pid[:8]}")
            disagree.append(
                row(
                    f"{sid} {pid[:8]}",
                    "failing",
                    f"Answer carries {expect}; ledger has no seal/reject_pair for this pair_id",
                )
            )
        else:
            disagree.append(
                row(
                    f"{sid} {pid[:8]}",
                    "failing",
                    f"Answer carries {expect}; ledger says {led}",
                )
            )

    if missing_store and not missing_ledger:
        needs.append(
            row(
                f"seal signatures: {len(missing_store)} of {len(want)} on ledger only "
                "(gap 2e290e75506e)",
                "open",
                "Ledger records the human act; tm_pairs has no row, so Nestor cannot serve "
                "them as signature_valid/servable. Trace the seal→store path, or treat "
                "ledger presence as enough until the store catches up: "
                + ", ".join(missing_store),
            )
        )
    elif missing_ledger:
        needs.append(
            row(
                f"seal join: {len(missing_ledger)} carried pairs absent from ledger",
                "failing",
                "Answer names pair ids the ledger never sealed/rejected: "
                + ", ".join(missing_ledger),
            )
        )
    return held, disagree, store_quiet, needs


def measure_docs() -> list[dict]:
    """Docs that still call a sealed thing open, or still home the script in Rat."""
    out = []
    files = sorted(
        set(HERE.parent.rglob("*.md")) | set((DESIGN / "one-box").glob("*.md"))
    )
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            m = OPEN_WORDS.search(ln)
            if m:
                sid = m.group(1).upper()
                pair = next(s["pair"][:8] for s in SEALS if s["id"] == sid)
                out.append(
                    row(
                        f"{rel(f)}:{i} says {sid} is open",
                        "differently",
                        f"sealed {pair}. The line: {ln.strip()[:110]}",
                    )
                )
            if IN_RAT.search(ln):
                out.append(
                    row(
                        f"{rel(f)}:{i} homes the script in Rat",
                        "differently",
                        f"D2 homes it in the bot. The line: {ln.strip()[:110]}",
                    )
                )
    return out


def grade_box(
    table: list[dict],
    held: list[dict],
    needs: list[dict],
    disagree: list[dict],
    v_quiet: list[dict],
) -> list[dict]:
    """Score the Answer against the box — morning-screen GRADES (improvement 3)."""
    parts_n = len(table)
    in_bot = sum(1 for p in table if p.get("bot"))
    only_bot = sum(
        1 for p in table if p.get("bot") and not p.get("rat") and not p.get("skeleton")
    )
    # held rows that mention ledger (measured join)
    seal_n = len(SEALS) + len(REJECTED)
    ledger_ok = sum(1 for r in held if "ledger" in r["subject"])
    doc_drift = sum(
        1
        for r in disagree
        if "says " in r["subject"] or "homes the script" in r["subject"]
    )
    write_fails = sum(1 for r in needs if " writes (" in r["subject"])
    empty_parts = sum(1 for r in needs if r["subject"].endswith("nothing in the bot"))
    venv_line = next((r for r in v_quiet if "venvs inside" in r["subject"]), None)

    def _score(ok: int, total: int) -> str:
        if total <= 0:
            return "n/a"
        return f"{ok}/{total}"

    grades = [
        row(
            f"parts in the bot: {_score(in_bot, parts_n)} "
            f"({only_bot} bot-only, {empty_parts} empty)",
            "satisfied" if empty_parts == 0 and in_bot == parts_n else "failing",
            "D2: every purpose lives inside willow-bot. Empty parts are NEEDS YOU; "
            "multi-home parts are DISAGREEMENTS.",
        ),
        row(
            f"Answer joined to ledger: {_score(ledger_ok, seal_n)}",
            "satisfied" if ledger_ok == seal_n else "failing",
            "Each carried seal/rejection must appear as ledger seal or reject_pair "
            "by pair_id. Store presence is separate (servable).",
        ),
        row(
            f"doc lines still contradicting seals: {doc_drift}",
            "satisfied" if doc_drift == 0 else "differently",
            "OPEN_WORDS / IN_RAT hits under one-script and one-box docs.",
        ),
        row(
            f"bot write sites still open: {write_fails}",
            "satisfied" if write_fails == 0 else "failing",
            "Append / truncate / mkdir triage under D2; each open site is a NEEDS YOU.",
        ),
    ]
    if venv_line:
        grades.append(
            row(
                venv_line["subject"],
                venv_line["verdict"],
                "D2: all the .venv live in the bot. " + venv_line["why"],
            )
        )
    return grades


# ── the screen ───────────────────────────────────────────────────────────────
def think() -> dict:
    needs, disagree, quiet, table = measure_parts()
    v_needs, v_quiet = measure_venvs()
    sock = measure_socket()
    l_disagree, l_quiet, choices = measure_ledger()
    held, p_disagree, p_quiet, p_needs = measure_pairs()
    docs = measure_docs()

    needs += v_needs + [r for r in sock if r["verdict"] == "failing"] + p_needs
    needs += [row(s, "open", w) for s, w in CARRIED_NEEDS]
    disagree += p_disagree + l_disagree + docs
    quiet += v_quiet + [r for r in sock if r["verdict"] != "failing"]
    quiet += l_quiet + p_quiet + [row(s, "standing", "carried") for s in STANDING]
    grades = grade_box(table, held, needs, disagree, v_quiet)
    return {
        "stamp": {
            "who": "deep_thought",
            "standing": "unattested",
            "code": sha16(HERE),
            "root": str(ROOT),
            "box": str(BOX) if BOX else None,
        },
        "answer": [{**s} for s in SEALS] + [{**s, "rejected": True} for s in REJECTED],
        "answer_held": held,
        "next_one": table,
        "needs_you": needs,
        "disagreements": disagree,
        "agreed_not_sealed": [
            row(s, "witnessed at most", w) for s, w in CARRIED_AGREED
        ],
        "grades": grades,
        "choices": choices,
        "quiet": quiet,
    }


def render(t: dict) -> str:
    s = t["stamp"]
    out = [
        "DEEP THOUGHT: the next one script",
        f"stamp: who={s['who']} standing={s['standing']} code={s['code']} "
        f"root={s['root']} box={s['box'] or 'unset'}",
        "read only. nothing here is moved, written or sealed.",
        "",
        "THE ANSWER (sealed by the human, 2026-10-02)",
    ]
    for a in t["answer"]:
        if a.get("rejected"):
            out.append(f"  x {a['id']:<10} {a['pair'][:8]}  rejected: {a['ruling']}")
        else:
            out.append(f"  {a['id']:<12} {a['pair'][:8]}  {a['ruling']}")
            out.append(f'  {"":<12} {"":<8}  words: "{a["words"]}"')
    out += [
        "",
        "THE NEXT ONE, AS FAR AS THE ANSWER GOES (proposal)",
        "  every part: inside willow-bot · callable on its own · read-only · "
        "ends at a card for the human",
    ]
    for p in t["next_one"]:
        today = "  ".join(f"{k}={len(p[k])}" for k in ("skeleton", "rat", "bot"))
        out.append(f"  {p['part']:<8} {p['purpose'][:58]:<58}  {today}")

    for title, key in (
        ("NEEDS YOU", "needs_you"),
        ("DISAGREEMENTS", "disagreements"),
        ("AGREED, NOT SEALED", "agreed_not_sealed"),
        ("GRADES", "grades"),
        ("CHOICES (offered vs chosen)", "choices"),
        ("QUIET", "quiet"),
    ):
        rows = list(t.get(key) or [])
        if key == "quiet":
            rows = rows + list(t.get("answer_held") or [])
        out += ["", f"{title} ({len(rows)})"]
        for r in rows:
            out.append(f"  [{r['verdict']}] {r['subject']}")
            if key != "quiet" or r["verdict"] not in ("satisfied", "standing"):
                out.append(f"      {r['why']}")
    out += [
        "",
        "THE QUESTION",
        "  What the box can't compute is everything under NEEDS YOU. Your answers,",
        "  sealed, are the next one script. ΔΣ=42",
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", action="store_true", help="rows instead of the screen")
    args = ap.parse_args(argv)
    t = think()
    sys.stdout.write(
        json.dumps(t, indent=2, sort_keys=True) + "\n" if args.json else render(t)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
