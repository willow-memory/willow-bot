"""The record is the kernel's first piece: its import closure is exactly
{record, canon, states, sign} (states is allowed, not
used today), and canon/sign import nothing else internal
(merge_check gate 9, dependency direction). R3 is held beside it: every
production `write_file` caller passes the shape check, and the seal check the
record uses is the gate's, byte for byte."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from onescript import gate, sign
from onescript.record import Record

PKG = Path(__file__).resolve().parent.parent
ALLOWED = {"record", "canon", "states", "sign"}


def closure(start: str, pkg: Path = PKG) -> set[str]:
    """Modules of `pkg` reachable from `start` by relative imports, anywhere in
    the file (lazy imports count)."""
    seen, todo = {start}, [start]
    while todo:
        tree = ast.parse((pkg / f"{todo.pop()}.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.ImportFrom) and n.level == 1):
                continue
            names = [n.module.split(".")[0]] if n.module else [a.name for a in n.names]
            for m in names:
                if (pkg / f"{m}.py").exists() and m not in seen:
                    seen.add(m)
                    todo.append(m)
    return seen


def test_the_record_closure_is_the_kernel():
    got = closure("record")
    assert got <= ALLOWED, f"record reaches beyond the kernel: {sorted(got - ALLOWED)}"
    assert {"record", "canon", "sign"} <= got


def test_a_planted_violation_is_seen(tmp_path):
    for m in ALLOWED:
        (tmp_path / f"{m}.py").write_text("")
    (tmp_path / "gate.py").write_text("")
    (tmp_path / "record.py").write_text("from . import canon\n")
    assert closure("record", tmp_path) == {"record", "canon"}
    (tmp_path / "sign.py").write_text("def f():\n    from .gate import x\n")
    (tmp_path / "record.py").write_text("from . import canon, sign\n")
    got = closure("record", tmp_path)
    assert got == {"record", "canon", "sign", "gate"}
    assert not got <= ALLOWED


def test_the_production_write_file_callers_pass_the_shape_check():
    bare = []
    for py in PKG.glob("*.py"):
        for n in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "write_file"
                and not any(k.arg == "check" for k in n.keywords)
            ):
                bare.append(f"{py.name}:{n.lineno}")
    assert bare == [], f"write_file without check=: {bare}"


def test_a_planted_bare_write_file_is_seen():
    src = "rec.write_file(w, 'a.md', b'x')\n"
    n = next(x for x in ast.walk(ast.parse(src)) if isinstance(x, ast.Call))
    assert not any(k.arg == "check" for k in n.keywords)


def test_the_seal_check_is_the_gates():
    key = b"k" * 32
    proof = gate.sign(key, "seal", "subj")
    for s, p in (
        ("subj", proof),
        ("subj", proof + "0"),
        ("x", proof),
        ("subj", "é" * 64),
    ):
        assert sign.verify_seal(s, p, key) is gate.verify_seal(s, p, key)
    assert sign.verify_seal("subj", proof, key) is True


def test_without_a_check_the_record_knows_no_document_shape(tmp_path):
    rec = Record(tmp_path, lambda: "t", "v", gate.token())
    bad = b"@markdownai\n: not a thing"
    assert "lint" not in rec.write_file(gate.system(), "d.md", bad)
    with pytest.raises(PermissionError):
        rec.write_file(gate.system(), "e.md", bad, check=_refuse)


def _refuse(rel, data):
    raise PermissionError("no")
