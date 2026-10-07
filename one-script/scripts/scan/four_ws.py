"""four_ws: which of who, what, when, where each markdown table carries.

A table's cells often hold who and where; its when (and often its who) lives
above it, in the section's dated italic line ("*2026-10-05, desk session …*").
So each table inherits the W's of the nearest dated line under its headings,
and is counted twice: from its cells alone, and with what it inherits.

    python3 four_ws.py [root]            # summary (root defaults to docs/)
    python3 four_ws.py [root] --rows     # one line per table

Read only. Stdlib only. Same tree, same output: files sorted, no clock.
The patterns are deliberately plain; a W that isn't written the usual way
isn't found, and the miss shows up as a table without it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PERSONAS = (
    "willow|heimdallr|hanuman|opus|ada|steve|kart|shiva|ganesha|skirnir|loki|"
    "vishwakarma|jeles|binder|publius|schmidt|nestor|ratatosk"
)
PATTERNS = {
    "who": re.compile(rf"\b(operator|desk|the human|{PERSONAS})\b", re.I),
    "what": re.compile(
        r"\b[0-9a-f]{7,40}\b|#\d{2,4}\b|\bPR \d+|\bKart [A-Z0-9]{8}\b|\bgap `?[0-9a-f]{12}"
    ),
    "when": re.compile(r"\b20\d\d-\d\d-\d\d\b|\bsession `?[0-9a-f]{6,}|\bT\d{2,3}\b"),
    "where": re.compile(
        r"`[^`]*(/|\.py|\.md|\.json|\.sh|\.ya?ml)[^`]*`|\b[\w-]+\.(py|md|json):\d+"
    ),
}
WS = tuple(PATTERNS)
HEADING = re.compile(r"^(#{1,6}) ")
DATED = re.compile(r"^\*20\d\d-\d\d-\d\d")
DIVIDER = re.compile(r"^\|[\s:|-]+\|$")


def found(text: str) -> set[str]:
    return {w for w, rx in PATTERNS.items() if rx.search(text)}


def tables(path: Path) -> list[dict]:
    """Every table in one file, with its own W's and the ones it inherits."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    stack: list[tuple[int, str, set[str]]] = []  # (level, heading, inherited W's)
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        h = HEADING.match(line)
        if h:
            level = len(h.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, line[level + 1 :].strip(), set()))
        elif DATED.match(line) and stack:
            stack[-1][2].update(found(line))
        elif (
            line.startswith("|") and i + 1 < len(lines) and DIVIDER.match(lines[i + 1])
        ):
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            cells = found("\n".join(lines[i:j]))
            inherited = set().union(*(s[2] for s in stack)) if stack else set()
            out.append(
                {
                    "file": str(path),
                    "line": i + 1,
                    "heading": stack[-1][1] if stack else "",
                    "cells": cells,
                    "with_heading": cells | inherited,
                }
            )
            i = j
            continue
        i += 1
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0] if args else "docs")
    if not root.is_dir():
        print(f"unreachable: {root} is not a directory")
        return 1
    rows = [t for p in sorted(root.rglob("*.md")) for t in tables(p)]
    if not rows:
        print(f"empty: no tables under {root}")
        return 0
    if "--rows" in argv:
        for t in rows:
            own = ",".join(w for w in WS if w in t["cells"]) or "-"
            got = ",".join(w for w in WS if w in t["with_heading"]) or "-"
            print(f"{t['file']}:{t['line']}  cells={own}  with_heading={got}")
        return 0
    n = len(rows)
    print(f"{n} tables under {root}")
    print(f"  {'':6} {'cells':>13} {'with heading':>16}")
    for w in WS + ("all four",):
        if w == "all four":
            a = sum(len(t["cells"]) == 4 for t in rows)
            b = sum(len(t["with_heading"]) == 4 for t in rows)
        else:
            a = sum(w in t["cells"] for t in rows)
            b = sum(w in t["with_heading"] for t in rows)
        print(f"  {w:9} {a:5} ({a * 100 // n:2}%)   {b:5} ({b * 100 // n:2}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
