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
WORD = re.compile(r"[^\W_]+")  # a word, for seed and the pile alike


def words(bite: str) -> list[str]:
    """The bite's distinct words, lowercased and sorted."""
    return sorted(set(WORD.findall(bite.lower())))


def read_index(rec) -> tuple[dict | None, str | None, str | None]:
    """(index, sha, None): the box's index and the pile pointer's SHA-256 for
    it, only as that pointer vouches for it; else (None, None, why). Callers
    take the sha from here and never re-derive it."""
    try:
        items = rec.pile()["items"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, None, f"the pile can't be read: {type(e).__name__}"
    ptr = next(
        (i for i in items if isinstance(i, dict) and i.get("where") == INDEX), None
    )
    if ptr is None:
        return (
            None,
            None,
            "no xref index in the pile; run `onescript.xref index --record`",
        )
    try:
        data = (rec.box / INDEX).read_bytes()
    except OSError as e:
        return None, None, f"the pile's index can't be read: {type(e).__name__}"
    if hashlib.sha256(data).hexdigest() != ptr.get("sha"):
        return None, None, "the box's index doesn't hash to its pile pointer"
    try:
        index = json.loads(data)
    except ValueError:
        return None, None, "the box's index isn't JSON"
    if not isinstance(index, dict):
        return None, None, "the box's index isn't an object"
    return index, ptr["sha"], None


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


class Texts:
    """The one reader of indexed text: seed's scan, the pile, its store and a
    chunk read back all go through it. Each file is read at its indexed blob
    and yielded as (repo, file, text) only when its bytes hash to the index,
    hold no NUL and decode as UTF-8. Every skip is tallied by reason; a repo
    with no checkout, or whose git fails, is `unreachable`, said, never
    silently left out. `only` = (repo, path) reads that one file."""

    def __init__(self, index: dict, repos: dict, only: tuple[str, str] | None = None):
        self.index, self.repos, self.only = index, repos, only
        self.skipped: dict[str, int] = {}
        self.unreachable: dict[str, str] = {}
        self.repos_read: list[str] = []
        self.read = 0

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def __iter__(self):
        arrivals = self.index.get("arrival", {})
        for name, arrival in sorted(
            arrivals.items() if isinstance(arrivals, dict) else ()
        ):
            if self.only is not None and name != self.only[0]:
                continue
            path = self.repos.get(name)
            if path is None or not Path(path).is_dir():
                self.unreachable[name] = "no checkout to read it from"
                continue
            files = [
                f
                for f in (arrival.get("files", []) if isinstance(arrival, dict) else [])
                if isinstance(f, dict)
                and f.get("state") == "present"
                and f.get("blob_match")
                and (self.only is None or f.get("path") == self.only[1])
            ]
            try:
                blobs = _blobs(Path(path), sorted({str(f["git_blob"]) for f in files}))
            except (GitError, ValueError, KeyError) as e:
                self.unreachable[name] = f"git: {str(e)[:80]}"
                continue
            self.repos_read.append(name)
            for f in files:
                data = blobs.get(str(f["git_blob"]))
                if data is None:
                    self._skip("blob not in the checkout")
                    continue
                if hashlib.sha256(data).hexdigest() != f.get("sha256"):
                    self._skip("bytes don't hash to the index")
                    continue
                if b"\0" in data:
                    self._skip("binary")
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    self._skip("not utf-8")
                    continue
                self.read += 1
                yield name, f, text

    def tally(self) -> dict:
        """What the read reached: the repos read, the files, every skip by
        reason and every unreachable repo by why."""
        return {
            "repos": sorted(self.repos_read),
            "files_read": self.read,
            "skipped": dict(sorted(self.skipped.items())),
            "unreachable": dict(sorted(self.unreachable.items())),
        }


def scan(index: dict, repos: dict[str, Path], bite_words: list[str]) -> dict:
    """The words against every file the index holds. `repos` names where each
    indexed repo is read from; a repo without one is unreachable, said."""
    want = set(bite_words)
    hits: dict[str, list[tuple[str, str, int]]] = {w: [] for w in bite_words}
    texts = Texts(index, repos)
    for name, f, text in texts:
        for n, line in enumerate(text.splitlines(), 1):
            for w in want.intersection(WORD.findall(line.lower())):
                hits[w].append((name, f["path"], n))
    read = texts.read

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
        "skipped": texts.tally()["skipped"],
        "unreachable": texts.tally()["unreachable"],
    }


def since_boot(rows: list[dict]) -> list[dict]:
    """The rows of the current check-in: everything after the last boot."""
    i = max((k for k, r in enumerate(rows) if r["kind"] == "boot"), default=-1)
    return rows[i + 1 :]
