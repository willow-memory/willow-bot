"""The one script restates no state word (flatten-2, reverse pass on F5/F9).

``states.py`` is the home of ``populated`` / ``empty`` / ``unreachable``
(flatten F5, F-18). The flatten asserted "the one script restates nothing";
the reverse pass found ~60 uses outside it, and F9 had converted only some.
The operator: "Using the onescript at every path." A state word the code
writes, compares or keys on is ``states.POPULATED`` / ``EMPTY`` /
``UNREACHABLE``, so a typo cannot make a fourth state and a rename is one edit.

The lint: no string constant equal to a state word anywhere in ``onescript/*.py``
except a bare string statement (a docstring) and the two files that cannot
import ``states`` (``states.py`` itself; ``reach_bootstrap.py``, a byte-copied
template). A constant that is NOT a reader's state but reuses the word (a seal's
``unreachable``, a report field that lists the unreachable items, the `empty`
flag of ``needs``) goes on ``ALLOW`` below with the one-line reason. An entry is
``(file, enclosing function, the source line, how many)``; one that no longer
matches fails, so the list cannot rot (the model is F17's listing lint).
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import states  # noqa: E402

ONE_SCRIPT = Path(__file__).resolve().parents[2]
WORDS = frozenset(states.STATES)
EXEMPT = {"states.py", "reach_bootstrap.py"}

# (file, enclosing function, stripped source line) -> (count, reason)
ALLOW: dict[tuple[str, str, str], tuple[int, str]] = {}


def _allow(file: str, fn: str, line: str, reason: str, n: int = 1) -> None:
    ALLOW[(file, fn, line)] = (n, reason)


_allow(
    "api.py",
    "peek",
    'return {"needs": [], "count": 0, "empty": True, "code": 0}',
    "`empty` is the needs answer's boolean flag, not a state word",
)
_allow(
    "api.py",
    "reconcile",
    '"unreachable": rep["wagers"]["unreachable"],',
    "a report field naming the list of unreachable seals (and the tally key it reads), not a state value",
    2,
)
_allow(
    "api.py",
    "refuse",
    'state = {"state": UNREACHABLE} if why.startswith("unreachable") else {}',
    "the prefix of a refusal's why prose, not a state value",
)
_allow(
    "box_lookup.py",
    "_ranked",
    "return UNREACHABLE, [], f\"{name}: {out.get('why') or 'unreachable'}\"",
    "fallback words in a why sentence, not a state value",
)
_allow(
    "market.py",
    "reveal",
    'return {**out, "unreachable": missing} if missing else out',
    "a report field listing the symbols that could not be read",
)
_allow(
    "pile.py",
    "counts",
    '"unreachable": t["unreachable"] or {},',
    "a report field (and the meta key it reads) holding the unreachable repos by why",
    2,
)
_allow(
    "pile.py",
    "tally",
    'for k in ("repos", "files_read", "skipped", "unreachable"):',
    "the meta keys of the build's tally; `unreachable` is the stored field name (D-e: stored keys never change)",
)
_allow(
    "reverse.py",
    "_marked",
    'return item if why is None else {**item, "seal": "unreachable", "seal_why": why}',
    "a seal's own vocabulary (checked or unreachable), not a reader's state",
)
_allow(
    "reverse.py",
    "reconcile_wagers",
    '"unreachable": {r["id"]: r["why"] for r in out if r["state"] == UNREACHABLE},',
    "a report field listing the unreachable rows by why",
)
_allow(
    "reverse.py",
    "reconcile_wagers",
    'elif isinstance(said := ask(), dict) and said.get("unreachable"):',
    'the check callback\'s protocol key ({"unreachable": why}), not a state value',
)
_allow(
    "reverse.py",
    "reconcile_wagers",
    'row |= {"state": UNREACHABLE, "why": f"R3: {said[\'unreachable\']}"}',
    "the check callback's protocol key, read back",
)
_allow(
    "run.py",
    "pooled",
    'item |= {"seal": "unreachable", "why": unchecked[s]}',
    "a seal's own vocabulary (checked or unreachable), not a reader's state",
)
_allow(
    "seed.py",
    "scan",
    '"unreachable": texts.tally()["unreachable"],',
    "a report field (and the tally key it reads) holding the unreachable repos by why",
    2,
)
_allow(
    "seed.py",
    "tally",
    '"unreachable": dict(sorted(self.unreachable.items())),',
    "the tally's stored field name for the unreachable repos",
)
_allow(
    "table.py",
    "check",
    'return {"unreachable": str(got.get("reason"))}',
    'the check callback\'s protocol key ({"unreachable": why}), not a state value',
    3,
)
_allow(
    "table.py",
    "check_agm_head",
    'return {**base, "state": UNREACHABLE, "why": got.get("why", "unreachable")}',
    "fallback word in a why sentence, not a state value",
)
_allow(
    "view.py",
    "_seal",
    'if item.get("seal") != "unreachable":',
    "a seal's own vocabulary (checked or unreachable), not a reader's state",
)


def _hits(path: Path) -> list[tuple[str, str, int]]:
    """(enclosing function, stripped line, line number) per state-word string
    constant that is not a bare string statement."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and node.value in WORDS):
            continue
        if isinstance(parent.get(node), ast.Expr):
            continue  # a docstring or a bare string: prose
        fn, p = "<module>", parent.get(node)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = p.name
                break
            p = parent.get(p)
        out.append((fn, lines[node.lineno - 1].strip(), node.lineno))
    return out


