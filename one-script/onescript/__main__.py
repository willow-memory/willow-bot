"""The one script, run on this box. A thin wrapper over `onescript.api`.

    cd one-script                           # in willow-bot
    python3 -m onescript checkin            # boot: record, probes, the four gates
    python3 -m onescript seed "the first bite"   # its words against the box, once
    python3 -m onescript scope --by who     # code proposes a stack: card + subject
    python3 -m onescript seal SUBJECT ...   # the human's seal over that subject
    python3 -m onescript serve --by who     # write the one file the model reads
    python3 -m onescript turn "the bite"    # one turn as the desk
    python3 -m onescript turn "the bite" --proposal f.jsonl   # the model's rows
    python3 -m onescript escalate "the task"  # D0, then small pieces to the local rung
    python3 -m onescript pooled             # unsealed pass proposals, JSON lines
    python3 -m onescript resume             # the last sealed scope's spec + subject, JSON
    python3 -m onescript needs              # the morning screen's NEEDS YOU lines, JSON
    python3 -m onescript decline KEY        # the human's No to an offer, recorded
    python3 -m onescript forecast ID --dist FILE [--by ROLE] [--committed FILE]
    python3 -m onescript forecast ID --dist FILE --deck FILE  # {question, beacon_round}
    python3 -m onescript deck-reveal ROUND  # the beacon's answers as deck rows (--drand)
    python3 -m onescript deck-outcome WAGER [--human-key FILE]  # proposed, never sealed
    python3 -m onescript forecast ID --dist FILE --market FILE  # {symbol, kind, close_date[, threshold]}
    python3 -m onescript market-reveal DATE  # the closes as market rows (--prices, --drand)
    python3 -m onescript market-outcome WAGER [--human-key FILE]  # proposed, never sealed
    python3 -m onescript seal-outcome WAGER --outcome '{"path_index": 0}' (--proof P | --pair F)
    python3 -m onescript lore proposal:HASH  # a sealed world/*.md becomes lore rows
    python3 -m onescript rules proposal:HASH # a sealed rules/*.md becomes rules rows, one @phase each
    python3 -m onescript seal-tip tip:N:HASH [--proof P] # what it covers; then seal
    python3 -m onescript seal-forecast WAGER --proof P   # the human: this one is mine
    python3 -m onescript reconcile [--human-key FILE]    # wagers scored, read-only
    python3 -m onescript read-misses [--human-key FILE]  # misses clustered, read-only
    python3 -m onescript checkout [--human-key FILE]  # reverse, then the morning screen
    python3 -m onescript verdict --json FILE         # one table verdict row (J4)
    python3 -m onescript ledger-head --n N --head H  # the agm_head row (J9, R9)
    python3 -m onescript keys export --from KEYRING [--to FILE]  # public half only
    python3 -m onescript merge-check merge.json [--json] [--record] [--box BOX]  # the merge gate
    python3 -m onescript diverge A B [--repo NAME ...] [--repos DIR] [--json] [--record]
        [--behaviour] [--box-a BOX --box-b BOX]  # where two refs part, to the smallest unit

The app calls the functions in `onescript.api`; this file only reads argv,
builds the `Config`, calls one of them and prints. Nothing here needs a tty.

The box defaults to willow-bot's `.flow/onescript/` (excluded from git). The
record persists there between commands, so check-in, turns and check-out are
one run across several invocations.

Every command first writes an `invocation` row: the argv, the root, the box,
and the hash of every input it read. A replay reads that row back; when the
bytes differ, the row names which input moved.

The law is willows-grove's: its constitution's Trace IDs, and the ruff pin
in its CI. willows-grove is found at ONESCRIPT_GROVE, else beside willow-bot
(../willows-grove). Without it there is no law, and every command refuses.

Honest about itself: the keys are the skeleton's HMAC secrets, kept beside the
box in `.flow/onescript-keys/` (0600), not passkeys, and still inside what the
sandbox can see. The desk signs its own identity with a key it can read,
so a turn proves the record's chain, not who sat at the keyboard.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import sys
from pathlib import Path

from . import api, boot, nestor_seal  # noqa: F401  (boot: tests patch cli.boot.probes)
from . import escalate as escalate_mod
from . import beacon, deck, diverge, jeles_store, market, merge_check, table, view
from .api import (  # noqa: F401  (re-exported: the tests and callers read these)
    ANCHOR,
    DESK,
    NESTED,
    KeysExposed,
    _keys,
    _last_boot,
    _law,
)
from .run import PKG

ROOT = PKG.parents[1]  # onescript -> one-script -> willow-bot
GROVE = Path(
    os.environ.get("ONESCRIPT_GROVE") or ROOT.parent / "willows-grove"
).expanduser()
CONSTITUTION = GROVE / "governance" / "CONSTITUTION.md"
FORGE = Path(os.environ.get("ONESCRIPT_FORGE") or ROOT.parent / "forge").expanduser()
CI = GROVE / ".github" / "workflows" / "tests.yml"


def _gate_cfg(no_tests: bool, ci_text: str, venv: Path, phone: bool = False) -> dict:
    return api._gate_cfg(no_tests, ci_text, venv, ROOT, GROVE, phone)


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
        defer = (
            " (deferred: runs at the next check-in that can)"
            if g.get("deferred")
            else ""
        )
        L.append(f"  {g['verdict']:12} {g['gate']}: {g['where']}{why}{defer}")
    for s in rep.get("settled", []):
        why = f" — {s['why']}" if s["why"] else ""
        L.append(
            f"  settled      {s['gate']}: {s['where']}: {s['verdict']}"
            f" (deferred at row {s['deferred_at']}){why}"
        )
    st = rep.get("store")
    if st:
        what = (
            f"attached ({st['how']}): {st['store']}"
            if st["state"] == "attached"
            else f"building in the background: {st['store']}"
            if st["state"] == "building"
            else f"unreachable — {st.get('why', '')}"
        )
        L.append(f"store: {what}")
    ss = rep.get("sessions")
    if ss:
        why = f" ({ss['why']})" if ss.get("why") else ""
        L.append(
            f"sessions: {ss['sealed']} sealed, {ss['waiting']} waiting on your seal{why}"
        )
    if rep["hard_close"]:
        L.append("HARD CLOSE:")
        L += [f"  {x}" for x in rep["lines"]]
        L.append("options: " + " | ".join(rep["options"]))
    else:
        L.append("open")
    return "\n".join(L)


def _kv(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k:
            raise SystemExit(f"--match wants W=value, got {p!r}")
        out[k] = v
    return out


def _spec(args) -> dict:
    spec = {
        "by": [w for w in args.by.split(",") if w],
        "match": _kv(args.match),
        "upto": args.upto,
    }
    if getattr(args, "seed_row", None) is not None and args.seed_top is not None:
        spec["seed"] = {"row": args.seed_row, "top": args.seed_top}
    return spec


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _name_path(spec: str) -> tuple[str, Path]:
    name, sep, path = spec.partition("=")
    if not (sep and name and path):
        raise argparse.ArgumentTypeError(f"want NAME=PATH, got {spec!r}")
    return name, Path(path)


def _session_line(ss: dict) -> str:
    """Check-out's one line about the session's seal."""
    if "subject" not in ss:
        return f"session: {ss.get('state')} — {ss.get('why', '')}"
    pair = ss["pair"]
    said = (
        "proposed to Nestor: seal it there (yes, these were my actions) or deny it"
        if pair.get("state") == "proposed"
        else f"not proposed — {pair.get('why', '')}"
    )
    return f"session: {ss['actions']} action(s), {ss['subject']}; {said}"


def _siblings(parent: Path) -> dict[str, Path]:
    """Every git clone beside willow-bot, willow-bot included, by directory name:
    the repo set when no --repo is given."""
    try:
        kids = sorted(parent.iterdir())
    except OSError:
        return {}
    return {d.name: d for d in kids if d.is_dir() and (d / ".git").exists()}


def _bot_venv() -> Path | None:
    """The bot's venv by the bot's own rule (flatten F-17, `willow_bot.paths`):
    ``<box>/willow-bot/venvs/willow-bot`` (D2: the venvs live in the bot), or
    the old ``<box>/venvs/willow-bot`` while only that one exists, where the box
    is ``$WILLOW_HOME``, else ``$WILLOW_VAULT_BOX``. No box: None, and the
    toolchain gate says why (`api._gate_cfg`); never a guessed path."""
    paths = api.bot_paths()
    try:
        new, old = (
            paths.venvs_dir() / "willow-bot",
            paths.legacy_venvs_dir() / "willow-bot",
        )
    except paths.BoxNotConfigured:
        return None
    return old if not new.is_dir() and old.is_dir() else new


def _frame():
    """prompt.py, the one frame both seats use (Rat's and this hook): the
    named middle, so what the model is shown can't drift between them."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "onescript_prompt", ROOT / "one-script" / "prompt.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prompt_hook(cfg, args, raw) -> int:
    """Every prompt is a bite (willows-grove one-script/workflow.md §2c): the
    hook's event on stdin, its prompt looked up in the box, the result framed
    by prompt.py into the turn. The prompt's words never reach argv or the
    record (the `lookup` row keeps its hash). A crash exits 2, which blocks
    the prompt: it never fails open."""
    try:
        event = json.load(sys.stdin)
        text = event.get("prompt", "") if isinstance(event, dict) else ""
        frame = _frame()
        cap = args.max_chars
        if cap is None:
            cap = frame.cap_from(os.environ.get("ONESCRIPT_MAX_CHARS"))
        elif cap < 1:
            raise ValueError("cap must be positive")
        where = args.embed or os.environ.get("ONESCRIPT_EMBED") or None
        cfg = dataclasses.replace(
            cfg, embed=Path(where).expanduser() if where else None
        )
        res = api.prompt(cfg, text if isinstance(text, str) else "", cap, argv=raw)
        out = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": frame.context(res["doc"]),
        }
        print(json.dumps({"hookSpecificOutput": out}))
        return 0
    except Exception:  # noqa: BLE001 — any failure blocks; it never fails open
        return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="onescript", allow_abbrev=False)
    p.add_argument("--box", type=Path, default=ROOT / ".flow" / "onescript")
    p.add_argument(
        "--repo",
        dest="repos",
        action="append",
        default=[],
        type=_name_path,
        metavar="NAME=PATH",
        help="where an indexed repo is read from, for the store, seed and serve;"
        " with none, every clone beside willow-bot, which a check-in indexes"
        " when the pile has no index",
    )
    p.add_argument(
        "--keys", type=Path, default=ROOT / ".flow" / "onescript-keys" / "keys.json"
    )
    p.add_argument(
        "--venv",
        type=Path,
        default=_bot_venv(),
        help="the venv whose tools the toolchain gate checks (the bot's)",
    )
    p.add_argument("--now", help="fixed clock, for replays and tests")
    p.add_argument(
        "--no-tests",
        action="store_true",
        help="skip the tests gate (a gate row says it was skipped)",
    )
    p.add_argument(
        "--phone",
        action="store_true",
        help="this run is on the phone: a gate whose tool or clone is missing "
        "defers. Without it, a missing tool hard-closes",
    )
    p.add_argument(
        "--keyring",
        type=Path,
        default=api.default_keyring(),
        help="the public-only export of the operator's keyring ($WILLOW_KEYRING, "
        "else $WILLOW_HOME/config/verifiers.public.json); a Nestor seal is "
        "checked by it. Make it with `keys export`",
    )
    p.add_argument(
        "--nestor-db",
        type=Path,
        default=api.default_nestor_db(),
        help="Nestor's store, read-only ($WILLOW_NESTOR_DB, else $NESTOR_DB): a "
        "pair seals only while it is sealed there",
    )
    p.add_argument(
        "--drand",
        action="store_true",
        help="read table forecasts' beacon rounds from drand over HTTPS (BLS not "
        "checked). Without it the box is offline and a table outcome is "
        "`unreachable`, said (W4 break 1)",
    )
    p.add_argument(
        "--prices",
        action="store_true",
        help="read market calls' closes from stooq.com's daily CSV over HTTPS. "
        "Without it no market call resolves, and its outcome is `unreachable`, said",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    ks = sub.add_parser("keys", help="the operator's keyring export (run off the box)")
    ksub = ks.add_subparsers(dest="keys_cmd", required=True)
    ex = ksub.add_parser(
        "export", help="write the public ed25519 entries of a signing keyring"
    )
    ex.add_argument("--from", dest="src", type=Path, required=True)
    ex.add_argument(
        "--to",
        dest="dst",
        type=Path,
        default=api.default_keyring(),
        help="default: the box's keyring path (verifiers.public.json)",
    )
    mc = sub.add_parser(
        "merge-check",
        help="the merge gate: can this chunk come in and leave the one script intact "
        "(off the box; --record writes one-script/merged/<chunk>.json only on a pass)",
    )
    mc.add_argument("attestation", type=Path, help="the chunk's merge.json")
    mc.add_argument("--json", dest="as_json", action="store_true")
    mc.add_argument("--record", dest="mc_record", action="store_true")
    mc.add_argument("--box", dest="mc_box", type=Path, default=None,
                    help="a box whose record's chain and anchor must verify (gate 2)")
    mc.add_argument("--repos", dest="mc_repos", type=Path, default=None,
                    help="the directory holding willow-bot and its siblings "
                    "(default: beside this willow-bot, or $ONESCRIPT_HOME's)")
    mc.add_argument("--skip-suite", action="store_true",
                    help="do not run gate 1; it then FAILS as not run, and never records")
    mc.add_argument("--python", dest="mc_python", action="append", default=[],
                    type=_name_path, metavar="REPO=PYTHON",
                    help="the interpreter for a repo's own tests (default: this one)")
    dv = sub.add_parser(
        "diverge",
        help="where two refs (or two boxes' chains) part, down to the smallest unit "
        "(off the box; --record appends one `diverge` row to --box)",
    )
    dv.add_argument("ref_a", nargs="?", metavar="A")
    dv.add_argument("ref_b", nargs="?", metavar="B")
    dv.add_argument("--repo", dest="dv_repo", action="append", default=[], metavar="NAME",
                    help="a repo under --repos to compare (repeatable; default willow-bot)")
    dv.add_argument("--repos", dest="dv_repos", type=Path, default=None,
                    help="the directory holding the repos (default: beside this willow-bot)")
    dv.add_argument("--json", dest="as_json", action="store_true")
    dv.add_argument("--record", dest="dv_record", action="store_true")
    dv.add_argument("--behaviour", dest="dv_behaviour", action="store_true",
                    help="level 4: merge in a throwaway clone and run each side's changed tests")
    dv.add_argument("--box-a", dest="dv_box_a", type=Path, default=None)
    dv.add_argument("--box-b", dest="dv_box_b", type=Path, default=None)
    sub.add_parser("checkin")
    sd = sub.add_parser(
        "seed", help="the first bite's words against the box; once per check-in"
    )
    sd.add_argument("bite")
    sd.add_argument(
        "--repo",
        action="append",
        default=[],
        type=_name_path,
        metavar="NAME=PATH",
        help="where an indexed repo is read from (default: beside willow-bot)",
    )
    sd.add_argument(
        "--exact",
        action="store_true",
        help="count exact files and lines by the scan (default: the store's chunks)",
    )
    fb = sub.add_parser(
        "forge", help="the Forge's first bite, this session's pile as its box"
    )
    fb.add_argument("bite")
    fb.add_argument("--project", default="one-script", help="the Forge project id")
    fb.add_argument("--builder", default="willow", help="the Forge builder id")
    fb.add_argument(
        "--root", type=Path, help="the Forge's checkpoint root (default: its own)"
    )
    lk = sub.add_parser(
        "lookup", help="the bite across sessions' stores, merged by hash; a view"
    )
    lk.add_argument("bite")
    lk.add_argument(
        "--other", action="append", default=[], type=Path, help="another session's box"
    )
    pr = sub.add_parser(
        "prompt",
        help="Claude Code UserPromptSubmit: the prompt (stdin) as a bite, served into the turn",
    )
    pr.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="the cap on the served text (default: $ONESCRIPT_MAX_CHARS, else none)",
    )
    pr.add_argument(
        "--embed",
        type=Path,
        default=None,
        help="WordLlama's local file dir: fuse the embedder rung (default: $ONESCRIPT_EMBED)",
    )
    dc = sub.add_parser(
        "decline", help="the human's No to an offer: not offered again for that key"
    )
    dc.add_argument("key", help="the offer's key, as NEEDS YOU lists it")
    dc.add_argument(
        "--proof", help="the human's HMAC proof over decline:<key>, to seal the No"
    )
    t = sub.add_parser("turn")
    t.add_argument("bite")
    t.add_argument(
        "--proposal", type=Path, help="the model's rows: JSONL of path/data/cites/claim"
    )
    es = sub.add_parser(
        "escalate", help="the deterministic chain on the sealed served scope"
    )
    es.add_argument("task")
    es.add_argument("--model", default=escalate_mod.DEFAULT_MODEL)
    es.add_argument(
        "--piece-chars",
        type=int,
        default=escalate_mod.PIECE_CHARS,
        help="the most characters in one piece handed to the local rung",
    )
    es.add_argument("--rung-timeout", type=float, default=escalate_mod.RUNG_TIMEOUT_S)
    es.add_argument(
        "--ratatosk",
        default="ratatosk",
        help="the local rung's command (it is run with --onescript ...)",
    )
    sub.add_parser(
        "pooled",
        help="the unsealed pass proposals on record, one JSON object per line",
    )
    sub.add_parser(
        "resume",
        help="the last human-sealed scope's spec (with its upto) and subject, as JSON",
    )
    sub.add_parser(
        "needs",
        help="the NEEDS YOU lines the morning screen would list, as JSON; read-only",
    )
    co = sub.add_parser("checkout")
    co.add_argument(
        "--human-key",
        type=Path,
        help="the human's key, hex, in a file outside the box (0600): wager "
        "outcomes then count and GRADES shows their scores",
    )
    # the forecast lane: names as forge-convergence.md:643-645 (SPEC R1)
    fc = sub.add_parser(
        "forecast", help="a wager row: the forecast, before the outcome"
    )
    fc.add_argument("id")
    fc.add_argument(
        "--dist",
        type=Path,
        required=True,
        help="JSON list of {path, p, band?, tags?}; the ps sum to 1",
    )
    fc.add_argument("--by", default="desk", help="a role: human, desk, oracle, coin")
    fc.add_argument(
        "--committed",
        type=Path,
        help="JSON {sha256, beacon_then, target}: a commitment made outside the record",
    )
    fc.add_argument(
        "--table",
        type=Path,
        help="JSON {world, sheets_sha}: the world a table forecast is about",
    )
    fc.add_argument(
        "--deck",
        type=Path,
        help="JSON {question, beacon_round}: one of the predictions deck's questions",
    )
    fc.add_argument(
        "--market",
        type=Path,
        help="JSON {symbol, kind: up|above, close_date[, threshold]}: a market call",
    )
    # the honest forecast ledger: market calls (market.py)
    mr = sub.add_parser(
        "market-reveal",
        aliases=["market_reveal"],
        help="the closes for the symbols called on a date, as market rows",
    )
    mr.add_argument("close_date", help="YYYY-MM-DD, the close the calls name")
    mo = sub.add_parser(
        "market-outcome",
        aliases=["market_outcome"],
        help="read-only: a market call's outcome, proposed for the human to seal",
    )
    mo.add_argument("wager", help="the wager row's hash, as forecast printed it")
    mo.add_argument(
        "--human-key", type=Path, help="the human's key, hex, outside the box"
    )
    # the predictions deck as a reader (flatten F6)
    dr = sub.add_parser(
        "deck-reveal",
        aliases=["deck_reveal"],
        help="the beacon's answers to the deck questions wagered on a round",
    )
    dr.add_argument("round", type=int, help="the drand round the forecasts named")
    do = sub.add_parser(
        "deck-outcome",
        aliases=["deck_outcome"],
        help="read-only: a deck wager's outcome, proposed for the human to seal",
    )
    do.add_argument("wager", help="the wager row's hash, as forecast printed it")
    do.add_argument(
        "--human-key", type=Path, help="the human's key, hex, outside the box"
    )
    # the table's rows (SPEC-documented §9 J4, §12 J9; SPEC R9, R10)
    vd = sub.add_parser(
        "verdict", help="one code-written verdict row as the table identity"
    )
    vd.add_argument(
        "--json",
        type=Path,
        required=True,
        dest="row_json",
        help="the row, as JSON; '-' reads stdin",
    )
    lh = sub.add_parser(
        "ledger-head",
        aliases=["ledger_head"],
        help="the ai-game-master ledger head as one agm_head verdict row (R9)",
    )
    lh.add_argument("--ledger", default="ai-game-master")
    lh.add_argument("--n", type=int, required=True, help="the ledger's row count")
    lh.add_argument("--head", required=True, help="the ledger's last hash")
    lh.add_argument("--db-sha", help="sha256 of campaign.db as anchored")
    lh.add_argument("--db", help="where campaign.db was read")
    so = sub.add_parser(
        "seal-outcome",
        aliases=["seal_outcome"],
        help="the human's seal on what happened to one wager",
    )
    so.add_argument("wager", help="the wager row's hash, as forecast printed it")
    so.add_argument(
        "--outcome",
        required=True,
        help='JSON: {"path_index": i}, {"path": p} or {"winner": w}',
    )
    so.add_argument("--proof", help="the human's HMAC proof over the outcome subject")
    so.add_argument(
        "--pair",
        type=Path,
        help="a sealed Nestor pair, as JSON, whose conclusion is the outcome subject",
    )
    so.add_argument(
        "--verifier",
        action="append",
        help="take a pair signed by one of these names, for this seal only",
    )
    lo = sub.add_parser(
        "lore", help="a sealed world/*.md proposal becomes lore rows (SPEC R11)"
    )
    lo.add_argument("subject", help="the proposal:<hash> the human sealed")
    ru = sub.add_parser(
        "rules",
        help="a sealed rules/*.md @markdownai proposal becomes rules rows (T-mai-2)",
    )
    ru.add_argument("subject", help="the proposal:<hash> the human sealed")
    sf = sub.add_parser(
        "seal-forecast",
        aliases=["seal_forecast"],
        help="the human says a forecast is theirs (by: human counts only then)",
    )
    sf.add_argument("wager", help="the wager row's hash, as forecast printed it")
    sf.add_argument("--proof", help="the human's HMAC proof over forecast:<hash>")
    st = sub.add_parser(
        "seal-tip", aliases=["seal_tip"], help="the human seals a tip of the record"
    )
    st.add_argument(
        "subject", help="tip:<n>:<hash>, as forecast or seal-outcome printed it"
    )
    st.add_argument("--proof", help="the human's HMAC proof over the tip subject")
    for name, alias in (("reconcile", None), ("read-misses", "read_misses")):
        rc = sub.add_parser(
            name,
            aliases=[alias] if alias else [],
            help="read-only: the wagers lined up against the human's sealed outcomes",
        )
        rc.add_argument(
            "--human-key",
            type=Path,
            help="the human's key, hex, in a file outside the box (0600)",
        )
    for name in ("scope", "serve"):
        s = sub.add_parser(name)
        s.add_argument("--by", default="who", help="W's to group by, comma-separated")
        s.add_argument("--match", action="append", default=[], metavar="W=value")
        s.add_argument(
            "--upto", type=int, help="the last record row the stack is drawn from"
        )
        s.add_argument(
            "--seed-top",
            type=int,
            help="also stack the seed's top N chunks of the repos (--by '' for those alone)",
        )
        s.add_argument(
            "--max-chars",
            type=int,
            help="the cap on served text; over it, empty (scope: says if it fits)",
        )
        if name == "serve":
            s.add_argument("--seed-row", type=int, help="the seed row scope named")
    sl = sub.add_parser("seal")
    sl.add_argument(
        "subject",
        help="serve:<hash> (a scope), proposal:<hash>, reject:proposal:<hash> (the "
        "human's rejection of a proposal) or session:<hash> (a session)",
    )
    sl.add_argument("--proof", help="the human's HMAC proof over the subject")
    sl.add_argument(
        "--pair", type=Path, help="a sealed Nestor pair, as JSON, to find in the store"
    )
    sl.add_argument(
        "--verifier",
        action="append",
        help="take a pair signed by one of these names, for this seal only; it "
        "is not stored and does not bind later readers (the keyring does)",
    )
    args = p.parse_args(argv)
    raw = list(argv if argv is not None else sys.argv[1:])

    if args.cmd == "keys":  # off the box: no record, no invocation row
        if args.dst is None:
            print(
                "refused: no --to, and no $WILLOW_KEYRING or $WILLOW_HOME to default it"
            )
            return 2
        try:
            res = nestor_seal.export_public(args.src, args.dst, box=args.box)
        except nestor_seal.Unverifiable as e:
            print(f"refused: {e}")
            return 2
        print(f"exported {len(res['exported'])} public key(s) to {args.dst}")
        for name in res["exported"]:
            print(f"  kept     {name}")
        for name in res["dropped_hmac"]:
            print(f"  dropped  {name} (HMAC: a shared secret, not a public key)")
        return 0

    if args.cmd == "diverge":  # off the box: it reads refs; --record appends one row to --box
        return diverge.run_cli(
            args.ref_a,
            args.ref_b,
            repos=args.dv_repos,
            names=args.dv_repo or None,
            as_json=args.as_json,
            record=args.dv_record,
            box=args.box,
            behaviour=args.dv_behaviour,
            box_a=args.dv_box_a,
            box_b=args.dv_box_b,
            clock=(lambda: args.now) if args.now else None,
        )

    if args.cmd == "merge-check":  # off the box: no record, no invocation row
        return merge_check.run_cli(
            args.attestation,
            repos=args.mc_repos,
            box=args.mc_box,
            as_json=args.as_json,
            record=args.mc_record,
            skip_suite=args.skip_suite,
            python={k: str(v) for k, v in args.mc_python},
        )

    cfg = api.Config(
        box=args.box,
        keys=args.keys,
        constitution=CONSTITUTION,
        ci=CI,
        root=ROOT,
        grove=GROVE,
        venv=args.venv,
        now=args.now,
        no_tests=args.no_tests,
        keyring=args.keyring,
        nestor_db=args.nestor_db,
        phone=args.phone,
        beacon=beacon.drand_reader if args.drand else None,
        market=market.stooq_reader if args.prices else None,
        repos=dict(args.repos) or None,
        store_background=True,
        forge=FORGE,
        index_repos=dict(args.repos) or _siblings(ROOT.parent) or None,
        seal_at_end=True,
        jeles=jeles_store.default_store(),
    )

    args.cmd = args.cmd.replace("_", "-")  # seal_outcome is seal-outcome
    if args.cmd == "forecast":
        try:
            dist = json.loads(args.dist.read_text(encoding="utf-8"))
            committed = (
                json.loads(args.committed.read_text(encoding="utf-8"))
                if args.committed
                else None
            )
        except (OSError, ValueError) as e:
            print(f"refused: {type(e).__name__}: {e}")
            return 2
        try:
            tbl = (
                json.loads(args.table.read_text(encoding="utf-8"))
                if args.table
                else None
            )
            dk = (
                json.loads(args.deck.read_text(encoding="utf-8")) if args.deck else None
            )
            mk = (
                json.loads(args.market.read_text(encoding="utf-8"))
                if args.market
                else None
            )
        except (OSError, ValueError) as e:
            print(f"refused: {type(e).__name__}: {e}")
            return 2
        res = api.forecast(
            cfg,
            args.id,
            dist,
            by=args.by,
            committed=committed,
            table=tbl,
            deck=dk,
            market=mk,
            argv=raw,
        )
        if "refused" not in res:
            print(f"wager {res['subject']}")  # the subject an outcome seal names
            print(f"tip {res['tip']}")  # sign it to commit the forecast
            return 0
    elif args.cmd in ("market-reveal", "market-outcome"):
        if args.cmd == "market-reveal":
            res = market.reveal(cfg, args.close_date, argv=raw)
            for r in res.get("rows", []):
                print(f"market {r['symbol']} {r['close_date']} closed {r['close']} "
                      f"(before: {r['prev_close']}) row {r['n']}")  # fmt: skip
        else:
            res = market.outcome(cfg, args.wager, human_key=args.human_key, argv=raw)
            if res.get("state") == "proposed":
                print(f"seal {res['subject']}")  # the human signs it, or nothing counts
        said = {k: v for k, v in res.items() if k != "rows"}
        print(json.dumps(said, sort_keys=True, ensure_ascii=True, default=str))
        return 0 if res.get("state") in ("populated", "empty", "proposed") else 1
    elif args.cmd in ("deck-reveal", "deck-outcome"):
        if args.cmd == "deck-reveal":
            res = deck.reveal(cfg, args.round, argv=raw)
            for r in res.get("rows", []):
                print(f"deck {r['question']} {r['verdict']} row {r['n']}")
        else:
            res = deck.outcome(cfg, args.wager, human_key=args.human_key, argv=raw)
            if res.get("state") == "proposed":
                print(f"seal {res['subject']}")  # the human signs it, or nothing counts
        said = {k: v for k, v in res.items() if k != "rows"}
        print(json.dumps(said, sort_keys=True, ensure_ascii=True, default=str))
        return 0 if res.get("state") in ("populated", "empty", "proposed") else 1
    elif args.cmd == "seal-outcome":
        try:
            outcome = json.loads(args.outcome)
        except ValueError as e:
            print(f"refused: --outcome is not JSON: {e}")
            return 2
        pair = json.loads(args.pair.read_text(encoding="utf-8")) if args.pair else None
        res = api.seal_outcome(
            cfg,
            args.wager,
            outcome,
            proof=args.proof,
            pair=pair,
            verifiers=args.verifier,
            argv=raw,
        )
        if res.get("sealed"):
            print(f"sealed {res['row']['subject']}")
            print(f"tip {res['tip']}")  # seal it, or this outcome is not covered
            if "counts_when" in res:  # the C6 gap: a Nestor seal needs a key tip
                print(f"counts when: {res['counts_when']}")
            print(f"rescope: {res['rescope']['why']}")  # SPEC R14: the last step
            for step in res["rescope"]["steps"]:
                print(f"  {step}")
            return 0
    elif args.cmd == "verdict":
        try:
            text = (
                sys.stdin.read()
                if str(args.row_json) == "-"
                else args.row_json.read_text(encoding="utf-8")
            )
            row = json.loads(text)
        except (OSError, ValueError) as e:
            print(f"refused: {type(e).__name__}: {e}")
            return 2
        res = table.verdict(cfg, row, argv=raw)
        if res.get("code") == 0:
            print(f"verdict row {res['row']['n']} {res['row']['hash']}")
            return 0
    elif args.cmd == "ledger-head":
        res = table.agm_head(
            cfg,
            n=args.n,
            head=args.head,
            ledger=args.ledger,
            db_sha=args.db_sha,
            db=args.db,
            argv=raw,
        )
        if res.get("code") == 0:
            print(f"agm_head row {res['row']['n']}")
            print(f"tip {res['tip']}")  # seal it: the human's tip covers the head
            return 0
    elif args.cmd == "lore":
        res = api.lore(cfg, args.subject, argv=raw)
        if "refused" not in res:
            print(f"lore {res['count']} row(s)")
            print(f"rescope: {res['rescope']['why']}")  # SPEC R14, as seal-outcome
            for step in res["rescope"]["steps"]:
                print(f"  {step}")
            return 0
    elif args.cmd == "rules":
        res = api.rules(cfg, args.subject, argv=raw)
        if "refused" not in res:
            print(f"rules {res['count']} row(s)")
            print(f"rescope: {res['rescope']['why']}")
            for step in res["rescope"]["steps"]:
                print(f"  {step}")
            return 0
    elif args.cmd == "seal-forecast":
        res = api.seal_forecast(cfg, args.wager, proof=args.proof, argv=raw)
    elif args.cmd == "seal-tip":
        res = api.seal_tip(cfg, args.subject, proof=args.proof, argv=raw)
        if "covers" in res:  # what the signature vouches for, before and after
            print(f"{args.subject} newly covers {len(res['covers'])} row(s):")
            for c in res["covers"]:
                print(f"  {view.cover_line(c)}")
            if res.get("sealed"):
                print("sealed")
                return 0
    elif args.cmd in ("reconcile", "read-misses"):
        verb = api.reconcile if args.cmd == "reconcile" else api.read_misses
        res = verb(cfg, human_key=args.human_key, argv=raw)
    elif args.cmd == "checkin":
        res = api.checkin(cfg, raw)
    elif args.cmd == "checkout":
        res = api.checkout(cfg, raw, human_key=args.human_key)
    elif args.cmd == "pooled":
        res = api.pooled(cfg, raw)
        if "refused" in res:  # stdout stays parseable: the reason goes to stderr
            print(f"refused: {res['refused']}", file=sys.stderr)
        else:  # JSON lines; an empty pool prints nothing and exits 0
            for item in res["pooled"]:
                print(json.dumps(item, sort_keys=True, ensure_ascii=True))
        return res["code"]
    elif args.cmd == "resume":
        res = api.resume(cfg, raw)
        if "refused" in res:  # stdout stays parseable: the reason goes to stderr
            print(f"refused: {res['refused']}", file=sys.stderr)
        else:
            _print_json({k: v for k, v in res.items() if k != "code"})
        return res["code"]
    elif args.cmd == "needs":
        res = api.needs(cfg, raw)
        if "refused" in res:  # stdout stays parseable: the reason goes to stderr
            print(f"refused: {res['refused']}", file=sys.stderr)
        else:
            _print_json({k: v for k, v in res.items() if k != "code"})
        return res["code"]
    elif args.cmd == "seed":
        res = api.seed(
            cfg, args.bite, repos=dict(args.repo), argv=raw, exact=args.exact
        )
    elif args.cmd == "forge":
        res = api.forge_bite(
            cfg,
            args.bite,
            project_id=args.project,
            builder_id=args.builder,
            root=args.root,
            argv=raw,
        )
    elif args.cmd == "lookup":
        res = api.lookup(cfg, args.bite, args.other, argv=raw)
    elif args.cmd == "prompt":
        return _prompt_hook(cfg, args, raw)
    elif args.cmd == "turn":
        rows = api.read_proposals(args.proposal) if args.proposal else None
        res = api.turn(cfg, args.bite, proposed=rows, argv=raw)
    elif args.cmd == "escalate":
        res = api.escalate(
            cfg,
            args.task,
            model=args.model,
            piece_chars=args.piece_chars,
            ratatosk=shlex.split(args.ratatosk),
            rung_timeout=args.rung_timeout,
            argv=raw,
        )
    elif args.cmd == "scope":
        res = api.scope(
            cfg,
            by=_spec(args)["by"],
            match=_kv(args.match),
            upto=args.upto,
            argv=raw,
            seed_top=args.seed_top,
            max_chars=args.max_chars,
        )
    elif args.cmd == "decline":
        res = api.decline_offer(cfg, args.key, argv=raw, proof=args.proof)
    elif args.cmd == "serve":
        spec = _spec(args)
        if spec["upto"] is None:
            print("refused: serve needs --upto, the row scope named")
            return 1
        if args.max_chars is None:
            print("refused: serve needs --max-chars; nothing is served uncapped")
            return 1
        res = api.serve(cfg, spec, max_chars=args.max_chars, argv=raw)
    else:
        seal = (
            api.reject_proposal
            if args.subject.startswith(api.REJECT + "proposal:")
            else api.seal_proposal
            if args.subject.startswith("proposal:")
            else api.seal_session
            if args.subject.startswith("session:")
            else api.seal_scope
        )
        pair = json.loads(args.pair.read_text(encoding="utf-8")) if args.pair else None
        res = seal(
            cfg,
            args.subject,
            proof=args.proof,
            pair=pair,
            verifiers=args.verifier,
            argv=raw,
        )

    if "refused" in res:
        print(f"refused: {res['refused']}")
    elif args.cmd == "checkin":
        if "wont_open" in res:
            print(f"BOX WON'T OPEN: {res['wont_open']}")
        else:
            print(_checkin_screen(res["report"], res["nested"]))
    elif args.cmd == "checkout":
        print(res["screen"])
        if res.get("session"):
            print(_session_line(res["session"]))
    elif args.cmd == "turn":
        out = dict(res["out"])
        if "proposals" in out:  # own_idea leads each proposal row (null: never parsed)
            out["proposals"] = [
                {"own_idea": p.get("own_idea"), **{k: v for k, v in p.items()}}
                for p in out["proposals"]
            ]
        print(json.dumps(out, indent=1, default=str))
    else:
        _print_json({k: v for k, v in res.items() if k != "code"})
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
