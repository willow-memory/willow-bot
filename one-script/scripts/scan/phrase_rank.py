#!/usr/bin/env python3
"""phrase_rank: a bite's words and short phrases, ranked against the pile. No model.

    python3 phrase_rank.py "the bite" [--box BOX] [--repo NAME=PATH ...] [--top N]

Rung 1 of the escalation ladder, past single words. The pile is the box's
xref index (only while it hashes to its pile pointer); each file is read at
its indexed blob and counted only when its bytes hash to the index.

| Step | Rule |
|---|---|
| chunks | blank-line paragraphs; one over MAX is split at line breaks, never dropped |
| dedupe | identical chunks count once (SHA-256), with their copy count |
| fold | w -> w[:-1] for a trailing s, only when the stem is attested: df(stem) * ATTEST >= df(w) |
| pairs | adjacent words within a clause, kept when PMI > 1 (more often together than by chance) |
| rank | BM25 over the kept words and pairs, at its standard constants |

Measured 2026-10-18 on one bite over 8 repos: the paper the bite was about
ranked 1st, 4 of the top 12, in about a minute (a static embedder had it 2nd
in about two). MAX and ATTEST are placeholders for the operator's numbers.

Read only: writes nothing but stdout. Same box, same repos, same bite: same output.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import seed  # noqa: E402

WORD = re.compile(r"[^\W_]+")
CLAUSE = re.compile(r"[,.;:!?()\[\]{}|\"]+")
K1, B = 1.2, 0.75  # BM25's standard constants
ATTEST = 10  # placeholder
MAX, MIN = 500, 40  # chunk characters; MAX is a placeholder


def raw_grams(text: str) -> list[str]:
    out = []
    for clause in CLAUSE.split(text.lower()):
        w = WORD.findall(clause)
        out += w + [f"{a} {b}" for a, b in zip(w, w[1:])]
    return out


def chunks(text: str):
    """(line, chunk) pairs; a block over MAX is split at line breaks."""
    line = 1
    for block in re.split(r"(\n\s*\n)", text):
        s = block.strip()
        if s and len(s) <= MAX:
            yield line, s
        elif s:
            buf, start, n = [], line, line
            for ln in block.split("\n"):
                if buf and sum(map(len, buf)) + len(ln) > MAX:
                    yield start, "\n".join(buf).strip()
                    buf, start = [], n
                buf.append(ln)
                n += 1
            if buf:
                yield start, "\n".join(buf).strip()
        line += block.count("\n")


def forms(w: str) -> set[str]:
    return {w, w + "s"} | ({w[:-1]} if w.endswith("s") else set())


def rank(index: dict, repos: dict, bite: str) -> dict:
    words = set().union(*(forms(w) for w in WORD.findall(bite.lower())))
    pairs = set()
    for g in raw_grams(bite):
        if " " in g:
            a, b = g.split(" ")
            pairs |= {f"{x} {y}" for x in forms(a) for y in forms(b)}
    want = words | pairs

    paras, df_raw, copies = [], collections.Counter(), collections.Counter()
    for name, arr in sorted(index.get("arrival", {}).items()):
        path = repos.get(name)
        if path is None or not Path(path).is_dir():
            continue
        files = [
            f
            for f in arr.get("files", [])
            if f.get("state") == "present" and f.get("blob_match")
        ]
        blobs = seed._blobs(Path(path), sorted({f["git_blob"] for f in files}))
        for f in files:
            d = blobs.get(f["git_blob"])
            if d is None or hashlib.sha256(d).hexdigest() != f["sha256"] or b"\0" in d:
                continue
            try:
                text = d.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line, s in chunks(text):
                if len(s) < MIN:
                    continue
                h = hashlib.sha256(s.encode()).hexdigest()
                copies[h] += 1
                if copies[h] > 1:
                    continue
                g = raw_grams(s)
                tf = collections.Counter(x for x in g if x in want)
                for x in tf:
                    df_raw[x] += 1
                paras.append(((name, f["path"], line, h), tf, len(g)))
    if not paras:
        return {"state": "unreachable", "hits": []}

    def fold(w: str) -> str:
        if len(w) >= 3 and w.endswith("s") and not w.endswith("ss"):
            stem = w[:-1]
            if df_raw[stem] and df_raw[stem] * ATTEST >= df_raw[w]:
                return stem
        return w

    fmap = {w: fold(w) for w in words}

    def fg(x: str) -> str:
        return " ".join(fmap.get(t, t) for t in x.split(" "))

    folded = []
    for where, tf, n in paras:
        c = collections.Counter()
        for x, k in tf.items():
            c[fg(x)] += k
        folded.append((where, c, n))
    n_all = len(folded)
    avg = sum(p[2] for p in folded) / n_all
    df = collections.Counter(x for _, tf, _ in folded for x in tf)
    bite_grams = sorted({fg(g) for g in raw_grams(bite)})

    def pmi(p: str) -> float:
        a, b = p.split(" ")
        return df[p] * n_all / (df[a] * df[b]) if df[a] and df[b] else 0.0

    kept = [g for g in bite_grams if " " not in g and df[g]]
    kept += [g for g in bite_grams if " " in g and df[g] and pmi(g) > 1]
    idf = {g: math.log(1 + (n_all - df[g] + 0.5) / (df[g] + 0.5)) for g in kept}

    def bm25(tf, n) -> float:
        norm = K1 * (1 - B + B * n / avg)
        return sum(idf[g] * tf[g] * (K1 + 1) / (tf[g] + norm) for g in kept if tf[g])

    scored = sorted(
        ((bm25(tf, n), w) for w, tf, n in folded if tf), key=lambda x: (-x[0], x[1])
    )
    return {
        "state": "ranked" if scored else "escalated:cold_start",
        "chunks": n_all,
        "copies": sum(copies.values()) - n_all,
        "pairs": {g: round(pmi(g), 2) for g in kept if " " in g},
        "hits": [
            {
                "score": round(s, 3),
                "repo": r,
                "path": p,
                "line": ln,
                "copies": copies[h],
            }
            for s, (r, p, ln, h) in scored
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bite")
    root = Path(__file__).resolve().parents[3]
    ap.add_argument("--box", type=Path, default=root / ".flow" / "onescript")
    ap.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)
    ptr = next(
        (
            i
            for i in json.loads((a.box / "pile.json").read_text())["items"]
            if i["where"] == seed.INDEX
        ),
        None,
    )
    data = (a.box / seed.INDEX).read_bytes() if ptr else b""
    if ptr is None or hashlib.sha256(data).hexdigest() != ptr["sha"]:
        print(
            json.dumps({"state": "unreachable", "why": "no index the pile vouches for"})
        )
        return 1
    index = json.loads(data)
    repos = {n: root.parent / n for n in index.get("arrival", {})}
    for spec in a.repo:
        name, _, path = spec.partition("=")
        repos[name] = Path(path)
    out = rank(index, repos, a.bite)
    out["hits"] = out["hits"][: a.top]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