def _sources() -> list[Path]:
    return sorted(
        p for p in (ONE_SCRIPT / "onescript").glob("*.py") if p.name not in EXEMPT
    )


def _leftover(paths: list[Path], allow: dict) -> tuple[list[str], list[str]]:
    seen: Counter = Counter()
    left = []
    for p in paths:
        for fn, line, no in _hits(p):
            key = (p.name, fn, line)
            seen[key] += 1
            if key not in allow:
                left.append(f"{p.name}:{no} in {fn}(): {line}")
    stale = [
        f"{k}: allowlisted {n}, found {seen.get(k, 0)}"
        for k, (n, _) in allow.items()
        if seen.get(k, 0) != n
    ]
    return left, stale


def test_no_state_word_is_restated():
    left, stale = _leftover(_sources(), ALLOW)
    assert not left, (
        "a state word written out (use states.POPULATED / EMPTY / UNREACHABLE, "
        "or allowlist it with the reason it is not a reader's state):\n"
        + "\n".join(left)
    )
    assert not stale, "stale allowlist entries:\n" + "\n".join(stale)


def test_every_allowlist_entry_has_a_reason():
    assert all(r.strip() for _, r in ALLOW.values())


def test_the_lint_sees_a_planted_restatement(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        '"""populated in a docstring is prose."""\n'
        "def a(x):\n"
        '    """empty here too."""\n'
        '    return {"state": "unreachable"}\n'
        "def b(x):\n"
        '    return x["state"] == "empty"\n'
        "def c(x):\n"
        '    return doc("populated", "")\n'
        "def d(x):\n"
        "    return {'state': 'seeded'}\n"
    )
    left, stale = _leftover([f], {})
    assert [s.split(" in ")[0] for s in left] == ["m.py:4", "m.py:6", "m.py:8"]
    assert not stale


def test_the_lint_flags_a_stale_allowlist_entry(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def a(x):\n    return x\n")
    left, stale = _leftover([f], {("m.py", "a", 'return "empty"'): (1, "gone")})
    assert not left and len(stale) == 1


def test_the_lint_flags_an_allowlisted_line_that_appears_twice(tmp_path):
    f = tmp_path / "m.py"
    f.write_text('def a(x):\n    x = "empty"\n    x = "empty"\n')
    left, stale = _leftover([f], {("m.py", "a", 'x = "empty"'): (1, "once")})
    assert not left and len(stale) == 1
