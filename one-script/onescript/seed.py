"""2 predict, the seed: the first bite's words, scanned against the box.

Cites: CONST-IV (Ground: the user's own sources first), INVARIANTS §1 (three
states).

The operator, 2026-10-18: "the pile, scan the first bite's words against the
box." The Forge's entry (`forge/entry.py`) seeds a project from the first
sentence by asking a table; this seeds a check-in by asking the pile. The first
input after a check-in is the only one that seeds; every later turn reads it.

The pile is the box's `xref/index.json`, and only while it hashes to the
record's pointer for it. Each indexed file is read at the indexed blob (git's
own object, not the working tree) and counted only when its bytes hash to the
index's SHA-256. A word is a run of letters or digits, lowercased; a hit is
exact, on a line. Nothing fuzzes, nothing is dropped as too common: a word that
is everywhere says so by its count, and the keys run rarest first, because the
one-offs are what point somewhere.

| State | When |
|---|---|
| `seeded` | at least one word of the bite is in the box |
| `escalated:cold_start` | the box was read and holds none of them |
| `unreachable` | no index the record vouches for, or no repo it names could be read |

Same index, same repos, same bite: the same seed, byte for byte. No clock.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .xref import GitError, git

INDEX = "xref/index.json"
ANCHORS = 5  # anchors kept per word; the counts are always whole
WORD = re.compile(r"[^\W_]+")


def words(bite: str) -> list[str]:
    """The bite's distinct words, lowercased and sorted."""
    return sorted(set(WORD.findall(bite.lower())))


def read_index(rec) -> tuple[dict | None, str | None]:
    """The box's index, only as the pile's pointer vouches for it."""
    ptr = next((i for i in rec.pile()["items"] if i["where"] == INDEX), None)
    if ptr is None:
        return None, "no xref index in the pile; run `onescript.xref index --record`"
    try:
        data = (rec.box / INDEX).read_bytes()
    except OSError as e:
        return None, f"the pile's index can't be read: {type(e).__name__}"
    if hashlib.sha256(data).hexdigest() != ptr["sha"]:
        return None, "the box's index doesn't hash to its pile pointer"
    try:
        return json.loads(data), None
    except ValueError:
        return None, "the box's index isn't JSON"


def _blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    """Every blob in one `cat-file --batch`; a missing one is left out."""
    out = git(
        repo, "cat-file", "--batch", stdin="".join(o + "\n" for o in oids).encode()
    )
    got, i = {}, 0
    while i < len(out):
        nl = out.index(b"\n", i)
        head = out[i:nl].split()
        if len(head) == 3:
            size = int(head[2])
            got[head[0].decode()] = out[nl + 1 : nl + 1 + size]
            i = nl + 1 + size + 1
        else:  # "<oid> missing"
            i = nl + 1
    return got


def scan(index: dict, repos: dict[str, Path], bite_words: list[str]) -> dict:
    """The words against every file the index holds. `repos` names where each
    indexed repo is read from; a repo without one is unreachable, said."""
    want = set(bite_words)
    hits: dict[str, list[tuple[str, str, int]]] = {w: [] for w in bite_words}
    skipped: dict[str, int] = {}
    unreachable: dict[str, str] = {}
    read = 0

    def skip(why: str) -> None:
        skipped[why] = skipped.get(why, 0) + 1

    for name, arrival in sorted(index.get("arrival", {}).items()):
        path = repos.get(name)
        if path is None or not Path(path).is_dir():
            unreachable[name] = "no checkout to read it from"
            continue
        files = [
            f
            for f in arrival.get("files", [])
            if f.get("state") == "present" and f.get("blob_match")
        ]
        try:
            blobs = _blobs(Path(path), sorted({f["git_blob"] for f in files}))
        except GitError as e:
            unreachable[name] = f"git: {str(e)[:80]}"
            continue
        for f in files:
            data = blobs.get(f["git_blob"])
            if data is None:
                skip("blob not in the checkout")
                continue
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                skip("bytes don't hash to the index")
                continue
            if b"\0" in data:
                skip("binary")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                skip("not utf-8")
                continue
            read += 1
            for n, line in enumerate(text.splitlines(), 1):
                for w in want.intersection(WORD.findall(line.lower())):
                    hits[w].append((name, f["path"], n))

    keys = []
    for w in bite_words:
        found = hits[w]
        if found:
            keys.append(
                {
                    "word": w,
                    "files": len({(r, p) for r, p, _ in found}),
                    "lines": len(found),
                    "anchors": [list(a) for a in found[:ANCHORS]],
                }
            )
    keys.sort(key=lambda k: (k["files"], k["lines"], k["word"]))
    missed = [w for w in bite_words if not hits[w]]
    if not read:
        state = "unreachable"
    elif keys:
        state = "seeded"
    else:
        state = "escalated:cold_start"
    return {
        "state": state,
        "keys": keys,
        "missed": missed,
        "files_read": read,
        "skipped": dict(sorted(skipped.items())),
        "unreachable": dict(sorted(unreachable.items())),
    }


def since_boot(rows: list[dict]) -> list[dict]:
    """The rows of the current check-in: everything after the last boot."""
    i = max((k for k, r in enumerate(rows) if r["kind"] == "boot"), default=-1)
    return rows[i + 1 :]
