#!/usr/bin/env python3
"""Snippet cover: how much of each idea is already here (IDEAS.md 86).

    python3 snippet_cover.py IDEAS.md NAME=REPO [NAME=REPO ...] [--json]

The operator, 2026-10-21: "I want to see how much of the other can really be
just compiled from what's already here, rather than hand built."

Reads every fenced ```python snippet in IDEAS.md under its numbered heading,
and every committed .py file in the repos (at HEAD, read through git, so an
uncommitted edit counts for nothing). Each name a snippet calls is one of:

  here      defined in the repos, by a name that says where: `module.name`
            where the module is a file stem there, `Class.method`, or a name
            the snippet imports from a module or package that is here
  loose     a bare name the snippet neither imports nor binds, which something
            here happens to define (`get`, `epoch`): not wired until the
            snippet says which one, so it counts as missing
  stdlib    the standard library or a builtin (a bare `Counter` too)
  outside   imported in the snippet from a package the repos don't define
  local     defined or bound inside the snippet itself
  value     a method on a value the snippet made (`rng.choice`): not counted
  missing   none of the above: what would have to be built

An idea is `compiled` when it calls only here/stdlib names, `needs outside`
when it also needs a package, and `needs building` when anything is missing.
`nearest` is the repos' function with the closest features, by
`script_match.features` and `script_match.jaccard` (imports, defs, calls),
shown only at NEAR_FLOOR or above.
A snippet that doesn't parse is read with a plain call pattern instead, said.

Read only, stdlib only. Same files, same output bytes.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import random
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import script_match as sm  # noqa: E402

HEADING = re.compile(r"^#{2,4} (\d+)\. (.*)$")
CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
STDLIB = set(sys.stdlib_module_names) | set(dir(builtins))
# Names a sketch uses bare without importing them, from the stdlib modules
# sketches lean on. Fixed here so the output doesn't drift with what's imported.
STDLIB_BARE = {
    "Counter",
    "defaultdict",
    "OrderedDict",
    "deque",
    "namedtuple",
    "datetime",
    "date",
    "timedelta",
    "timezone",
    "Path",
    "Fraction",
    "Decimal",
    "dataclass",
    "field",
    "partial",
    "reduce",
}
NEAR_FLOOR = 0.3  # placeholder; the operator's number
# The methods the standard library's value types carry. `db.execute`,
# `rng.choice`, `report.get` are calls on a value even where some repo holds a
# `db.py`, `rng.py` or `report.py` the snippet never imported.
VALUE_METHODS = frozenset().union(
    *(
        dir(t)
        for t in (str, bytes, dict, list, set, tuple, int, float, Path)
        + (sqlite3.Connection, sqlite3.Cursor, random.Random, io.StringIO)
    )
)


def snippets(text: str) -> list[dict]:
    """Every ```python block, with the number and title of the item above it."""
    out, item, block = [], None, None
    for line in text.splitlines():
        if block is not None:
            if line.startswith("```"):
                out.append({**item, "source": "\n".join(block)})
                block = None
            else:
                block.append(line)
            continue
        m = HEADING.match(line)
        if m:
            item = {"n": int(m.group(1)), "title": m.group(2)}
        elif line.startswith("```python") and item:
            block = []
    return out


def committed_py(repo: Path) -> list[tuple[str, str]]:
    """(path, text) of every .py file committed at HEAD."""
    names = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    paths = sorted(p for p in names if p.endswith(".py"))
    batch = "".join(f"HEAD:{p}\n" for p in paths)
    raw = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=batch.encode(),
        capture_output=True,
        check=True,
    ).stdout
    out, i = [], 0
    for p in paths:
        header_end = raw.index(b"\n", i)
        size = int(raw[i:header_end].split()[2])
        body = raw[header_end + 1 : header_end + 1 + size]
        i = header_end + 1 + size + 1
        out.append((p, body.decode("utf-8", "replace")))
    return out


