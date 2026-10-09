"""The one script, run on this box. A thin wrapper over `onescript.api`.

    cd one-script                           # in willow-bot
    python3 -m onescript checkin            # boot: record, probes, the four gates
    python3 -m onescript scope --by who     # code proposes a stack: card + subject
    python3 -m onescript seal SUBJECT ...   # the human's seal over that subject
    python3 -m onescript serve --by who     # write the one file the model reads
    python3 -m onescript turn "the bite"    # one turn as the desk
    python3 -m onescript turn "the bite" --proposal f.jsonl   # the model's rows
    python3 -m onescript escalate "the task"  # D0, then small pieces to the local rung
    python3 -m onescript pooled             # unsealed pass proposals, JSON lines
    python3 -m onescript checkout           # reverse, then the morning screen
    python3 -m onescript keys export --from KEYRING [--to FILE]  # public half only

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
import json
import os
import shlex
import sys
from pathlib import Path

from . import api, boot, nestor_seal  # noqa: F401  (boot: tests patch cli.boot.probes)
from . import escalate as escalate_mod
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
    return {
        "by": [w for w in args.by.split(",") if w],
        "match": _kv(args.match),
        "upto": args.upto,
    }


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


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
    sub.add_parser("checkin")
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
    sub.add_parser("checkout")
    for name in ("scope", "serve"):
        s = sub.add_parser(name)
        s.add_argument("--by", default="who", help="W's to group by, comma-separated")
        s.add_argument("--match", action="append", default=[], metavar="W=value")
        s.add_argument(
            "--upto", type=int, help="the last record row the stack is drawn from"
        )
        if name == "serve":
            s.add_argument(
                "--max-chars", type=int, help="the cap on served text; over it, empty"
            )
    sl = sub.add_parser("seal")
    sl.add_argument("subject", help="serve:<hash> (a scope) or proposal:<hash>")
    sl.add_argument("--proof", help="the human's HMAC proof over the subject")
    sl.add_argument(
        "--pair", type=Path, help="a sealed Nestor pair, as JSON, to find in the store"
    )
    sl.add_argument("--verifier", action="append", help="only these may seal")
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
    )

    if args.cmd == "checkin":
        res = api.checkin(cfg, raw)
    elif args.cmd == "checkout":
        res = api.checkout(cfg, raw)
    elif args.cmd == "pooled":
        res = api.pooled(cfg, raw)
        if "refused" in res:  # stdout stays parseable: the reason goes to stderr
            print(f"refused: {res['refused']}", file=sys.stderr)
        else:  # JSON lines; an empty pool prints nothing and exits 0
            for item in res["pooled"]:
                print(json.dumps(item, sort_keys=True, ensure_ascii=True))
        return res["code"]
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
        )
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
            api.seal_proposal
            if args.subject.startswith("proposal:")
            else api.seal_scope
        )
        pair = json.loads(args.pair.read_text()) if args.pair else None
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
