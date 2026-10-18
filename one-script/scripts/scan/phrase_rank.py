#!/usr/bin/env python3
"""phrase_rank: a bite's words and short phrases, ranked against the pile. No model.

    python3 phrase_rank.py "the bite" [--box BOX] [--repo NAME=PATH ...] [--top N]
        [--max CHARS] [--embed DIR]
    python3 phrase_rank.py --sweep QUERIES.json [--max-values 128,250,500]

Rung 1 of the escalation ladder, past single words. The pile is the box's
xref index (only while it hashes to its pile pointer); each file is read at
its indexed blob and counted only when its bytes hash to the index.

| Step | Rule | Prior art |
|---|---|---|
| chunks | blank-line paragraphs; one over MAX is split at line breaks, never dropped | passage retrieval; chunk size measured by `--sweep` |
| dedupe | identical chunks count once (SHA-256), with their copy count | exact-duplicate removal |
| fold | -ies -> -y, -s, -es, each only when the stem is attested: df(stem) * ATTEST >= df(w) | a corpus-gated light stemmer (S-stemmer family) |
| pairs | adjacent words within a clause, kept when df >= PAIR_MIN and NPMI > 0 | Bouma 2009 (NPMI) |
| fields | a line ending in `?` is a question field, weighted QUESTION before saturation | BM25F, Robertson et al. 2004 |
| rank | BM25 over the kept words and pairs, at its standard constants | Robertson & Zaragoza |
| fuse | with `--embed`, a static embedder ranks the same candidates and the two ranks fuse by 1/(RRF_K + rank) | RRF, Cormack et al. 2009 |

ATTEST, PAIR_MIN, QUESTION and MAX are placeholders for the operator's numbers.
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
RRF_K = 60  # Cormack, Clarke & Büttcher 2009
ATTEST = 10  # placeholder
PAIR_MIN = 2  # placeholder
QUESTION = 2.0  # placeholder
MAX, MIN = 500, 40  # chunk characters; MAX is a placeholder
EMBED_CHARS = 2000  # the most of one chunk the embedder reads
SUFFIXES = (("ies", "y"), ("s", ""), ("es", ""))  # tried in this order


def raw_grams(text: str) -> list[str]:
    out = []
    for clause in CLAUSE.split(text.lower()):
        w = WORD.findall(clause)
        out += w + [f"{a} {b}" for a, b in zip(w, w[1:])]
    return out


def is_question(line: str) -> bool:
    return line.strip().rstrip(" \",'`").endswith("?")


def chunks(text: str, limit: int = MAX):
    """(line, chunk) pairs; a block over `limit` is split at line breaks."""
    line = 1
    for block in re.split(r"(\n\s*\n)", text):
        s = block.strip()
        if s and len(s) <= limit:
            yield line, s
        elif s:
            buf, start, n = [], line, line
            for ln in block.split("\n"):
                if buf and sum(map(len, buf)) + len(ln) > limit:
                    yield start, "\n".join(buf).strip()
                    buf, start = [], n
                buf.append(ln)
                n += 1
            if buf:
                yield start, "\n".join(buf).strip()
        line += block.count("\n")


def stems(w: str) -> set[str]:
    return {
        w[: -len(s)] + r for s, r in SUFFIXES if w.endswith(s) and len(w) > len(s) + 1
    }


def forms(w: str) -> set[str]:
    bases = {w} | stems(w)
    out = set()
    for b in bases:
        out |= {b, b + "s", b + "es"} | ({b[:-1] + "ies"} if b.endswith("y") else set())
    return out


def folder(df: collections.Counter, attest: int = ATTEST):
    """The fold, gated by the pile's own counts."""

    def fold(w: str) -> str:
        for suf, rep in SUFFIXES:
            if w.endswith(suf) and len(w) > len(suf) + 1:
                if suf == "s" and w.endswith("ss"):
                    continue
                stem = w[: -len(suf)] + rep
                if df[stem] and df[stem] * attest >= df[w]:
                    return stem
        return w

    return fold


def npmi(df_ab: int, df_a: int, df_b: int, n: int) -> float:
    """Normalized PMI over chunk counts, in [-1, 1]; 0 is independence."""
    if not (df_ab and df_a and df_b) or df_ab >= n:
        return -1.0
    p_ab = df_ab / n
    return math.log(p_ab / ((df_a / n) * (df_b / n))) / -math.log(p_ab)


