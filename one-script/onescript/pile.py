#!/usr/bin/env python3
"""pile: a bite's words and short phrases, ranked against the pile. No model.

    python3 scripts/scan/phrase_rank.py "the bite" [--box BOX] [--repo NAME=PATH ...] [--top N]
        [--max CHARS] [--embed DIR]
    python3 scripts/scan/phrase_rank.py --sweep QUERIES.json [--max-values 128,250,500]

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

from . import seed

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
    n_all = len(paras)
    avg = sum(p[3] for p in paras) / n_all
    cands = [p for p in paras if p[1] or p[2]]
    extra = sum(copies.values()) - n_all
    return score(bite, cands, df_raw, n_all, avg, copies, extra, embed)


def score(bite, cands, df_raw, n_all, avg, copies, extra, embed=None) -> dict:
    """Rung 1 over the chunks that hold any of the bite's grams. A row is
    (where, body tf, question tf, length[, text]); `n_all` and `avg` are over
    every chunk in the pile. The direct read and the cache both end here."""
    words, _ = wanted(bite)
    fold = folder(df_raw)
    fmap = {w: fold(w) for w in words}

    def fg(x: str) -> str:
        return " ".join(fmap.get(t, t) for t in x.split(" "))

    folded = []
    for row in cands:
        tf = collections.Counter()
        for x, k in row[1].items():
            tf[fg(x)] += k
        for x, k in row[2].items():
            tf[fg(x)] += QUESTION * k  # BM25F: weighted before saturation
        folded.append((row[0], tf, row[3]) + row[4:])
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
        "copies": extra,
        "folds": {w: f for w, f in sorted(fmap.items()) if w != f and df_raw[w]},
        "pairs": {g: round(pair_score(g), 3) for g in kept if " " in g},
        "fused": False,
    }
    if embed is not None and scored and all(len(row) > 3 for _, row in scored):
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
            "chunk": w[3],
        }
        for w in order
    ]
    return out


# ── the cache: the pile hashed once, words as hashes, never text ──────────
# "A baseline holds hashes only, never content" (one-script README); "once the
# box is hashed, the hash is a lookup that returns a hash and a count"
# (next-pile.md). Every word is an 8-byte BLAKE2b with its chunk and position;
# every sentence of three or more words is one hash with a count. Rebuilt
# whole when the xref index, MAX, MIN or this version changes.
CACHE_VERSION = 1
SENT = re.compile(r"[.!?]+")
GAP = 1  # positions skipped at a clause or line break, so no pair spans one


def term(w: str) -> int:
    """A word (or a normalized sentence) as a signed 8-byte BLAKE2b."""
    d = hashlib.blake2b(w.encode(), digest_size=8).digest()
    return int.from_bytes(d, "big", signed=True)


def sentences(line: str) -> list[str]:
    out = []
    for part in SENT.split(line.lower()):
        ws = WORD.findall(part)
        if len(ws) >= 3:
            out.append(" ".join(ws))
    return out


def _meta(index_sha: str, limit: int) -> dict:
    return {
        "version": str(CACHE_VERSION),
        "index_sha256": index_sha,
        "max": str(limit),
        "min": str(MIN),
        "byteorder": sys.byteorder,
    }


def build_cache(index: dict, repos: dict, path: Path, index_sha: str, limit: int = MAX):
    """Read the pile once and write the hashed cache atomically."""
    import array
    import os
    import sqlite3
    import zlib

    post: dict = {}
    sent: dict = {}
    chunks_out, copies, ids = [], collections.Counter(), {}
    for name, arr in sorted(index.get("arrival", {}).items()):
        root = repos.get(name)
        if root is None or not Path(root).is_dir():
            continue
        files = [
            f
            for f in arr.get("files", [])
            if f.get("state") == "present" and f.get("blob_match")
        ]
        blobs = seed._blobs(Path(root), sorted({f["git_blob"] for f in files}))
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
                if h in ids:
                    continue
                cid = ids[h] = len(chunks_out)
                pos, n = 0, 0
                for ln in s.split("\n"):
                    q = 1 if is_question(ln) else 0
                    for clause in CLAUSE.split(ln.lower()):
                        ws = WORD.findall(clause)
                        n += len(ws) + max(len(ws) - 1, 0)
                        for w in ws:
                            slot = post.get(t := term(w))
                            if slot is None:
                                slot = post[t] = (array.array("I"), array.array("I"))
                            slot[0].append(cid)
                            slot[1].append(pos << 1 | q)
                            pos += 1
                        pos += GAP
                    pos += GAP
                    for sn in sentences(ln):
                        e = sent.get(t := term(sn))
                        if e is None:
                            sent[t] = [1, cid]
                        else:
                            e[0] += 1
                chunks_out.append((cid, h, n, name, f["path"], line))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);"
        "CREATE TABLE chunk(id INTEGER PRIMARY KEY, sha TEXT, n INTEGER,"
        " copies INTEGER, repo TEXT, path TEXT, line INTEGER);"
        "CREATE TABLE post(term INTEGER PRIMARY KEY, cids BLOB, pos BLOB);"
        "CREATE TABLE sent(h INTEGER PRIMARY KEY, count INTEGER, cid INTEGER);"
    )
    db.executemany("INSERT INTO meta VALUES (?, ?)", _meta(index_sha, limit).items())
    db.executemany(
        "INSERT INTO chunk VALUES (?, ?, ?, ?, ?, ?, ?)",
        ((c, h, n, copies[h], r, p, ln) for c, h, n, r, p, ln in chunks_out),
    )
    db.executemany(
        "INSERT INTO post VALUES (?, ?, ?)",
        (
            (t, zlib.compress(a.tobytes()), zlib.compress(b.tobytes()))
            for t, (a, b) in post.items()
        ),
    )
    db.executemany(
        "INSERT INTO sent VALUES (?, ?, ?)", ((t, c, i) for t, (c, i) in sent.items())
    )
    db.commit()
    db.close()
    os.replace(tmp, path)


def open_cache(index: dict, repos: dict, path: Path, index_sha: str, limit: int = MAX):
    """The cache for this index and limit, built (or rebuilt) when it isn't."""
    import sqlite3

    want = _meta(index_sha, limit)
    if path.exists():
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            have = dict(db.execute("SELECT k, v FROM meta"))
        except sqlite3.DatabaseError:
            have = {}
        if have == want:
            return db, "warm"
        db.close()
    build_cache(index, repos, path, index_sha, limit)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True), "built"