def index_repos(repos: dict[str, Path]) -> dict:
    """What is here: each module's top-level names, every defined name, and
    every function as a unit with its features."""
    modules: dict[str, set[str]] = defaultdict(set)
    defined: dict[str, set[str]] = defaultdict(set)
    packages: set[str] = set()
    units = []
    for name in sorted(repos):
        for path, text in committed_py(repos[name]):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            stem = Path(path).stem
            where = f"{name}:{path}"
            if stem == "__init__" and len(Path(path).parts) > 1:
                packages.add(Path(path).parts[-2])
            for node in tree.body:
                for top in _top_names(node):
                    modules[stem].add(top)
                    defined[top].add(where)
                if isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            defined[f"{node.name}.{sub.name}"].add(where)
                            defined[sub.name].add(where)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    feats = sm.features(node)
                    if feats:
                        units.append((f"{where}:{node.lineno} {node.name}", feats))
    return {
        "modules": modules,
        "defined": defined,
        "packages": packages,
        "units": units,
    }


def _top_names(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [t.id for t in targets if isinstance(t, ast.Name)]
    return []


def dotted(node: ast.expr) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def read_snippet(source: str) -> dict:
    """The snippet's calls, imports and locally bound names; by the AST when it
    parses (with `...` lines dropped), else by the plain call pattern."""
    for text in (source, re.sub(r"(?m)^.*\.\.\..*$", "", source)):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imports, bound, calls = {}, set(), []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imports[a.asname or a.name] = f"{node.module}.{a.name}"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)
                bound |= {a.arg for a in node.args.args}
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, ast.Call):
                d = dotted(node.func)
                if d:
                    calls.append(d)
        return {
            "parsed": True,
            "imports": imports,
            "bound": bound,
            "calls": sorted(set(calls)),
            "tree": tree,
        }
    return {
        "parsed": False,
        "imports": {},
        "bound": set(),
        "calls": sorted(set(CALL.findall(source))),
        "tree": None,
    }


def classify(call: str, snip: dict, here: dict) -> str:
    head, *rest = call.split(".")
    mods, defined = here["modules"], here["defined"]
    if head in snip["imports"]:
        target = snip["imports"][head]
        top, last = target.split(".")[0], target.split(".")[-1]
        if top in STDLIB:
            return "stdlib"
        if top not in here["packages"] and top not in mods:
            return "outside"
        if not rest:  # `from onescript.calibration import brier`: brier is here?
            return "here" if last in defined else "missing"
        head = last  # `import sigh` or `from onescript import pile`: a module
    if not rest:
        if head in snip["bound"]:
            return "local"
        if head in STDLIB or head in STDLIB_BARE:
            return "stdlib"
        return "loose" if head in defined else "missing"
    if head in mods:
        if rest[0] in mods[head]:
            return "here"
        if head not in snip["imports"] and rest[0] in VALUE_METHODS:
            return "value"  # a variable that shares a module's name
        return "missing"
    if f"{head}.{rest[0]}" in defined:
        return "here"
    if head in STDLIB:
        return "stdlib"
    if head in snip["bound"]:
        return "value"  # a method on something the snippet made: `SAID.findall`
    if head[0].isupper():
        return "missing"  # a class nothing here defines
    return "value"  # a method on a value: `rng.choice`, `rows.append`


