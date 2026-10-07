#!/usr/bin/env python3
"""Script match: checks 1-4 from next-pile.md (gate), run read-only over a session.

Builds an index of every Python script the session saw, in the order it saw them:
inline heredocs and `python3 -c` strings from the transcript's Bash calls, plus
the saved .py files under session-flow/. Each script is checked against
everything earlier in the index; the first check that matches decides:

  1 same name     (saved files only: basename seen before)
  2 renamed copy  (same content hash)
  3 variant       (same syntax-tree shape, names/strings/numbers/comments stripped)
  4 similar       (Jaccard over imports, defs, calls >= THRESHOLD)
  5 new

Deterministic: same transcript and files give the same output bytes.
Read-only: writes nothing but stdout.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

THRESHOLD = 0.6  # placeholder; the operator's number

HEREDOC = re.compile(
    r"python3?\s+-\s*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\1\b", re.S
)
DASH_C = re.compile(r"python3?\s+-c\s+(['\"])(.*?)(?<!\\)\1", re.S)


class _Strip(ast.NodeTransformer):
    """Strip names, strings and numbers so only the shape is left."""

    def visit_Name(self, n):
        return ast.copy_location(ast.Name(id="_", ctx=n.ctx), n)

    def visit_Constant(self, n):
        return ast.copy_location(ast.Constant(value=type(n.value).__name__), n)

    def visit_Attribute(self, n):
        self.generic_visit(n)
        n.attr = "_"
        return n

    def visit_arg(self, n):
        n.arg, n.annotation = "_", None
        return n

    def visit_FunctionDef(self, n):
        self.generic_visit(n)
        n.name = "_"
        return n

    def visit_alias(self, n):
        n.name, n.asname = "_", None
        return n


def features(tree: ast.AST) -> frozenset[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {f"import:{a.name}" for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out |= {f"import:{n.module}.{a.name}" for a in n.names}
        elif isinstance(n, ast.FunctionDef):
            out.add(f"def:{n.name}")
        elif isinstance(n, ast.Call):
            f = n.func
            out.add(
                f"call:{f.id}"
                if isinstance(f, ast.Name)
                else f"call:.{f.attr}"
                if isinstance(f, ast.Attribute)
                else "call:?"
            )
    return frozenset(out)


def entry(source: str, label: str, name: str | None) -> dict:
    e = {
        "label": label,
        "name": name,
        "lines": source.count("\n") + 1,
        "sha": hashlib.sha256(source.encode()).hexdigest()[:16],
    }
    try:
        tree = ast.parse(source)
    except SyntaxError:
        e["parse"] = "failed"
        return e
    shape = ast.dump(_Strip().visit(ast.parse(source)), annotate_fields=False)
    e["shape"] = hashlib.sha256(shape.encode()).hexdigest()[:16]
    e["features"] = features(tree)
    return e


def from_transcript(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        o = json.loads(line)
        if o.get("type") != "assistant":
            continue
        for c in o["message"].get("content", []):
            if c.get("type") != "tool_use" or c.get("name") != "Bash":
                continue
            cmd = c["input"].get("command", "")
            desc = c["input"].get("description", "")
            ts = o.get("timestamp", "")[11:16]
            bodies = [m.group(2) for m in HEREDOC.finditer(cmd)]
            bodies += [m.group(2).replace('\\"', '"') for m in DASH_C.finditer(cmd)]
            for b in bodies:
                out.append(entry(b, f"{ts} inline · {desc}", None))
    return out


def from_files(root: Path) -> list[dict]:
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return [
        entry(p.read_text(), f"saved · {p.relative_to(root)}", p.name) for p in files
    ]


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def match(index: list[dict]) -> list[dict]:
    for i, e in enumerate(index):
        earlier = index[:i]
        e["verdict"], e["of"] = "new", None
        if e.get("parse") == "failed":
            e["verdict"] = "unparsed"
            continue
        for check, test in (
            ("same name", lambda x: e["name"] and x["name"] == e["name"]),
            ("renamed copy", lambda x: x["sha"] == e["sha"]),
            ("variant", lambda x: x.get("shape") == e["shape"]),
        ):
            hit = next((j for j, x in enumerate(earlier) if test(x)), None)
            if hit is not None:
                e["verdict"], e["of"] = check, hit
                break
        else:
            scored = [
                (jaccard(e["features"], x["features"]), j)
                for j, x in enumerate(earlier)
                if "features" in x
            ]
            best = max(scored, default=(0.0, None))
            if best[0] >= THRESHOLD:
                e["verdict"], e["of"], e["score"] = (
                    "similar",
                    best[1],
                    round(best[0], 2),
                )
    return index


def families(index: list[dict]) -> list[list[int]]:
    """Follow each match back to its first member: one family per root."""
    root = {}
    for i, e in enumerate(index):
        root[i] = root[e["of"]] if e["of"] is not None else i
    fam: dict[int, list[int]] = {}
    for i, r in root.items():
        fam.setdefault(r, []).append(i)
    return sorted(fam.values(), key=lambda f: (-len(f), f[0]))


def main(transcript: str, root: str) -> None:
    index = match(from_transcript(Path(transcript)) + from_files(Path(root)))
    counts: dict[str, int] = {}
    for e in index:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    print(f"scripts indexed: {len(index)}  threshold: {THRESHOLD}")
    print("verdicts:", json.dumps(dict(sorted(counts.items()))))
    print(
        "\nfamilies of 2+ (first member, then matches; the 3rd member is where the offer fires):"
    )
    for f in families(index):
        if len(f) < 2:
            continue
        print(f"\n[{len(f)}] {index[f[0]]['label']}")
        for k, i in enumerate(f[1:], start=2):
            e = index[i]
            tag = e["verdict"] + (f" {e['score']}" if "score" in e else "")
            print(f"   {k:>2}{' ← offer' if k == 3 else ''}  {tag:<14} {e['label']}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
