#!/usr/bin/env python3
"""pile: a bite's words and short phrases, ranked against the pile. No model.

    python3 scripts/scan/phrase_rank.py "the bite" [--box BOX] [--repo NAME=PATH ...] [--top N]
        [--max CHARS] [--embed DIR]
    python3 scripts/scan/phrase_rank.py --sweep QUERIES.json [--max-values 128,250,500]
    python3 scripts/scan/phrase_rank.py "the bite" --cache DIR [--key FILE]

Rung 1 of the escalation ladder, past single words. The pile is the box's
xref index (only while it hashes to its pile pointer); each file is read at
its indexed blob and counted only when its bytes hash to the index.

| Step | Rule | Prior art |
|---|---|---|
| chunks | blank-line paragraphs; one over MAX is split at line breaks, and a single line over MAX into pieces of at most MAX (at whitespace where it can); a piece under MIN rides on the next chunk as its title, or on the last one when nothing follows. Nothing is dropped | passage retrieval; chunk size measured by `--sweep` |
| dedupe | identical chunks count once (SHA-256), with their copy count | exact-duplicate removal |
| fold | -ies -> -y, -s, -es, each only when the stem is attested: df(stem) * ATTEST >= df(w) | a corpus-gated light stemmer (S-stemmer family) |
| pairs | adjacent words within a clause, kept when df >= PAIR_MIN and NPMI > 0 | Bouma 2009 (NPMI) |
| fields | a title line (a piece under MIN) is a title field, weighted TITLE; any other line ending in `?` (past trailing quote or emphasis marks) is a question field, weighted QUESTION; both before saturation | BM25F, Robertson et al. 2004 |
| rank | BM25 over the kept words and pairs, at its standard constants | Robertson & Zaragoza |
| fuse | with `--embed`, a static embedder ranks the same candidates and the two ranks fuse by 1/(RRF_K + rank) | RRF, Cormack et al. 2009 |

Every number here is a baseline the operator set on 2026-10-18 (D12: "as suggested
but marked as baseline"): kept as measured or suggested, the operator's to move.
Writes nothing but stdout, except with `--cache`, which writes the keyed store
(`build_cache`) into that dir. Same box, same repos, same bite: same output.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import sys
import urllib.parse
import zlib
from pathlib import Path

from . import seed
from .states import (
    EMPTY,
    POPULATED,
    RANKED,
    STEP_ESCALATED_COLD_START,
    UNREACHABLE,
)
from .words import WORD as _WORD

WORD = _WORD  # one word, for seed and the pile alike (flatten-2 F11)
CLAUSE = re.compile(r"[,.;:!?()\[\]{}|\"]+")
K1, B = 1.2, 0.75  # BM25's standard constants
RRF_K = 60  # Cormack, Clarke & Büttcher 2009
ATTEST = 10  # baseline (D12)
PAIR_MIN = 2  # baseline (D12)
QUESTION = 2.0  # baseline (D12)
TITLE = 2.0  # baseline (D12)
REPAIR_MIN = 4  # baseline (D12): the shortest word rung 2 repairs
#: Off. Keep a pair only if its rarer word sits in at most this share of chunks.
#: Measured 2026-10-18 on 25 bites: 0.003 gave MRR 0.5439 against 0.5235 off,
#: fixing one miss (50 -> 2) and costing two (1 -> 2, 13 -> 36); 0.001 was worse,
#: 0.01 and up changed nothing. Off is the baseline (D12).
PAIR_BASIN = None
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
MAX, MIN = 500, 40  # chunk characters; baseline (D12) (MAX measured best of 4)
EMBED_CHARS = 2000  # the most of one chunk the embedder reads
SUFFIXES = (("ies", "y"), ("s", ""), ("es", ""))  # tried in this order


def raw_grams(text: str) -> list[str]:
    out = []
    for clause in CLAUSE.split(text.lower()):
        w = WORD.findall(clause)
        out += w + [f"{a} {b}" for a, b in zip(w, w[1:])]
    return out


def is_question(line: str) -> bool:
    """A line ending in `?`, past trailing quotes and markdown emphasis
    (`> **What's the first bite?**` is a question)."""
    return line.strip().rstrip(" \"'`*_~>").endswith("?")


def field(i: int, line: str, titles) -> int:
    """A chunk line's BM25F field: 0 body, 1 question, 2 title (title first)."""
    return 2 if i in titles else 1 if is_question(line) else 0