def rrf(*rankings: list) -> list:
    """Reciprocal rank fusion: each list is keys best-first; ties by key."""
    score: dict = collections.defaultdict(float)
    for ranking in rankings:
        for r, key in enumerate(ranking, 1):
            score[key] += 1.0 / (RRF_K + r)
    return sorted(score, key=lambda k: (-score[k], k))


def read_pile(index: dict, repos: dict, want: set, limit: int = MAX, keep_text=False):
    """Every unique chunk: (where, body tf, question tf, length[, text])."""
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
            for line, s in chunks(text, limit):
                if len(s) < MIN:
                    continue
                h = hashlib.sha256(s.encode()).hexdigest()
                copies[h] += 1
                if copies[h] > 1:
                    continue
                body, quest, n = collections.Counter(), collections.Counter(), 0
                for ln in s.split("\n"):
                    g = raw_grams(ln)
                    n += len(g)
                    into = quest if is_question(ln) else body
                    into.update(x for x in g if x in want)
                for x in set(body) | set(quest):
                    df_raw[x] += 1
                row = ((name, f["path"], line, h), body, quest, n)
                paras.append(row + ((s,) if keep_text else ()))
    return paras, df_raw, copies


def wanted(bite: str) -> tuple[set, set]:
    """The bite's words and pairs in every form the fold may meet."""
    words = set().union(*(forms(w) for w in WORD.findall(bite.lower())))
    pairs = set()
    for g in raw_grams(bite):
        if " " in g:
            a, b = g.split(" ")
            pairs |= {f"{x} {y}" for x in forms(a) for y in forms(b)}
    return words, pairs


def rank(
    index: dict, repos: dict, bite: str, limit: int = MAX, embed=None, pile=None
) -> dict:
    """`pile` is a `read_pile` result to reuse (it may want more than this bite)."""
    words, pairs = wanted(bite)
    if pile is None:
        pile = read_pile(index, repos, words | pairs, limit, embed is not None)
    paras, df_raw, copies = pile
    if not paras:
        return {"state": "unreachable", "fused": False, "hits": []}

    fold = folder(df_raw)
    fmap = {w: fold(w) for w in words}

    def fg(x: str) -> str:
        return " ".join(fmap.get(t, t) for t in x.split(" "))

    folded = []
    for row in paras:
        tf = collections.Counter()
        for x, k in row[1].items():
            tf[fg(x)] += k
        for x, k in row[2].items():
            tf[fg(x)] += QUESTION * k  # BM25F: weighted before saturation
        folded.append((row[0], tf, row[3]) + row[4:])
    n_all = len(folded)
    avg = sum(p[2] for p in folded) / n_all
    df = collections.Counter(x for _, tf, *_ in folded for x in tf)
    bite_grams = sorted({fg(g) for g in raw_grams(bite)})

    def pair_score(p: str) -> float:
        a, b = p.split(" ")
        return npmi(df[p], df[a], df[b], n_all)

    kept = [g for g in bite_grams if " " not in g and df[g]]
    kept += [
        g for g in bite_grams if " " in g and df[g] >= PAIR_MIN and pair_score(g) > 0
    ]
    idf = {g: math.log(1 + (n_all - df[g] + 0.5) / (df[g] + 0.5)) for g in kept}

    def bm25(tf, n) -> float:
        norm = K1 * (1 - B + B * n / avg)
        return sum(idf[g] * tf[g] * (K1 + 1) / (tf[g] + norm) for g in kept if tf[g])

    scored = sorted(
        ((sc, row) for row in folded if (sc := bm25(row[1], row[2])) > 0),
        key=lambda x: (-x[0], x[1][0]),
    )
    order = [row[0] for _, row in scored]
    by_where = {row[0]: s for s, row in scored}
    out = {
        "state": "ranked" if scored else "escalated:cold_start",
        "chunks": n_all,
        "copies": sum(copies.values()) - n_all,
        "folds": {w: f for w, f in sorted(fmap.items()) if w != f and df_raw[w]},
        "pairs": {g: round(pair_score(g), 3) for g in kept if " " in g},
        "fused": False,
    }
    if embed is not None and scored:
        emb_order = embed(bite, [(row[0], row[3]) for _, row in scored])
        if emb_order is not None:
            order = rrf(order, emb_order)
            out["fused"] = True
    out["hits"] = [
        {
            "score": round(by_where[w], 3),
            "repo": w[0],
            "path": w[1],
            "line": w[2],
            "copies": copies[w[3]],
        }
        for w in order
    ]
    return out


