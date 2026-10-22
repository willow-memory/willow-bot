#!/usr/bin/env python3
"""Seal the joins: the operator's three serendipity joins, through the loop.

    python3 compiled/seal_joins.py [--human-key FILE] [the CLI's own options]

The operator, 2026-10-22: "write the seals to include with the PR, so they
just land local." A seal is the human's proof over a proposal's exact hash,
made with the human's key, so it can't be written ahead of time: the served
ids it covers are keyed fresh at each check-in, and the key is the
operator's. What ships is everything up to it, and the seal step itself:

  for each join in joins/joins.json
    draw      the two chunks it names (`draw --chosen`, recorded as chosen)
    scope     `scope --draw`; serve reads them at their blobs
    turn      the join (joins/draw-N.md) as a world/joins/draw-N.md proposal
              citing both served ids: `pass` means awaiting the human
    seal      only with --human-key: the proof made here, from the key in that
              file (outside the box, 0600, as `checkout --human-key` reads it)
    lore      each line of the sealed join becomes a lore row

Without --human-key it stops at the seal, prints each proposal's subject and
the command that seals or declines it, and exits 42. The box must be checked
in first (`python3 -m onescript checkin`); the options after --human-key are
the CLI's (--box, --keys, --repo, ...), read the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, draw, gate  # noqa: E402

JOINS = HERE / "joins"
MAX_CHARS = 20_000  # the cap on the two served chunks


def manifest(folder: Path = JOINS) -> list[dict]:
    """Each join: its document's text and the two chunks it names."""
    out = []
    for j in json.loads((folder / "joins.json").read_text(encoding="utf-8")):
        chunks = [
            {"repo": r, "path": p, "line": int(n)}
            for r, p, n in (c.rsplit(":", 2) for c in j["chosen"])
        ]
        out.append(
            {
                "path": f"world/joins/{j['doc']}",
                "data": (folder / j["doc"]).read_text(encoding="utf-8"),
                "chosen": chunks,
            }
        )
    return out


def through_the_loop(cfg, join: dict, key: bytes | None) -> dict:
    """One join from draw to the seal (or to waiting on it)."""
    got = draw.draw(cfg, chosen=join["chosen"], argv=("draw", "--chosen"))
    if got.get("code") != 0:
        return {"step": "draw", **got}
    card = api.scope(cfg, by=(), draw=True, argv=("scope", "--draw"))
    if card.get("code") != 0:
        return {"step": "scope", **card}
    if key is not None and not cfg.seal_at_end:
        api.seal_scope(
            cfg, card["subject"], proof=gate.sign(key, "seal", card["subject"])
        )
    res = api.serve(cfg, card["spec"], max_chars=MAX_CHARS, argv=("serve",))
    if res.get("state") != api.POPULATED:
        return {"step": "serve", **res}
    ids = [t["id"] for t in res["doc"]["tables"]]
    row = {
        "path": join["path"],
        "data": join["data"],
        "cites": ids,
        "claim": "what joins these",
    }
    turn = api.take_proposals(cfg, [row], bite=f"what joins these: {join['path']}")
    (p,) = turn["out"]["proposals"]
    if p["verdict"] != "pass":
        return {"step": "turn", "verdict": p["verdict"], "why": p.get("why")}
    if key is None:
        return {"step": "seal", "waiting": p["subject"]}
    sealed = api.seal_proposal(
        cfg, p["subject"], proof=gate.sign(key, "seal", p["subject"])
    )
    if sealed.get("code") != 0:
        return {"step": "seal", **sealed}
    lore = api.lore(cfg, p["subject"])
    return {
        "step": "lore",
        "subject": p["subject"],
        "lore": lore.get("count"),
        "code": lore["code"],
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="seal_joins", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--human-key", type=Path, help="the human's key, hex, outside the box"
    )
    cli._load_everything()
    cli._add_globals(p)
    args = p.parse_args(argv)
    cfg = cli._config(args)
    key, why = api.read_human_key(args.human_key, cfg.box)
    if why is not None:
        print(f"refused: {why}")
        return 2
    waiting = 0
    for join in manifest():
        out = through_the_loop(cfg, join, key)
        print(json.dumps({"join": join["path"], **out}, sort_keys=True, default=str))
        if out.get("waiting"):
            waiting += 1
            print(
                f"  seal:    python3 -m onescript seal {out['waiting']} --proof <yours>"
            )
            print(f"  decline: python3 -m onescript seal {api.REJECT}{out['waiting']}")
        elif out["step"] != "lore" or out.get("code") != 0:
            return 1
    return api.ESCALATE if waiting else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
