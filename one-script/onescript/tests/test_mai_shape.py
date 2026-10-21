"""T-mai-1 (R15): a malformed `@markdownai` doc is refused before it is kept.

SPEC-tools.md phase T-mai-1; vendored from willow-mcp tools/mai_lint.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, mai_shape  # noqa: E402
from onescript.record import Record  # noqa: E402

CLEAN = (
    "---\nkind: doc\nname: x\n---\n\n@markdownai v1.0\n\n# T\n\n"
    "@phase one\nbody\n\n@constraint severity=error\nRule one.\n"
)
BAD = {
    "header": "# Title first\n\n@markdownai v1.0\n\nbody\n",
    "colon": "@markdownai v1.0\n\n@constraint: no colon\n",
    "constraint-swallow": "@markdownai v1.0\n\n@constraint severity=error\nRule\n## Next\nx\n",
    "phase-swallow": "@markdownai v1.0\n\n@phase a\n## One\nx\n## Two\ny\n",
    "frontmatter": "---\nname: x\n@phase hidden\n---\n\n@markdownai v1.0\n\nb\n",
    "unbalanced-if": "@markdownai v1.0\n\n@if a\nx\n",
    "unbalanced-macro": "@markdownai v1.0\n\n@macro m\nx\n",
}
MAI_LINT = Path("/home/user/sandbox/repos/willow-mcp/tools/mai_lint.py")


def box(tmp_path):
    n = iter(range(10**6))
    return Record(tmp_path, lambda: f"t{next(n)}", "v", gate.token())


def test_clean_mai_doc_passes_and_is_stamped(tmp_path):
    rec = box(tmp_path)
    row = rec.write_file(gate.system(), "d.md", CLEAN.encode(), check=mai_shape.check)
    assert row["kind"] == "write" and row["lint"] == "clean"
    assert (tmp_path / "d.md").read_text() == CLEAN


@pytest.mark.parametrize("name", sorted(BAD))
def test_each_lint_class_is_refused_with_a_reason(tmp_path, name):
    rec = box(tmp_path)
    with pytest.raises(PermissionError) as e:
        rec.write_file(gate.system(), "d.md", BAD[name].encode(), check=mai_shape.check)
    assert "malformed @markdownai" in str(e.value)
    assert not (tmp_path / "d.md").exists()
    assert not [r for r in rec.rows() if r["kind"] == "write"]
    assert not rec.pile()["items"]


def test_reasons_name_the_defect():
    want = {
        "header": "not a @markdownai header",
        "colon": "colon form",
        "constraint-swallow": "last @constraint swallows",
        "phase-swallow": "last @phase swallows",
        "frontmatter": "inside frontmatter",
        "unbalanced-if": "unbalanced @if",
        "unbalanced-macro": "unbalanced @macro",
    }
    for name, text in BAD.items():
        assert any(want[name] in p for p in mai_shape.problems(text)), name


def test_plain_markdown_and_other_files_pass_untouched(tmp_path):
    rec = box(tmp_path)
    plain = "# Notes\n\n@constraint: this is just prose in plain markdown\n@if x\n"
    row = rec.write_file(gate.system(), "n.md", plain.encode(), check=mai_shape.check)
    assert "lint" not in row
    fenced = "# About\n\n```\n@markdownai v1.0\n@constraint: x\n```\n"
    assert "lint" not in rec.write_file(
        gate.system(), "f.md", fenced.encode(), check=mai_shape.check
    )
    assert "lint" not in rec.write_file(
        gate.system(), "x.txt", BAD["colon"].encode(), check=mai_shape.check
    )


def test_a_refused_mai_doc_is_a_refused_row_through_write_proposal(tmp_path):
    from onescript.run import Run

    keys = {"willow": b"k", gate.HUMAN: b"h"}
    run = Run(tmp_path / "box", keys, {"trace_ids": [], "grants": []}, lambda: "t")
    from onescript import proposals

    p = {"path": "notes/d.md", "data": BAD["colon"], "cites": ["x"], "claim": "c"}
    subject = proposals.subject(p)
    who = gate.verify(
        {
            "who": "willow",
            "family": "q",
            "sig": gate.sign(keys["willow"], "willow", "q"),
        },
        keys,
    )
    run.rec.append(
        "proposal", who, where=p["path"], verdict="pass", subject=subject, **p
    )
    proof = gate.sign(keys[gate.HUMAN], "seal", subject)
    hum = gate.human(subject, proof, keys[gate.HUMAN])
    run.rec.append("seal", hum, where=subject, subject=subject, proof=proof)
    out = run.write_proposal(subject)
    assert out["kind"] == "refused" and "colon form" in out["reason"]
    assert not (tmp_path / "box" / "notes" / "d.md").exists()


def test_crlf_does_not_slip_past():
    assert mai_shape.problems(BAD["colon"].replace("\n", "\r\n"))


# ── drift: the vendored checks against willow-mcp's, when it is in reach ────
def _mai_lint():
    if not MAI_LINT.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("mai_lint_ref", MAI_LINT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # exits 2 when pyyaml/jsonschema are missing
        return mod
    except (SystemExit, Exception):
        return None


def test_drift_same_verdicts_as_mai_lint(tmp_path, capsys):
    ref = _mai_lint()
    if ref is None:
        pytest.skip("willow-mcp tools/mai_lint.py is not importable here")
    schema = ref._load_schema()
    docs = {"clean": CLEAN, **BAD}
    for name, text in docs.items():
        f = tmp_path / f"{name}.md"
        f.write_text(text)
        theirs = ref.lint_file(f, schema, quiet=True)
        ours = not mai_shape.problems(text)
        if name == "frontmatter":  # theirs also fails it on the YAML schema; both fail
            assert not theirs and not ours
        else:
            assert theirs == ours, (name, capsys.readouterr().out)


# ── revision round (Opus audit of 48b3408) ──────────────────────────────────
def test_S7_only_a_first_body_line_header_makes_a_doc_mai(tmp_path):
    rec = box(tmp_path)
    for i, text in enumerate(
        (
            "# Readme\n@markdownai is a format we use.\n@constraint: prose\n",
            "# Readme\nExample:\n\n    @markdownai v1.0\n    @constraint: x\n",
            "# Readme\n```\n@markdownai v1.0\n@constraint: x\n```\n",
            "@markdownai\n@constraint: header-less is mai_lint's warning, not mai\n",
        )
    ):
        assert "lint" not in rec.write_file(
            gate.system(), f"p{i}.md", text.encode(), check=mai_shape.check
        )


def test_S7_a_header_placed_late_or_in_frontmatter_is_refused(tmp_path):
    rec = box(tmp_path)
    for text in (
        "# T\n\n@markdownai v1.0\n",
        "---\nname: x\n@markdownai v1.0\n---\nbody\n",
        "@markdownai v1.0\n\n# T\n\n@markdownai v1.0\n",
    ):
        with pytest.raises(PermissionError):
            rec.write_file(gate.system(), "d.md", text.encode(), check=mai_shape.check)


def test_S8_a_bom_does_not_hide_a_defect(tmp_path):
    rec = box(tmp_path)
    with pytest.raises(PermissionError, match="colon form"):
        rec.write_file(
            gate.system(), "d.md", ("﻿" + BAD["colon"]).encode(), check=mai_shape.check
        )
    row = rec.write_file(
        gate.system(), "ok.md", ("﻿" + CLEAN).encode(), check=mai_shape.check
    )
    assert row["lint"] == "clean"


def test_S8_frontmatter_shape_is_checked_with_the_stdlib():
    fm = "---\nname: x\njust a sentence\n---\n@markdownai v1.0\n"
    assert any("not `key: value`" in p for p in mai_shape.problems(fm))
    unclosed = "---\nname: x\n\n@markdownai v1.0\n# T\n"
    assert any("not closed" in p for p in mai_shape.problems(unclosed))
    ok = "---\nname: x\ntags:\n  - a\n  - b\n# note\ndesc: |\n  long\n---\n@markdownai v1.0\n"
    assert mai_shape.problems(ok) == []


def test_N4_upper_case_suffix_and_lone_cr_are_checked(tmp_path):
    rec = box(tmp_path)
    with pytest.raises(PermissionError):
        rec.write_file(
            gate.system(), "d.MD", BAD["colon"].encode(), check=mai_shape.check
        )
    assert mai_shape.problems(BAD["colon"].replace("\n", "\r"))
    assert mai_shape.is_mai("\r@markdownai v1.0\r# T\r")


def test_NEW7_quoted_frontmatter_keys_are_keys():
    fm = "---\n\"name\": x\n'kind': doc\n---\n@markdownai v1.0\n"
    assert mai_shape.problems(fm) == []


def test_NEW8_a_fence_closes_only_on_its_own_marker():
    doc = "@markdownai v1.0\n# T\n```\n~~~\n@markdownai v1.0\n```\n@constraint: x\n"
    assert any("colon form" in p for p in mai_shape.problems(doc))
    ok = "# P\n````\n```\n@markdownai v1.0\n````\ntext\n"
    assert mai_shape.problems(ok) == []
    assert mai_shape.problems("# P\n~~~\n```\n@markdownai v1.0\n~~~\n") == []
