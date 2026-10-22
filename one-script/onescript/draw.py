"""draw.py -- serendipity, in the loop (IDEAS.md 7).

The operator, 2026-10-22: "build the loop, then run my joins through it."
One chunk from each of two repos, picked by randomness nobody chose, served
like any chunk, so a model can say what joins them and the human can seal it:

  draw     ``draw(cfg)`` -- the pick, written as one ``draw`` row whose
           ``rank`` holds the two chunks in the shape a seed row ranks them
           ({repo, path, line, chunk}), so the row is the chunks' receipt
  scope    ``api.scope(cfg, by=(), draw=True)`` -- the stack is the draw's two
           chunks (`serve.RECEIPTS`: a chunk's receipt is a seed row or a
           draw row, chain-checked the same way)
  serve    ``api.serve`` -- the two chunks, read at their blobs
  turn     the model's join comes back as one proposal row: a
           ``world/joins/*.md`` document citing both served ids
  seal     the human's, over that proposal; then ``api.lore`` makes each line
           of the sealed join a ``lore`` row

The randomness: a drand round's, through the box's beacon reader
(``Config.beacon``, the CLI's ``--drand``), its round on the row; else the
caller's (``randomness``), and the row says ``given``, since a caller can pick
it; or no randomness at all, two chunks the caller names (``chosen``), and the
row says ``chosen``: the same door for "compare these two" (IDEAS.md 86).
None of these: ``unreachable``, said. Proposal files (`seed.is_proposal`) are
never drawn: an idea is not the corpus (IDEAS.md 103).

Same index, same randomness, same pair. Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from . import api, beacon, pile, seed
from .states import EMPTY, POPULATED, UNREACHABLE
from .stream import StreamRandom

KIND = "draw"
QUESTION = "what joins these?"
_HEX = re.compile(r"[0-9a-f]{16,128}")


def pick(index: dict, repos: dict, randomness: str) -> list[dict]:
    """Two chunks, each from a different repo, as a seed row ranks them. []
    when fewer than two repos hold a chunk."""
    by_repo: dict[str, list] = {}
    for name, f, text in seed.Texts(index, repos):
        if seed.is_proposal(f["path"]):
            continue
        for line, body, _ in pile.chunks(text):
            sha = hashlib.sha256(body.encode()).hexdigest()
            by_repo.setdefault(name, []).append((f["path"], line, sha))
    names = sorted(n for n, cs in by_repo.items() if cs)
    if len(names) < 2:
        return []
    rng = StreamRandom.from_seed(bytes.fromhex(randomness), label=KIND.encode())
    first = rng.choice(names)
    second = rng.choice([n for n in names if n != first])
    out = []
    for name in (first, second):
        path, line, sha = rng.choice(sorted(by_repo[name]))
        out.append({"repo": name, "path": path, "line": line, "chunk": sha})
    return out


def located(index: dict, repos: dict, chosen: list[dict]) -> tuple[list, str | None]:
    """The chunks the caller names, each {repo, path, line}, as a seed row ranks
    them: (pair, None), or ([], why) when one isn't a chunk start in the pile."""
    want = {(c.get("repo"), c.get("path"), c.get("line")) for c in chosen}
    found = {}
    for name, f, text in seed.Texts(index, repos):
        for line, body, _ in pile.chunks(text):
            if (name, f["path"], line) in want:
                found[(name, f["path"], line)] = hashlib.sha256(
                    body.encode()
                ).hexdigest()
    out = []
    for c in chosen:
        key = (c.get("repo"), c.get("path"), c.get("line"))
        if key not in found:
            return [], f"{key[0]}:{key[1]}:{key[2]} is not where a chunk starts"
        out.append(
            {"repo": key[0], "path": key[1], "line": key[2], "chunk": found[key]}
        )
    return out, None


def _randomness(cfg, given: str | None) -> tuple[str | None, dict | None, str | None]:
    """(randomness, source, None) or (None, None, why)."""
    if given is not None:
        if not isinstance(given, str) or not _HEX.fullmatch(given):
            return None, None, "randomness is 16 to 128 lowercase hex characters"
        return given, {"given": True}, None
    rnd, why = beacon.latest(cfg.beacon)
    if rnd is None:
        return None, None, f"{why} (--drand), and no randomness was given"
    try:
        got = cfg.beacon(rnd)["randomness"]
    except Exception as e:  # any failure of the reader: said, never guessed
        return None, None, f"drand round {rnd}: {type(e).__name__}: {e}"
    return got, {"drand": rnd}, None


def draw(
    cfg: api.Config,
    randomness: str | None = None,
    *,
    chosen: list[dict] | None = None,
    argv: Iterable[str] = ("draw",),
) -> dict:
    """Pick two chunks (or take the two ``chosen``) and write the ``draw`` row;
    see the module's doc."""
    if chosen is not None and (randomness is not None or len(chosen) != 2):
        return api._refused("chosen is exactly two chunks, and no randomness", 1)
    run, info = api._open(cfg, KIND, argv)
    if run is None:
        return info
    if (refusal := api._need_open(run, KIND)) is not None:
        return refusal
    got, source, seed_id = None, {"chosen": True}, None
    if chosen is None:
        got, source, why = _randomness(cfg, randomness)
        if got is None:
            return {"state": UNREACHABLE, "why": why, "code": 1}
        seed_id = StreamRandom.from_seed(
            bytes.fromhex(got), label=KIND.encode()
        ).seed_id
    index, sha, why = seed.read_index(run.rec)
    if index is None:
        return {"state": UNREACHABLE, "why": why, "code": 1}
    repos = api._repos(cfg, index)
    if chosen is None:
        pair = pick(index, repos, got)
    else:
        pair, why = located(index, repos, chosen)
        if why is not None:
            return api._refused(why, 1)
    if not pair:
        return {"state": EMPTY, "why": "a draw needs two repos with chunks", "code": 0}
    row = run.rec.append(
        KIND,
        run.sys,
        where=seed.INDEX,
        index_sha256=sha,
        rank=pair,
        source=source,
        seed_id=seed_id,
        question=QUESTION,
    )
    return {
        "state": POPULATED,
        "row": row["n"],
        "pair": pair,
        "source": source,
        "question": QUESTION,
        "code": 0,
    }


def last_draw(rows: list[dict]) -> dict | None:
    """This check-in's newest ``draw`` row, or None."""
    return next((r for r in reversed(seed.since_boot(rows)) if r["kind"] == KIND), None)
