"""shared_phrases: which phrases do the most documents have in common?

    python3 shared_phrases.py [dir ...]     # defaults: docs/design/one-script docs/design/one-box

The pile on top of itself, run on words: a phrase counts once per document,
and the count is how many documents hold it. Links, URLs, code, file names
and any token with a digit in it (ids, hashes, dates) are stripped first, so
what's counted is wording. The copies under `constitution-proposal/` count
as one document. Read only, stdlib only, deterministic.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

STOP = set(
    "the a an and or of to in on for is it its it's that this with as at by be are "
    "was from not no but if then so than into out up all one each any every what "
    "which who when where why how there their they them we you i he she his her our "
    "my your do does did done has have had can could would should will just only "
    "also more most very same own over under about after before here".split()
)
STRIP = [
    re.compile(r"```.*?```", re.S),  # code fences
    re.compile(r"\]\([^)]*\)"),  # link targets
    re.compile(r"https?://\S+"),  # urls
    re.compile(r"`[^`]*`"),  # code spans
    re.compile(r"\b[\w.-]+\.(?:md|py|json|jsonl|mmd|drawio|sh)\b"),  # file names
    re.compile(r"\S*\d\S*"),  # anything with a digit: ids, hashes, dates
]
WORD = re.compile(r"[a-z][a-z'-]*")


def document(path: Path) -> str:
    return (
        "constitution-proposal" if "constitution-proposal" in path.parts else str(path)
    )


def phrases(text: str, longest: int = 5) -> set[tuple[str, ...]]:
    for rx in STRIP:
        text = rx.sub(" ", text)
    words = WORD.findall(text.lower())
    out = set()
    for n in range(2, longest + 1):
        for i in range(len(words) - n + 1):
            g = tuple(words[i : i + n])
            if g[0] in STOP or g[-1] in STOP or any(len(w) == 1 for w in g):
                continue
            out.add(g)
    return out


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [
        Path("docs/design/one-script"),
        Path("docs/design/one-box"),
    ]
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        print("unreachable: " + ", ".join(missing))
        return 1
    held: dict[str, set] = {}
    for root in roots:
        for f in sorted(root.rglob("*.md")):
            held.setdefault(document(f), set()).update(
                phrases(f.read_text(encoding="utf-8", errors="replace"))
            )
    if not held:
        print("empty: no markdown under " + ", ".join(map(str, roots)))
        return 0
    count = Counter(g for gs in held.values() for g in gs)
    print(f"{len(held)} documents")
    for n in (2, 3, 4):
        top = sorted((g for g in count if len(g) == n), key=lambda g: (-count[g], g))
        print(f"\n{n}-word phrases in the most documents:")
        for g in top[:16]:
            print(f"  {count[g]:3}  {' '.join(g)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
