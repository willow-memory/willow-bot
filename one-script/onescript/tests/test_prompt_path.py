"""The prompt hook's path (`prompt_path.py`), the one the human runs on every
Enter, loads its own pieces and nothing else: not the check-in, the gates'
runner, the ladder, the turn or the check-out (`run.py`), and not `api`. Held
two ways: by its import closure (lazy imports count; a `TYPE_CHECKING` import
is a type, not a load), and by what `python -m onescript prompt` actually
imports, run as the hook runs it."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
ONE = PKG.parent  # one-script/

# The record and its stamp (record, canon, sign, and stamp.py, the write
# token's home: the gate re-exports the same objects), the front door, the
# store and its lookup, the chunk serving.
ALLOWED = {
    "prompt_path",
    "opening",
    "boxstore",
    "chunk",
    "record",
    "canon",
    "sign",
    "stamp",
    "clock",
    "states",
    "scrub",
    "pile",
    "seed",
    "words",
    "xref",
    "mai_shape",  # xref's write-shape check, a leaf
}
NEVER = {
    "api",
    "run",
    "gate",
    "boot",
    "reverse",
    "resolve",
    "ladder",
    "view",
    "writeback",
}


def _type_only(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)


def closure(start: str) -> set[str]:
    seen, todo = {start}, [start]
    while todo:
        tree = ast.parse((PKG / f"{todo.pop()}.py").read_text(encoding="utf-8"))
        skip = {id(n) for t in ast.walk(tree) if _type_only(t) for n in ast.walk(t)}
        for n in ast.walk(tree):
            if id(n) in skip or not (isinstance(n, ast.ImportFrom) and n.level == 1):
                continue
            names = [n.module.split(".")[0]] if n.module else [a.name for a in n.names]
            for m in names:
                if (PKG / f"{m}.py").exists() and m not in seen:
                    seen.add(m)
                    todo.append(m)
    return seen


def test_the_prompt_paths_closure_is_its_own():
    got = closure("prompt_path")
    assert got <= ALLOWED, f"the prompt path now reaches {sorted(got - ALLOWED)}"
    assert not got & NEVER


def test_the_hook_imports_only_its_path(tmp_path):
    """`python -m onescript prompt`, as `scripts/prompt-hook` runs it, with no
    check-in: it answers, and no module outside its path was imported."""
    probe = (
        "import runpy, sys, json, io\n"
        "sys.argv = ['onescript', '--box', sys.argv[1], '--keys', sys.argv[2], 'prompt']\n"
        "sys.stdin = io.StringIO(json.dumps({'prompt': 'the record lock'}))\n"
        "try:\n"
        "    runpy.run_module('onescript', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('onescript.'))),"
        " file=sys.stderr)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ONE)}
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(tmp_path / "box"),
            str(tmp_path / "k" / "keys.json"),
        ],
        cwd=ONE,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    loaded = {m.split(".")[1] for m in json.loads(p.stderr.strip().splitlines()[-1])}
    assert "hookSpecificOutput" in p.stdout, p.stdout + p.stderr
    assert loaded <= ALLOWED | {"__main__"}, sorted(loaded - ALLOWED)
    assert not loaded & NEVER


def test_any_other_verb_still_loads_the_whole_one_script():
    from onescript import __main__ as cli

    assert cli.api.prompt is cli.prompt_path.prompt
    assert cli._prompt_only(["--box", "b", "prompt", "--max-chars", "9"])
    assert not cli._prompt_only(["checkin"])
    assert not cli._prompt_only(["lookup", "prompt"])


def test_the_stamp_is_the_gates_own():
    """One token, two names: what the prompt path writes with is what the gate
    mints with, and the record refuses a stamp made with any other."""
    import pytest

    from onescript import gate, stamp
    from onescript.record import Verified

    assert gate.token() is stamp.token() and gate._TOKEN is stamp._TOKEN
    assert gate.system()._token is stamp.system()._token is stamp.token()
    rec_kw = {"box": None, "clock": lambda: "t", "version": "v", "token": stamp.token()}
    from onescript.record import Record

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        rec = Record(Path(d) / "box", rec_kw["clock"], "v", stamp.token())
        rec.append("note", gate.system())
        with pytest.raises(Exception):
            rec.append("note", Verified("run", "python", object()))