def hard_split(s: str, limit: int) -> list[str]:
    """One line over `limit` as pieces of at most `limit` characters, cut at
    whitespace where it can be and hard where it can't. The cuts are spread
    evenly, so no piece is a sliver."""
    out = []
    while len(s) > limit:
        width = -(-len(s) // -(-len(s) // limit))  # ceil(len / ceil(len / limit))
        cut = max(s.rfind(" ", 0, width + 1), s.rfind("\t", 0, width + 1))
        if cut <= width // 2:
            cut = width
        out.append(s[:cut].rstrip())
        s = s[cut:].lstrip()
    return out + ([s] if s else [])


def pieces(text: str, limit: int = MAX):
    """(line, piece) pairs: a block over `limit` is split at line breaks, and
    one line over it by `hard_split`. A piece is at most `limit` characters
    not counting the line breaks inside it (the split sums the lines only)."""
    line = 1
    for block in re.split(r"(\n\s*\n)", text):
        s = block.strip()
        if s and len(s) <= limit:
            yield line + block[: len(block) - len(block.lstrip())].count("\n"), s
        elif s:
            buf, start, n = [], line, line
            for ln in block.split("\n"):
                if not buf and not ln.strip():
                    n += 1
                    start = n
                    continue
                if len(ln.strip()) > limit:
                    if buf:
                        yield start, "\n".join(buf).strip()
                    for part in hard_split(ln.strip(), limit):
                        yield n, part
                    buf, start = [], n + 1
                    n += 1
                    continue
                if buf and sum(map(len, buf)) + len(ln) > limit:
                    yield start, "\n".join(buf).strip()
                    buf, start = [], n
                buf.append(ln)
                n += 1
            if buf:
                yield start, "\n".join(buf).strip()
        line += block.count("\n")


def chunks(text: str, limit: int = MAX):
    """(line, chunk, titles): `titles` is the set of the chunk's line indexes
    that are title lines. A piece under MIN is never dropped: it rides on the
    next chunk as a title (and the chunk's line is then the title's), or, with
    nothing after it, on the last chunk. A run of short pieces longer than
    `limit` stands as its own chunk, all title. A chunk's body is at most
    `limit`; the title lines it carries sit on top of that."""
    prev, held = None, []
    for line, s in pieces(text, limit):
        if len(s) < MIN:
            if held and sum(len(x) + 1 for _, x in held) + len(s) > limit:
                if prev:
                    yield prev
                prev, held = _chunk(held, len(held)), []
            held.append((line, s))
            continue
        cur = _chunk(held + [(line, s)], len(held))
        held = []
        if prev:
            yield prev
        prev = cur
    if held:
        if prev:
            n = prev[1].count("\n") + 1
            tail = _chunk(held, len(held))
            prev = (
                prev[0],
                prev[1] + "\n\n" + tail[1],
                frozenset(prev[2] | {n + 1 + i for i in tail[2]}),
            )
        else:
            prev = _chunk(held, len(held))
    if prev:
        yield prev


def _chunk(parts: list, n_title: int) -> tuple:
    """(line, text, titles) from pieces, the first `n_title` of them titles.
    Pieces join at a blank line, as they stood, so a title is a piece under
    MIN in the chunk's own text, and one text always has the same fields."""
    lines, titles = [], set()
    for i, (_, s) in enumerate(parts):
        if lines:
            lines.append("")
        for x in s.split("\n"):
            if i < n_title:
                titles.add(len(lines))
            lines.append(x)
    return parts[0][0], "\n".join(lines), frozenset(titles)


ES_AFTER = ("s", "x", "z", "ch", "sh", "o")  # English takes -es only after these


def _takes(stem: str, suf: str) -> bool:
    return suf != "es" or stem.endswith(ES_AFTER)


def stems(w: str) -> set[str]:
    return {
        w[: -len(s)] + r
        for s, r in SUFFIXES
        if w.endswith(s) and len(w) > len(s) + 1 and _takes(w[: -len(s)], s)
    }


def edits1(w: str) -> list[str]:
    """Every spelling one edit from `w` (a deletion, a swap of neighbours, a
    replacement or an insertion, over a-z), sorted, `w` itself left out."""
    s = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    out = {a + b[1:] for a, b in s if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in s if len(b) > 1}
    out |= {a + x + b[1:] for a, b in s if b for x in ALPHABET}
    out |= {a + x + b for a, b in s for x in ALPHABET}
    out.discard(w)
    return sorted(out)


def forms(w: str) -> set[str]:
    bases = {w} | stems(w)
    out = set()
    for b in bases:
        out |= {b, b + "s"} | ({b + "es"} if _takes(b, "es") else set())
        out |= {b[:-1] + "ies"} if b.endswith("y") else set()
    return out


def folder(df: collections.Counter, attest: int = ATTEST):
    """The fold, gated by the pile's own counts."""

    def fold(w: str) -> str:
        for suf, rep in SUFFIXES:
            if w.endswith(suf) and len(w) > len(suf) + 1:
                if suf == "s" and w.endswith("ss"):
                    continue
                if not _takes(w[: -len(suf)], suf):  # "ones" is not "on" + es
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
    """Every unique chunk: (where, body, question, title tf, length[, text]), with
    the `seed.Texts` read that reached them (its repos, skips, unreachable)."""
    paras, df_raw, copies = [], collections.Counter(), collections.Counter()
    texts = seed.Texts(index, repos)
    for name, f, text in texts:
        for line, s, titles in chunks(text, limit):
            h = hashlib.sha256(s.encode()).hexdigest()
            copies[h] += 1
            if copies[h] > 1:
                continue
            fields = tuple(collections.Counter() for _ in range(3))
            n = 0
            for i, ln in enumerate(s.split("\n")):
                g = raw_grams(ln)
                n += len(g)
                fields[field(i, ln, titles)].update(x for x in g if x in want)
            for x in set().union(*fields):
                df_raw[x] += 1
            row = ((name, f["path"], line, h), *fields, n)
            paras.append(row + ((s,) if keep_text else ()))
    return paras, df_raw, copies, texts.tally()


def nothing(tally: dict) -> dict:
    """No chunk in the pile: `empty` when it was read and holds none,
    `unreachable` when no repo could be read, or every file read was refused
    (INVARIANTS section 1: the two are never collapsed)."""
    if not tally.get("repos"):
        state, why = UNREACHABLE, "no indexed repo could be read"
    elif not tally.get("files_read") and tally.get("skipped"):
        state, why = UNREACHABLE, "no indexed file could be read as text"
    else:
        state, why = EMPTY, "the pile was read and holds no chunk"
    return {"state": state, "why": why, "fused": False, "hits": []}


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
    paras, df_raw, copies, tally = pile
    if not paras:
        return nothing(tally)
    n_all = len(paras)
    avg = sum(p[4] for p in paras) / n_all
    cands = [p for p in paras if p[1] or p[2] or p[3]]
    extra = sum(copies.values()) - n_all
    return score(bite, words, cands, df_raw, n_all, avg, copies, extra, embed)


def score(bite, words, cands, df_raw, n_all, avg, copies, extra, embed=None) -> dict:
    """Rung 1 over the chunks that hold any of the bite's grams. A row is
    (where, body tf, question tf, title tf, length[, text]); `n_all` and `avg` are over
    every chunk in the pile; `words` is the caller's `wanted(bite)[0]`. Turned
    word-major and scored by `score_grams`, where the cache ends too."""
    grams: dict = collections.defaultdict(dict)
    n_of, texts = {}, {}
    for row in cands:
        w = row[0]
        n_of[w] = row[4]
        if len(row) > 5:
            texts[w] = row[5]
        for x in set(row[1]) | set(row[2]) | set(row[3]):
            grams[x][w] = row[1][x] + QUESTION * row[2][x] + TITLE * row[3][x]
    return score_grams(
        bite,
        words,
        grams,
        n_of,
        df_raw,
        n_all,
        avg,
        copies,
        extra,
        texts if len(texts) == len(n_of) else None,
        embed,
    )


def score_grams(
    bite, words, grams, n_of, df_raw, n_all, avg, copies, extra, texts=None, embed=None
) -> dict:
    """BM25F, word by word: `grams` maps each raw gram to {where: its weighted
    tf (body + QUESTION x question + TITLE x title)}; `n_of` is each chunk's
    length. Each kept gram's postings are walked once, so the cost follows the
    postings, not the chunks a common word touches. A chunk's terms are summed
    in kept order, as `sum` over them, so the scores don't depend on the walk."""
    fold = folder(df_raw)
    fmap = {w: fold(w) for w in words}

    def fg(x: str) -> str:
        return " ".join(fmap.get(t, t) for t in x.split(" "))

    tf: dict = collections.defaultdict(dict)
    for x in sorted(grams):
        d = tf[fg(x)]
        for w, k in grams[x].items():
            if k:
                d[w] = d.get(w, 0) + k
    df = collections.Counter({g: len(d) for g, d in tf.items()})
    bite_grams = sorted({fg(g) for g in raw_grams(bite)})

    def pair_score(p: str) -> float:
        a, b = p.split(" ")
        return npmi(df[p], df[a], df[b], n_all)

    kept = [g for g in bite_grams if " " not in g and df[g]]
    kept += [
        g
        for g in bite_grams
        if " " in g
        and df[g] >= PAIR_MIN
        and pair_score(g) > 0
        and (
            PAIR_BASIN is None or min(df[t] for t in g.split(" ")) <= PAIR_BASIN * n_all
        )
    ]
    idf = {g: math.log(1 + (n_all - df[g] + 0.5) / (df[g] + 0.5)) for g in kept}
    terms: dict = {}
    norms: dict = {}
    for g in kept:
        for w, t in tf[g].items():
            norm = norms.get(w)
            if norm is None:
                norm = norms[w] = K1 * (1 - B + B * n_of[w] / avg)
            terms.setdefault(w, []).append(idf[g] * t * (K1 + 1) / (t + norm))
    scored = sorted(
        ((sc, w) for w, ts in terms.items() if (sc := sum(ts)) > 0),
        key=lambda x: (-x[0], x[1]),
    )
    order = [w for _, w in scored]
    by_where = {w: sc for sc, w in scored}
    out = {
        "state": RANKED if scored else STEP_ESCALATED_COLD_START,
        "why": "",
        "chunks": n_all,
        "copies": extra,
        "folds": {w: f for w, f in sorted(fmap.items()) if w != f and df_raw[w]},
        "pairs": {g: round(pair_score(g), 3) for g in kept if " " in g},
        "fused": False,
    }
    if embed is not None and scored and texts is not None:
        emb_order = embed(bite, [(w, texts[w]) for w in order])
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


# ── the cache: the pile hashed once, words as keyed hashes ─────────────────
# "A baseline holds hashes only, never content" (one-script README); "once the
# box is hashed, the hash is a lookup that returns a hash and a count"
# (next-pile.md). Every word is an 8-byte BLAKE2b keyed with the store's key,
# with its chunk and position; every sentence of three or more words is one
# keyed hash with a count. Unkeyed, a word's hash is a dictionary lookup away
# from the word, so the key is what makes a hash not text: it lives in the
# operator's keys dir, never in the box or beside the store, and the store
# records only its fingerprint. What the store does hold in the clear: each
# chunk's repo, path and line, and its plain SHA-256 (the id serve re-reads it
# by and sessions merge on), which confirms a guess of a whole chunk's exact
# text but names no word. Rebuilt whole when the xref index, the key, MAX, MIN,
# the repos read or this version changes.
CACHE_VERSION = 3  # 3: a posting carries its field (body, question, title)
SENT = re.compile(r"[.!?]+")
GAP = 1  # positions skipped at a clause or line break, so no pair spans one
KEY_BYTES = 32
FINGERPRINT = b"onescript store key fingerprint"  # a fixed label, keyed
MAX_STORE = 1 << 30  # baseline (D12): the most a store file may weigh to be opened
MAX_POSTING = 1 << 26  # baseline (D12): the most one posting may decompress to
PATH_CAP = 512  # the most of a repo or path name a store hands back
CORE = ("version", "index_sha256", "max", "min", "byteorder", "key")
#: What a broken store raises when read; every reader turns it into a state.
BROKEN = (sqlite3.Error, zlib.error, ValueError, TypeError, KeyError, OverflowError)


def term(w: str, key: bytes) -> int:
    """A word (or a normalized sentence) as a signed 8-byte BLAKE2b, keyed."""
    d = hashlib.blake2b(w.encode(), digest_size=8, key=key).digest()
    return int.from_bytes(d, "big", signed=True)


def fingerprint(key: bytes) -> str:
    """What the store records of its key: a keyed hash of a fixed label. It
    names the key without holding it."""
    return hashlib.blake2b(FINGERPRINT, digest_size=16, key=key).hexdigest()


def key_name(box: Path) -> str:
    """The store key's file name for one box, in the operator's keys dir."""
    return f"store-{hashlib.sha256(str(Path(box).resolve()).encode()).hexdigest()[:16]}.key"


def make_key(path: Path) -> None:
    """32 fresh bytes, 0600, made once: an existing key is never replaced
    (`O_EXCL`), so a store stays readable by the key it was built with."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secrets.token_bytes(KEY_BYTES).hex())
    path.chmod(0o600)


def read_key(path: Path) -> tuple[bytes | None, str | None]:
    """(key, None), or (None, why): a key that is missing, not a plain file,
    readable beyond its owner, or not 32 bytes is no key."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return None, "the store key isn't a plain file"
        if st.st_mode & 0o077:
            return None, "the store key is readable beyond its owner; chmod 600 it"
        key = bytes.fromhex(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return None, "no store key in the operator's keys dir"
    except (OSError, ValueError) as e:
        return None, f"the store key can't be read: {type(e).__name__}"
    if len(key) != KEY_BYTES:
        return None, "the store key isn't 32 bytes"
    return key, None


def sentences(line: str) -> list[str]:
    out = []
    for part in SENT.split(line.lower()):
        ws = WORD.findall(part)
        if len(ws) >= 3:
            out.append(" ".join(ws))
    return out


def _meta(index_sha: str, limit: int, key: bytes) -> dict:
    """The store's identity: a store whose meta differs is another store."""
    return {
        "version": str(CACHE_VERSION),
        "index_sha256": index_sha,
        "max": str(limit),
        "min": str(MIN),
        "byteorder": sys.byteorder,
        "key": fingerprint(key),
    }


def reachable(index: dict, repos: dict) -> list[str]:
    """The indexed repos with a checkout to read now: a store built while one
    was missing is rebuilt when it returns, never `warm`."""
    return sorted(
        n
        for n in index.get("arrival", {})
        if repos.get(n) is not None and Path(repos[n]).is_dir()
    )


def build_cache(
    index: dict, repos: dict, path: Path, index_sha: str, key: bytes, limit: int = MAX
) -> dict:
    """Read the pile once and write the keyed store atomically. Its meta holds
    the identity (`_meta`) and what the read reached (`seed.Texts.tally`):
    the repos read, the files, the skips by reason, the unreachable repos."""
    import array

    post: dict = {}
    sent: dict = {}
    chunks_out, copies, ids = [], collections.Counter(), {}
    texts = seed.Texts(index, repos)
    for name, f, text in texts:
        for line, s, titles in chunks(text, limit):
            h = hashlib.sha256(s.encode()).hexdigest()
            copies[h] += 1
            if h in ids:
                continue
            cid = ids[h] = len(chunks_out)
            pos, n = 0, 0
            for i, ln in enumerate(s.split("\n")):
                q = field(i, ln, titles)
                for clause in CLAUSE.split(ln.lower()):
                    ws = WORD.findall(clause)
                    n += len(ws) + max(len(ws) - 1, 0)
                    for w in ws:
                        slot = post.get(t := term(w, key))
                        if slot is None:
                            slot = post[t] = (array.array("I"), array.array("I"))
                        slot[0].append(cid)
                        slot[1].append(pos << 2 | q)
                        pos += 1
                    pos += GAP
                pos += GAP
                for sn in sentences(ln):
                    e = sent.get(t := term(sn, key))
                    if e is None:
                        sent[t] = [1, cid]
                    else:
                        e[0] += 1
            chunks_out.append((cid, h, n, name, f["path"], line))
    tally = texts.tally()
    meta = {
        **_meta(index_sha, limit, key),
        **{k: json.dumps(v) for k, v in tally.items()},
    }
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
    db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
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
    return tally


def connect_ro(path: Path) -> sqlite3.Connection:
    """A store opened read-only and immutable, queries only, and no schema
    function trusted: what is in the file is data, never code."""
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    db.execute("PRAGMA query_only=1")
    db.execute("PRAGMA trusted_schema=0")
    return db


def read_meta(db) -> dict:
    """The store's meta; {} when it has none it can read."""
    try:
        return {str(k): str(v) for k, v in db.execute("SELECT k, v FROM meta")}
    except BROKEN:
        return {}


def open_cache(
    index: dict, repos: dict, path: Path, index_sha: str, key: bytes, limit: int = MAX
):
    """The cache for this index, key and limit, built (or rebuilt) when it
    isn't: another index, key, limit or version, or another set of repos
    read than can be read now."""
    if cache_ready(index, repos, path, index_sha, key, limit):
        return connect_ro(path), "warm"
    build_cache(index, repos, path, index_sha, key, limit)
    return connect_ro(path), "built"


def cache_ready(
    index: dict, repos: dict, path: Path, index_sha: str, key: bytes, limit: int = MAX
) -> bool:
    """Whether the cache at `path` is the one this index, key, limit, version
    and set of readable repos would build, so it needs no build."""
    if not path.is_file() or path.is_symlink():
        return False
    try:
        db = connect_ro(path)
    except BROKEN:
        return False
    try:
        have = read_meta(db)
    except BROKEN:
        return False
    finally:
        db.close()
    return {k: have.get(k) for k in CORE} == _meta(index_sha, limit, key) and have.get(
        "repos"
    ) == json.dumps(reachable(index, repos))


# ── the store built in the background ──────────────────────────────────────
# The operator: "loaded everything up in the background, in anticipation of
# my next enter stroke" (next-pile.md). Check-in writes a `building` row and
# starts this; it writes only the store file, never the record (one writer),
# and reads the key itself from the keys dir, so no key sits in a spec.
LOCK = "build.lock"


def build_spec(box: Path, index_sha: str, repos: dict, key_path: Path) -> Path:
    """The builder's orders, beside the store: no key, only where it lives."""
    spec = store_path(box).parent / "build.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "box": str(Path(box).resolve()),
        "index_sha256": index_sha,
        "repos": {n: str(Path(p)) for n, p in sorted(repos.items())},
        "key_path": str(key_path),
    }
    tmp = spec.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.replace(tmp, spec)
    return spec


def building(box: Path) -> bool:
    """Whether a builder holds the lock now (a lock whose process is gone is
    stale, and is cleared)."""
    lock = store_path(box).parent / LOCK
    try:
        pid = int(lock.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        lock.unlink(missing_ok=True)
        return False


def build_from_spec(spec_path: Path) -> int:
    """The background builder: 0 built, 1 refused (with why on stderr)."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    box = Path(spec["box"])
    lock = store_path(box).parent / LOCK
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("a build already holds the lock", file=sys.stderr)
        return 1
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        index, sha, _ = load_index(box)
        if index is None or sha != spec["index_sha256"]:
            print("the index moved since check-in", file=sys.stderr)
            return 1
        key, why = read_key(Path(spec["key_path"]))
        if key is None:
            print(f"no store key: {why}", file=sys.stderr)
            return 1
        repos = {n: Path(p) for n, p in spec["repos"].items()}
        build_cache(index, repos, store_path(box), sha, key)
        return 0
    finally:
        lock.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_ino, a.st_size, a.st_mtime_ns) == (b.st_ino, b.st_size, b.st_mtime_ns)


def open_store(
    path: Path, sha: object, key: bytes, index_sha: str, limit: int = MAX
) -> tuple["Cached | None", str | None]:
    """The one store opener: (Cached, None) or (None, why), never an
    exception. Read once: the file is stat'ed, hashed against `sha` (its
    `store` row's), opened read-only and immutable, read, and stat'ed again;
    a file that moved under the read is `unreachable`. Its meta must be this
    index's, this key's and this limit's. Capped in size, and so is every
    posting it decompresses."""
    try:
        st = os.lstat(path)
    except OSError:
        return None, "no store file"
    if not stat.S_ISREG(st.st_mode):
        return None, "the store isn't a plain file"
    if st.st_size > MAX_STORE:
        return None, f"the store is over its cap ({MAX_STORE} bytes)"
    try:
        if not isinstance(sha, str) or file_sha256(path) != sha:
            return None, "the store doesn't hash to its row; check in again"
        db = connect_ro(path)
    except (OSError, sqlite3.Error) as e:
        return None, f"the store can't be opened: {type(e).__name__}"
    c, why = cached(db, key, _meta(index_sha, limit, key))
    try:
        moved = not _same(st, os.lstat(path))
    except OSError:
        moved = True
    if c is None or moved:
        db.close()
        return None, why or "the store changed while it was read; check in again"
    return c, None


def cached(
    db, key: bytes, want: dict | None = None
) -> tuple["Cached | None", str | None]:
    """`Cached(db, key)`, or (None, why) when the store can't be read as one,
    was made with another key, or (with `want`) for another index or limit."""
    meta = read_meta(db)
    if not meta:
        return None, "the store has no meta it can read"
    if meta.get("key") != fingerprint(key):
        return None, "the store was made with another key"
    if want is not None and {k: meta.get(k) for k in CORE} != want:
        return None, "the store was built for another index; check in again"
    try:
        return Cached(db, key, meta), None
    except BROKEN as e:
        return None, f"the store can't be read: {type(e).__name__}"


def _clean(s: object) -> str:
    """A name a store hands back: printable, and capped."""
    if not isinstance(s, str):
        return "?"
    return "".join(ch if ch.isprintable() else "?" for ch in s)[:PATH_CAP]


class Cached:
    """Rung 1's statistics from the cache, for `score`. `rank` and `quotes`
    answer with a state, never an exception."""

    def __init__(self, db, key: bytes, meta: dict | None = None):
        import array

        self.db, self.key, self._array = db, key, array
        self.meta = read_meta(db) if meta is None else meta
        rows = db.execute("SELECT id, sha, n, copies, repo, path, line FROM chunk")
        self.where, self.n, self.copies, self._cid = {}, {}, {}, {}
        total = 0
        for cid, sha, n, cp, repo, path, line in rows:
            if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
                raise ValueError("a chunk without a SHA-256")
            if not all(isinstance(x, int) for x in (cid, n, cp, line)):
                raise ValueError("a chunk row that isn't whole numbers")
            self.where[cid] = (_clean(repo), _clean(path), line, sha)
            self._cid[self.where[cid]] = cid
            self.n[cid] = n
            self.copies[sha] = cp
            total += cp
        self.n_all = len(self.n)
        self.avg = sum(self.n.values()) / self.n_all if self.n_all else 0.0
        self.extra = total - self.n_all
        self._memo: dict = {}

    def tally(self) -> dict:
        """What the build reached, as its meta says."""
        out = {}
        for k in ("repos", "files_read", "skipped", "unreachable"):
            try:
                out[k] = json.loads(self.meta[k])
            except (KeyError, ValueError):
                out[k] = None
        return out

    def _inflate(self, blob: bytes):
        d = zlib.decompressobj()
        raw = d.decompress(blob, MAX_POSTING)
        if d.unconsumed_tail or not d.eof:
            raise ValueError("a posting over its cap")
        a = self._array.array("I")
        a.frombytes(raw)
        return a

    def postings(self, w: str) -> list:
        """(cid, position, field) for every occurrence of `w`."""
        if w not in self._memo:
            row = self.db.execute(
                "SELECT cids, pos FROM post WHERE term = ?", (term(w, self.key),)
            ).fetchone()
            if row is None:
                self._memo[w] = []
            else:
                a, b = self._inflate(row[0]), self._inflate(row[1])
                if len(a) != len(b) or not set(a) <= self.n.keys():
                    raise ValueError("a posting that names no chunk")
                if any(p & 3 == 3 for p in b):
                    raise ValueError("a posting in no field")
                self._memo[w] = [(c, p >> 2, p & 3) for c, p in zip(a, b)]
        return self._memo[w]

    def df(self, w: str) -> int:
        """How many chunks hold `w`."""
        return len({c for c, _, _ in self.postings(w)})

    def counts(self, words: list[str]) -> dict:
        """Seed's counts from the store (D6, the operator, 2026-10-18: "C"):
        per word, exactly as written, the chunks and files that hold it and
        the first `seed.ANCHORS` chunks' starts, rarest first. Chunks, not
        lines: the store keeps positions per chunk. The same three states as
        `seed.scan`; `unreachable` when the store can't be read."""
        keys, missed = [], []
        try:
            for w in words:
                at = sorted({self.where[c][:3] for c, _, _ in self.postings(w)})
                if not at:
                    missed.append(w)
                    continue
                keys.append(
                    {
                        "word": w,
                        "files": len({(r, p) for r, p, _ in at}),
                        "chunks": len(at),
                        "anchors": [list(a) for a in at[: seed.ANCHORS]],
                    }
                )
        except BROKEN as e:
            why = f"the store can't be read: {type(e).__name__}"
            return {"state": UNREACHABLE, "why": why, "keys": [], "missed": words}
        keys.sort(key=lambda k: (k["files"], k["chunks"], k["word"]))
        t = self.tally()
        state = (
            UNREACHABLE
            if not self.n_all
            else "seeded"
            if keys
            else STEP_ESCALATED_COLD_START
        )
        return {
            "state": state,
            "keys": keys,
            "missed": missed,
            "files_read": t["files_read"],
            "skipped": t["skipped"] or {},
            "unreachable": t["unreachable"] or {},
        }

    def repair(self, bite: str) -> dict:
        """Rung 2, over hashes: a word of the bite the store holds in no form
        is tried at every spelling one edit away (`edits1`), each hashed with
        the store's key and looked up; the spelling most chunks hold wins, ties
        to the first in order. The store still holds no word list: the
        candidates come from the bite. A word it holds is never touched, nor
        one shorter than REPAIR_MIN or not plain a-z. `repaired` or `none`, with
        the bite as repaired and each [word, spelling, chunks]; `unreachable`
        (the bite unchanged) when the store can't be read."""
        try:
            fixes = {}
            for w in dict.fromkeys(WORD.findall(bite.lower())):
                if len(w) < REPAIR_MIN or not (w.isascii() and w.isalpha()):
                    continue
                if any(self.postings(f) for f in sorted(forms(w))):
                    continue
                best = min(
                    ((-d, x) for x in edits1(w) if (d := self.df(x))), default=None
                )
                if best is not None:
                    fixes[w] = (best[1], -best[0])
        except BROKEN as e:
            why = f"the store can't be read: {type(e).__name__}"
            return {"state": UNREACHABLE, "why": why, "bite": bite, "repairs": []}
        fixed = WORD.sub(
            lambda m: fixes.get(m.group(0).lower(), (m.group(0),))[0], bite
        )
        return {
            "state": "repaired" if fixes else "none",
            "why": "",
            "bite": fixed,
            "repairs": [[w, x, d] for w, (x, d) in sorted(fixes.items())],
        }

    def rank(self, bite: str, embed=None) -> dict:
        try:
            return self._rank(bite, embed)
        except BROKEN as e:
            why = f"the store can't be read: {type(e).__name__}"
            return {"state": UNREACHABLE, "why": why, "fused": False, "hits": []}

    def _rank(self, bite: str, embed=None) -> dict:
        if not self.n_all:
            return nothing(self.tally())
        words, pairs = wanted(bite)
        weight = (1, QUESTION, TITLE)
        grams: dict = {}
        df_raw = collections.Counter()

        def add(x: str, hits) -> None:
            counts: dict = {}
            for c, f in hits:
                k = counts.setdefault(c, [0, 0, 0])
                k[f] += 1
            if counts:
                where = self.where
                grams[x] = {
                    where[c]: k[0] * weight[0] + weight[1] * k[1] + weight[2] * k[2]
                    for c, k in counts.items()
                }
                df_raw[x] = len(counts)

        for w in sorted(words):
            add(w, ((c, f) for c, _, f in self.postings(w)))
        nexts: dict = {}  # one position set per second word, not per pair
        for p in sorted(pairs):
            a, b = p.split(" ")
            first = self.postings(a)
            if not first or not self.postings(b):
                continue
            nxt = nexts.get(b)
            if nxt is None:
                nxt = nexts[b] = {(c, pos) for c, pos, _ in self.postings(b)}
            add(p, ((c, f) for c, pos, f in first if (c, pos + 1) in nxt))
        n_of = {w: self.n[self._cid[w]] for d in grams.values() for w in d}
        return score_grams(
            bite,
            words,
            grams,
            n_of,
            df_raw,
            self.n_all,
            self.avg,
            self.copies,
            self.extra,
            None,
            embed,
        )

    def quotes(self, bite: str) -> dict:
        """The bite's sentences said before, by keyed hash: a count and where
        first. `populated`, `empty` (none said before) or `unreachable`."""
        out = []
        try:
            for sn in dict.fromkeys(sentences(bite)):
                row = self.db.execute(
                    "SELECT count, cid FROM sent WHERE h = ?", (term(sn, self.key),)
                ).fetchone()
                if row:
                    r, p, ln, _ = self.where[row[1]]
                    if not isinstance(row[0], int):
                        raise ValueError("a sentence count that isn't whole")
                    out.append(
                        {
                            "words": len(sn.split()),
                            "count": row[0],
                            "repo": r,
                            "path": p,
                            "line": ln,
                        }
                    )
        except BROKEN as e:
            why = f"the store can't be read: {type(e).__name__}"
            return {"state": UNREACHABLE, "why": why, "quotes": []}
        if not out:
            return {
                "state": EMPTY,
                "why": "no sentence of the bite said before",
                "quotes": [],
            }
        return {"state": POPULATED, "why": "", "quotes": out}


# ── the session's store, and a chunk read back at its blob ─────────────────
def store_path(box: Path, limit: int = MAX) -> Path:
    """The session's hashed pile, beside its box (never inside it: the box
    holds what the record points at, and this is rebuilt, not pointed at)."""
    return box.with_name(box.name + "-store") / f"pile-{limit}.sqlite"


def read_chunk(index: dict, repos: dict, chunk: dict, limit: int = MAX):
    """(text, None) for a chunk read at its indexed blob, or (None, why).
    Content stays where it lives: the text is re-read here (`seed.Texts`), and
    only while the blob hashes to the index and the chunk hashes to its own
    SHA-256."""
    name, path = chunk.get("repo"), chunk.get("path")
    arrival = index.get("arrival", {}).get(name, {})
    f = next((x for x in arrival.get("files", []) if x.get("path") == path), None)
    if f is None or f.get("state") != "present" or not f.get("blob_match"):
        return None, "not in the index"
    texts = seed.Texts(index, repos, only=(name, path))
    for _, _, text in texts:
        for line, s, _ in chunks(text, limit):
            if line == chunk.get("line") and hashlib.sha256(
                s.encode()
            ).hexdigest() == chunk.get("sha256"):
                return s, None
        return None, "the chunk isn't at that line with that hash"
    return None, texts.unreachable.get(name) or next(iter(texts.skipped), "not read")


# ── across sessions: piles merged by hash, a view rebuilt on demand ─────────
def merge(stores: dict, bite: str) -> dict:
    """Each session's store ranks the bite on its own; the hits merge by chunk
    hash (one chunk, with every session that holds it and every place it sits)
    and the ranks fuse by RRF. Nothing is written and no store reads another:
    "cross-session lookups are piles merged by hash", never a shared soil.
    `stores` maps a session's name to its `Cached`. Each session answers
    `{state, why}`; one whose store fails is `unreachable` and adds nothing,
    and the rest still answer."""
    states, orders, by_chunk, quotes = {}, [], {}, []
    for name in sorted(stores):
        c = stores[name]
        r, q = c.rank(bite), c.quotes(bite)
        if UNREACHABLE in (r["state"], q["state"]):
            bad = r if r["state"] == UNREACHABLE else q
            states[name] = {"state": UNREACHABLE, "why": bad["why"]}
            continue
        states[name] = {"state": r["state"], "why": r.get("why", "")}
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
        quotes += [{"session": name, **x} for x in q["quotes"]]
    hits = [by_chunk[k] for k in rrf(*orders)]
    for h in hits:
        h["seen_in"] = len(h["sessions"])
    return {"sessions": states, "hits": hits, "quotes": quotes}


def _bundled_tokenizers(wordllama, cache: Path) -> None:
    """WordLlama 0.4 ships its tokenizer configs in its package's `tokenizers/`
    but looks for them in its own `tokenizer/` or in the cache's tokenizer dir,
    so with downloads off the bundled model can't load. Copy the bundled
    configs into the cache, once: still local files only, nothing fetched."""
    import shutil  # noqa: PLC0415

    shipped = Path(sys.modules[wordllama.__module__].__file__).parent / "tokenizers"
    dst = wordllama.get_file_path("tokenizer", cache)
    for f in (
        sorted(shipped.glob("*_tokenizer_config.json")) if shipped.is_dir() else ()
    ):
        if not (dst / f.name).exists():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, dst / f.name)


def wordllama_embed(cache: Path):
    """Rung 3, from local files only: WordLlama's bundled table, no download.
    Returns None (and the rank stays rung 1 alone) when it can't load."""
    try:
        from wordllama import WordLlama  # noqa: PLC0415

        _bundled_tokenizers(WordLlama, Path(cache))
        wl = WordLlama.load(cache_dir=cache, disable_download=True)
    except Exception as e:  # noqa: BLE001 — absent is a state, said
        print(f"embed unreachable: {type(e).__name__}", file=sys.stderr)
        return None

    vecs: dict = {}  # chunk SHA-256 -> vector, for the life of this run

    def order(bite: str, cands: list) -> list:
        q = wl.embed([bite], norm=True)[0]
        new = [(w[3], t) for w, t in cands if w[3] not in vecs]
        if new:
            # a chunk's title lines may carry it past MAX; the embedder pads
            # a batch to its longest text, so it reads only the head
            v = wl.embed([t[:EMBED_CHARS] for _, t in new], norm=True, batch_size=256)
            vecs.update(zip((h for h, _ in new), v))
        s = [float(vecs[w[3]] @ q) for w, _ in cands]
        return [
            cands[i][0]
            for i in sorted(range(len(cands)), key=lambda i: (-s[i], cands[i][0]))
        ]

    return order


def sweep(index: dict, repos: dict, queries: list, limits: list, cache=None) -> dict:
    """Mean reciprocal rank of each query's target, at each chunk size. A
    target is `repo:path` or a prefix of one (a directory, or `repo:`). A
    query with no target is a control: it is left out of the MRR, and
    `controls` counts the hits each one drew (the scored baseline,
    `phrase_rank_queries.json`, is read the same way). `cache` is
    (dir, index_sha, key): each size reads its own keyed cache."""
    out = {}
    want = set()
    for q in queries:
        want |= set().union(*wanted(q["bite"]))
    for limit in limits:
        if cache:
            db, _ = open_cache(
                index,
                repos,
                cache[0] / f"pile-{limit}.sqlite",
                cache[1],
                cache[2],
                limit,
            )
            c = Cached(db, cache[2])
            ranker = c.rank
        else:
            pile = read_pile(index, repos, want, limit)

            def ranker(b, pile=pile, limit=limit):
                return rank(index, repos, b, limit, pile=pile)

        rr, controls = [], []
        for q in queries:
            hits = ranker(q["bite"])["hits"]
            if not q["targets"]:
                controls.append(len(hits))
                continue
            ranks = [
                i
                for i, h in enumerate(hits, 1)
                if any(f"{h['repo']}:{h['path']}".startswith(t) for t in q["targets"])
            ]
            rr.append(1 / ranks[0] if ranks else 0.0)
        out[limit] = {
            "mrr": round(sum(rr) / len(rr), 4) if rr else 0.0,
            "rr": [round(x, 4) for x in rr],
            "controls": controls,
        }
    return out


def load_index(box: Path):
    """(index, sha, None) for the box's xref index while it hashes to its pile
    pointer (`seed.read_index`), else (None, None, why)."""
    from .record import Record  # noqa: PLC0415

    if not (Path(box) / "pile.json").is_file():
        return None, None, "no pile in that box"
    return seed.read_index(Record(Path(box), lambda: "", "", object()))


def cli_key(path: Path | None) -> tuple[bytes | None, str | None]:
    """The store key for `--cache`: read from `--key FILE`, or, without one,
    an ephemeral key, said on stderr (the cache is rebuilt every run, and no
    later run can read it)."""
    if path is not None:
        return read_key(path)
    print(
        "no --key: an ephemeral store key, so the cache is rebuilt each run",
        file=sys.stderr,
    )
    return secrets.token_bytes(KEY_BYTES), None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--build-store"] and len(argv) == 2:
        return build_from_spec(Path(argv[1]))
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
        "--repair",
        action="store_true",
        help="with --cache: repair unseen words one edit away (rung 2) first",
    )
    ap.add_argument(
        "--cache",
        type=Path,
        help="dir for the keyed pile cache (built once per index, key and --max)",
    )
    ap.add_argument(
        "--key", type=Path, help="the cache's store key (32 bytes, hex, 0600)"
    )
    a = ap.parse_args(argv)
    if a.cache and a.embed:
        ap.error("--embed reads chunk text, and the cache holds no text")
    if a.key and not a.cache:
        ap.error("--key is the --cache's key")
    index, index_sha, why = load_index(a.box)
    if index is None:
        print(json.dumps({"state": UNREACHABLE, "why": why}))
        return 1
    key = None
    if a.cache:
        key, why = cli_key(a.key)
        if key is None:
            print(json.dumps({"state": UNREACHABLE, "why": why}))
            return 1
    repos = {n: root.parent / n for n in index.get("arrival", {})}
    for spec in a.repo:
        name, _, path = spec.partition("=")
        repos[name] = Path(path)
    if a.sweep:
        queries = json.loads(a.sweep.read_text(encoding="utf-8"))["queries"]
        limits = [int(x) for x in a.max_values.split(",")]
        cache = (a.cache, index_sha, key) if a.cache else None
        print(json.dumps(sweep(index, repos, queries, limits, cache), indent=1))
        return 0
    if not a.bite:
        ap.error("a bite, or --sweep")
    if a.cache:
        db, state = open_cache(
            index, repos, a.cache / f"pile-{a.max}.sqlite", index_sha, key, a.max
        )
        c = Cached(db, key)
        fix = c.repair(a.bite) if a.repair else None
        out = c.rank(fix["bite"] if fix else a.bite)
        if fix:
            out["repair"] = {k: fix[k] for k in ("state", "repairs")}
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