class Cached:
    """Rung 1's statistics from the cache, for `score`."""

    def __init__(self, db):
        import array
        import zlib

        self.db, self._array, self._zlib = db, array, zlib
        rows = db.execute("SELECT id, sha, n, copies, repo, path, line FROM chunk")
        self.where, self.n, self.copies = {}, {}, {}
        total = 0
        for cid, sha, n, cp, repo, path, line in rows:
            self.where[cid] = (repo, path, line, sha)
            self.n[cid] = n
            self.copies[sha] = cp
            total += cp
        self.n_all = len(self.n)
        self.avg = sum(self.n.values()) / self.n_all if self.n_all else 0.0
        self.extra = total - self.n_all
        self._memo: dict = {}

    def postings(self, w: str) -> list:
        """(cid, position, question) for every occurrence of `w`."""
        if w not in self._memo:
            row = self.db.execute(
                "SELECT cids, pos FROM post WHERE term = ?", (term(w),)
            ).fetchone()
            if row is None:
                self._memo[w] = []
            else:
                a, b = self._array.array("I"), self._array.array("I")
                a.frombytes(self._zlib.decompress(row[0]))
                b.frombytes(self._zlib.decompress(row[1]))
                self._memo[w] = [(c, p >> 1, p & 1) for c, p in zip(a, b)]
        return self._memo[w]

    def rank(self, bite: str, embed=None) -> dict:
        if not self.n_all:
            return {"state": "unreachable", "fused": False, "hits": []}
        words, pairs = wanted(bite)
        body: dict = collections.defaultdict(collections.Counter)
        quest: dict = collections.defaultdict(collections.Counter)
        df_raw = collections.Counter()
        for w in sorted(words):
            seen = set()
            for c, _, q in self.postings(w):
                (quest if q else body)[c][w] += 1
                seen.add(c)
            df_raw[w] = len(seen)
        for p in sorted(pairs):
            a, b = p.split(" ")
            nxt = {(c, pos) for c, pos, _ in self.postings(b)}
            seen = set()
            for c, pos, q in self.postings(a):
                if (c, pos + 1) in nxt:
                    (quest if q else body)[c][p] += 1
                    seen.add(c)
            if seen:
                df_raw[p] = len(seen)
        cands = [
            (
                self.where[c],
                body.get(c, collections.Counter()),
                quest.get(c, collections.Counter()),
                self.n[c],
            )
            for c in sorted(set(body) | set(quest))
        ]
        return score(
            bite, cands, df_raw, self.n_all, self.avg, self.copies, self.extra, embed
        )

    def quotes(self, bite: str) -> list:
        """The bite's sentences said before, by hash: a count and where first."""
        out = []
        for sn in dict.fromkeys(sentences(bite)):
            row = self.db.execute(
                "SELECT count, cid FROM sent WHERE h = ?", (term(sn),)
            ).fetchone()
            if row:
                r, p, ln, _ = self.where[row[1]]
                out.append(
                    {
                        "words": len(sn.split()),
                        "count": row[0],
                        "repo": r,
                        "path": p,
                        "line": ln,
                    }
                )
        return out