def wordllama_embed(cache: Path):
    """Rung 3, from local files only: WordLlama's bundled table, no download.
    Returns None (and the rank stays rung 1 alone) when it can't load."""
    try:
        from wordllama import WordLlama  # noqa: PLC0415

        wl = WordLlama.load(cache_dir=cache, disable_download=True)
    except Exception as e:  # noqa: BLE001 — absent is a state, said
        print(f"embed unreachable: {type(e).__name__}", file=sys.stderr)
        return None

    vecs: dict = {}  # chunk SHA-256 -> vector, for the life of this run

    def order(bite: str, cands: list) -> list:
        q = wl.embed([bite], norm=True)[0]
        new = [(w[3], t) for w, t in cands if w[3] not in vecs]
        if new:
            # a line over MAX is kept whole for the word rank; the embedder
            # pads a batch to its longest text, so it reads only the head
            v = wl.embed([t[:EMBED_CHARS] for _, t in new], norm=True, batch_size=256)
            vecs.update(zip((h for h, _ in new), v))
        s = [float(vecs[w[3]] @ q) for w, _ in cands]
        return [
            cands[i][0]
            for i in sorted(range(len(cands)), key=lambda i: (-s[i], cands[i][0]))
        ]

    return order


def sweep(index: dict, repos: dict, queries: list, limits: list) -> dict:
    """Mean reciprocal rank of each query's target, at each chunk size."""
    out = {}
    want = set()
    for q in queries:
        want |= set().union(*wanted(q["bite"]))
    for limit in limits:
        pile = read_pile(index, repos, want, limit)
        rr = []
        for q in queries:
            hits = rank(index, repos, q["bite"], limit, pile=pile)["hits"]
            ranks = [
                i
                for i, h in enumerate(hits, 1)
                if f"{h['repo']}:{h['path']}" in q["targets"]
            ]
            rr.append(1 / ranks[0] if ranks else 0.0)
        out[limit] = {
            "mrr": round(sum(rr) / len(rr), 4),
            "rr": [round(x, 4) for x in rr],
        }
    return out


def load_index(box: Path) -> dict | None:
    items = json.loads((box / "pile.json").read_text())["items"]
    ptr = next((i for i in items if i["where"] == seed.INDEX), None)
    data = (box / seed.INDEX).read_bytes() if ptr else b""
    if ptr is None or hashlib.sha256(data).hexdigest() != ptr["sha"]:
        return None
    return json.loads(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bite", nargs="?")
    root = Path(__file__).resolve().parents[3]
    ap.add_argument("--box", type=Path, default=root / ".flow" / "onescript")
    ap.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--max", type=int, default=MAX, help="chunk characters")
    ap.add_argument(
        "--embed", type=Path, help="WordLlama's local file dir: fuse by RRF"
    )
    ap.add_argument("--sweep", type=Path, help="queries JSON: MRR at each --max-values")
    ap.add_argument("--max-values", default="128,250,500")
    a = ap.parse_args(argv)
    index = load_index(a.box)
    if index is None:
        print(
            json.dumps({"state": "unreachable", "why": "no index the pile vouches for"})
        )
        return 1
    repos = {n: root.parent / n for n in index.get("arrival", {})}
    for spec in a.repo:
        name, _, path = spec.partition("=")
        repos[name] = Path(path)
    if a.sweep:
        queries = json.loads(a.sweep.read_text())["queries"]
        limits = [int(x) for x in a.max_values.split(",")]
        print(json.dumps(sweep(index, repos, queries, limits), indent=1))
        return 0
    if not a.bite:
        ap.error("a bite, or --sweep")
    embed = wordllama_embed(a.embed) if a.embed else None
    out = rank(index, repos, a.bite, a.max, embed)
    out["hits"] = out["hits"][: a.top]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
