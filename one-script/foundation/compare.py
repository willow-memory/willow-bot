"""Line up two probe results, say where they disagree, and name the cause.

    python3 compare.py a.json b.json [cases.pkl]

The only cause it knows by name is the int-to-text limit (3.11+: an int over
4,300 digits can't become text). Any difference without that cause is counted
as "unexplained", which is the number to read.
"""

import json
import pickle
import sys
from collections import Counter

LIMIT = 4300


def too_big(o) -> bool:
    if isinstance(o, bool):
        return False
    if isinstance(o, int):
        return abs(o) >= 10**LIMIT  # more than LIMIT digits, without making text
    if isinstance(o, dict):
        return any(too_big(v) for v in o.values())
    if isinstance(o, list):
        return any(too_big(v) for v in o)
    return False


def kind(x: str) -> str:
    return x if x.startswith(("!", "not")) else "hash"


def main(a_path, b_path, cases_path="cases.pkl"):
    with open(a_path) as f:
        a = json.load(f)
    with open(b_path) as f:
        b = json.load(f)
    with open(cases_path, "rb") as f:
        cases = pickle.load(f)
    print(f"{a['version']}  vs  {b['version']}")

    pairs, unexplained = Counter(), 0
    for o, x, y in zip(cases["objects"], a["objects"], b["objects"]):
        pairs[(kind(x), kind(y), x == y)] += 1
        if x != y and not too_big(o):
            unexplained += 1
    print(f"objects: {len(a['objects'])}")
    for (kx, ky, same), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}  {kx:>22} | {ky:<22} {'same' if same else 'DIFFERENT'}")
    print(f"  unexplained differences: {unexplained}")

    print("record lines:")
    for line, x, y in zip(cases["lines"], a["lines"], b["lines"]):
        shown = line if len(line) < 30 else line[:12] + f"…({len(line)} chars)"
        mark = "same" if x == y else "DIFFERENT"
        sx, sy = (v if kind(v) != "hash" else "readable" for v in (x, y))
        print(f"  {mark:9} {shown!r:34} {sx:>20} | {sy}")

    print("realpath:")
    for p in a["paths"]:
        x, y = a["paths"][p], b["paths"][p]
        print(f"  {'same' if x == y else 'DIFFERENT':9} {p:28} {x} | {y}")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