# ── the session's store, and a chunk read back at its blob ─────────────────
def store_path(box: Path, limit: int = MAX) -> Path:
    """The session's hashed pile, beside its box (never inside it: the box
    holds what the record points at, and this is rebuilt, not pointed at)."""
    return box.with_name(box.name + "-store") / f"pile-{limit}.sqlite"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_chunk(index: dict, repos: dict, chunk: dict, limit: int = MAX):
    """(text, None) for a chunk read at its indexed blob, or (None, why).
    Content stays where it lives: the text is re-read here, and only while the
    blob hashes to the index and the chunk hashes to its own SHA-256."""
    name, path = chunk.get("repo"), chunk.get("path")
    files = {
        f["path"]: f for f in index.get("arrival", {}).get(name, {}).get("files", [])
    }
    f = files.get(path)
    if f is None or f.get("state") != "present" or not f.get("blob_match"):
        return None, "not in the index"
    root = repos.get(name)
    if root is None or not Path(root).is_dir():
        return None, "no checkout to read it from"
    try:
        d = seed._blobs(Path(root), [f["git_blob"]]).get(f["git_blob"])
    except seed.GitError as e:
        return None, f"git: {str(e)[:80]}"
    if d is None or hashlib.sha256(d).hexdigest() != f["sha256"]:
        return None, "the blob doesn't hash to the index"
    try:
        text = d.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not utf-8"
    for line, s in chunks(text, limit):
        if line == chunk.get("line") and hashlib.sha256(
            s.encode()
        ).hexdigest() == chunk.get("sha256"):
            return s, None
    return None, "the chunk isn't at that line with that hash"


# ── across sessions: piles merged by hash, a view rebuilt on demand ─────────
def merge(stores: dict, bite: str) -> dict:
    """Each session's store ranks the bite on its own; the hits merge by chunk
    hash (one chunk, with every session that holds it and every place it sits)
    and the ranks fuse by RRF. Nothing is written and no store reads another:
    "cross-session lookups are piles merged by hash", never a shared soil.
    `stores` maps a session's name to its `Cached`."""
    states, orders, by_chunk, quotes = {}, [], {}, []
    for name in sorted(stores):
        c = stores[name]
        r = c.rank(bite)
        states[name] = r["state"]
        order = []
        for h in r["hits"]:
            e = by_chunk.setdefault(
                h["chunk"], {"chunk": h["chunk"], "sessions": [], "where": []}
            )
            e["sessions"].append(name)  # a chunk is once per store: no repeat
            loc = [h["repo"], h["path"], h["line"]]
            if loc not in e["where"]:
                e["where"].append(loc)
            order.append(h["chunk"])
        orders.append(order)
        quotes += [{"session": name, **q} for q in c.quotes(bite)]
    hits = [by_chunk[k] for k in rrf(*orders)]
    for h in hits:
        h["seen_in"] = len(h["sessions"])
    return {"sessions": states, "hits": hits, "quotes": quotes}


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


def sweep(index: dict, repos: dict, queries: list, limits: list, cache=None) -> dict:
    """Mean reciprocal rank of each query's target, at each chunk size.
    `cache` is (dir, index_sha): each size reads its own hashed cache."""
    out = {}
    want = set()
    for q in queries:
        want |= set().union(*wanted(q["bite"]))
    for limit in limits:
        if cache:
            db, _ = open_cache(
                index, repos, cache[0] / f"pile-{limit}.sqlite", cache[1], limit
            )
            c = Cached(db)
            ranker = c.rank
        else:
            pile = read_pile(index, repos, want, limit)

            def ranker(b, pile=pile, limit=limit):
                return rank(index, repos, b, limit, pile=pile)

        rr = []
        for q in queries:
            hits = ranker(q["bite"])["hits"]
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


def load_index(box: Path, with_sha: bool = False):
    """The box's xref index, only while it hashes to its pile pointer."""
    items = json.loads((box / "pile.json").read_text())["items"]
    ptr = next((i for i in items if i["where"] == seed.INDEX), None)
    data = (box / seed.INDEX).read_bytes() if ptr else b""
    if ptr is None or hashlib.sha256(data).hexdigest() != ptr["sha"]:
        return (None, None) if with_sha else None
    return (json.loads(data), ptr["sha"]) if with_sha else json.loads(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bite", nargs="?")
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--box", type=Path, default=root / ".flow" / "onescript")
    ap.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--max", type=int, default=MAX, help="chunk characters")
    ap.add_argument(
        "--embed", type=Path, help="WordLlama's local file dir: fuse by RRF"
    )
    ap.add_argument("--sweep", type=Path, help="queries JSON: MRR at each --max-values")
    ap.add_argument("--max-values", default="128,250,500")
    ap.add_argument(
        "--cache",
        type=Path,
        help="dir for the hashed pile cache (built once per index and --max)",
    )
    a = ap.parse_args(argv)
    if a.cache and a.embed:
        ap.error("--embed reads chunk text, and the cache holds hashes only")
    index, index_sha = load_index(a.box, with_sha=True)
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
        cache = (a.cache, index_sha) if a.cache else None
        print(json.dumps(sweep(index, repos, queries, limits, cache), indent=1))
        return 0
    if not a.bite:
        ap.error("a bite, or --sweep")
    if a.cache:
        db, state = open_cache(
            index, repos, a.cache / f"pile-{a.max}.sqlite", index_sha, a.max
        )
        c = Cached(db)
        out = c.rank(a.bite)
        out["cache"] = state
        out["quotes"] = c.quotes(a.bite)
    else:
        embed = wordllama_embed(a.embed) if a.embed else None
        out = rank(index, repos, a.bite, a.max, embed)
    out["hits"] = out["hits"][: a.top]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