def cover(ideas_text: str, here: dict) -> list[dict]:
    out = []
    for s in snippets(ideas_text):
        snip = read_snippet(s["source"])
        kinds = {c: classify(c, snip, here) for c in snip["calls"]}
        count = defaultdict(int)
        for k in kinds.values():
            count[k] += 1
        missing = sorted(c for c, k in kinds.items() if k in ("missing", "loose"))
        loose = {c: len(here["defined"][c]) for c, k in kinds.items() if k == "loose"}
        outside = sorted(c for c, k in kinds.items() if k == "outside")
        if missing:
            verdict = "needs building"
        elif outside:
            verdict = "needs outside"
        elif count["here"]:
            verdict = "compiled"
        else:
            verdict = "stdlib only"
        near = None
        if snip["tree"] is not None:
            feats = sm.features(snip["tree"])
            if feats:
                best = max(
                    ((sm.jaccard(feats, f), u) for u, f in here["units"]),
                    default=(0.0, None),
                    key=lambda x: (x[0], x[1] or ""),
                )
                if best[0] >= NEAR_FLOOR:
                    near = {"unit": best[1], "jaccard": round(best[0], 3)}
        out.append(
            {
                "n": s["n"],
                "title": s["title"],
                "verdict": verdict,
                "parsed": snip["parsed"],
                "counts": dict(sorted(count.items())),
                "here": sorted(c for c, k in kinds.items() if k == "here"),
                "missing": missing,
                "loose": loose,
                "outside": outside,
                "nearest": near,
            }
        )
    return out


def merge_items(rows: list[dict]) -> list[dict]:
    """One row per idea: an idea with two snippets takes the worse verdict."""
    rank = {"needs building": 0, "needs outside": 1, "compiled": 2, "stdlib only": 3}
    by: dict[int, dict] = {}
    for r in rows:
        if r["n"] not in by:
            by[r["n"]] = {**r, "counts": dict(r["counts"])}
            continue
        m = by[r["n"]]
        if rank[r["verdict"]] < rank[m["verdict"]]:
            m["verdict"] = r["verdict"]
        for k, v in r["counts"].items():
            m["counts"][k] = m["counts"].get(k, 0) + v
        for k in ("here", "missing", "outside"):
            m[k] = sorted(set(m[k]) | set(r[k]))
        m["loose"] = {**m["loose"], **r["loose"]}
        m["parsed"] = m["parsed"] and r["parsed"]
        if r["nearest"] and (
            not m["nearest"] or r["nearest"]["jaccard"] > m["nearest"]["jaccard"]
        ):
            m["nearest"] = r["nearest"]
    return [by[n] for n in sorted(by)]


def report(items: list[dict]) -> str:
    lines, tally = [], defaultdict(list)
    for it in items:
        tally[it["verdict"]].append(it["n"])
    lines.append(f"ideas with a python snippet: {len(items)}")
    for v in ("compiled", "stdlib only", "needs outside", "needs building"):
        ns = tally.get(v, [])
        lines.append(f"  {v:15} {len(ns):3}  {' '.join(map(str, ns))}")
    lines.append("")
    for it in items:
        c = " ".join(f"{k}={v}" for k, v in it["counts"].items())
        p = "" if it["parsed"] else "  (unparsed: read by pattern)"
        lines.append(f"{it['n']:>3}. {it['title']}  [{it['verdict']}]{p}")
        lines.append(f"     {c}")
        if it["here"]:
            lines.append(f"     here:    {', '.join(it['here'])}")
        if it["outside"]:
            lines.append(f"     outside: {', '.join(it['outside'])}")
        if it["missing"]:
            said = [
                f"{c} (loose: {it['loose'][c]} file(s) here define one)"
                if c in it["loose"]
                else c
                for c in it["missing"]
            ]
            lines.append(f"     missing: {', '.join(said)}")
        if it["nearest"] and it["nearest"]["unit"]:
            lines.append(
                f"     nearest: {it['nearest']['unit']} ({it['nearest']['jaccard']})"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if len(args) < 2 or any("=" not in a for a in args[1:]):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    ideas = Path(args[0]).read_text(encoding="utf-8")
    repos = {k: Path(v) for k, v in (a.split("=", 1) for a in args[1:])}
    items = merge_items(cover(ideas, index_repos(repos)))
    if as_json:
        print(json.dumps(items, indent=1, sort_keys=True))
    else:
        sys.stdout.write(report(items))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
