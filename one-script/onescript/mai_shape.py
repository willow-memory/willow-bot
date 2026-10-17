"""mai_shape — the shape of an `@markdownai` document, checked before it is kept.

Cites: SPEC.md ruling R15 and SPEC-tools.md phase T-mai-1. Reached from
`Record.write_file`; a failure is a `PermissionError`, which `Run.write_proposal`
already turns into a `refused` row, with no bytes written.

Vendored from willow-mcp `tools/mai_lint.py` (`lint_file`, willow-mcp master at
8215d1d), the way BRIEF section 4 vendors Brier math: onescript stays
stdlib-only and does not import willow-mcp. Only the TEXTUAL checks are carried.
Left out on purpose: the frontmatter YAML parse and the JSON-schema check (they
need pyyaml and jsonschema); `tests/test_mai_shape.py` pins every carried check
to mai_lint's verdict when mai_lint is importable, so a change there shows here.

Carried (each maps to a real parser sharp edge):
  header      the first non-blank body line is `@markdownai vN` (#156 context)
  colon       `@constraint:` is silently dropped by the parser (#156)
  swallow     the last `@constraint`, or the last `@phase`, captures to EOF and
              buries structural content that was meant to render (#156)
  frontmatter a `@phase` or `@macro` line inside the YAML frontmatter (#157)
  balance     unbalanced `@if`/`@endif` and `@macro`/`@endmacro`

A plain markdown document (no `@markdownai` line outside a code fence) is never
checked. A header-less version (`@markdownai`) is a warning in mai_lint, not a
failure, and passes here too.
"""

from __future__ import annotations

import re

FM_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
HEADER_RE = re.compile(r"^@markdownai\s+v\d+(\.\d+)*\s*$")
# the parser's own constraint regex (mai_lint.CONSTRAINT_RE), reproduced
CONSTRAINT_RE = re.compile(
    r"@constraint(?:\s+severity=[\"']?(\w+)[\"']?)?\s+(.*?)(?=@constraint|$)", re.DOTALL
)
_MAI_LINE = re.compile(r"^\s*@markdownai\b")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _unfenced(text: str):
    fenced = False
    for ln in text.splitlines():
        if _FENCE.match(ln):
            fenced = not fenced
            continue
        if not fenced:
            yield ln


def is_mai(text: str) -> bool:
    """A document that claims to be `@markdownai`: a line of it opens with the
    header word, outside a code fence. Where that line sits is `problems`' job."""
    return any(_MAI_LINE.match(ln) for ln in _unfenced(text))


def problems(text: str) -> list[str]:
    """Why `text` is not a well-formed `@markdownai` document, each a sentence.
    Empty when it is clean, and for a plain markdown document."""
    if not is_mai(text):
        return []
    fails: list[str] = []
    text = text.replace("\r\n", "\n")
    m = FM_RE.match(text)
    fm_raw, body = (m.group(1), m.group(2)) if m else ("", text)
    for hidden in ("@phase", "@macro"):
        if fm_raw and re.search(rf"^{hidden}\b", fm_raw, re.MULTILINE):
            fails.append(
                f"{hidden} appears inside frontmatter (invisible to the renderer, "
                "but the phase/macro tools parse raw; #157)"
            )
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), None)
    if not (first and (HEADER_RE.match(first) or first.startswith("@markdownai"))):
        fails.append(f"first body line is not a @markdownai header: {first!r}")
    n_colon = len(re.findall(r"@constraint:", body))
    if n_colon:
        fails.append(
            f"{n_colon}x `@constraint:` colon form is silently dropped by the "
            "parser (it needs whitespace after @constraint; #156)"
        )
    cons = list(CONSTRAINT_RE.finditer(body))
    if cons:
        trailing = [
            ln for ln in cons[-1].group(2).strip().splitlines()[1:] if ln.strip()
        ]
        structural = [
            ln
            for ln in trailing
            if ln.lstrip().startswith("@") or re.match(r"^#{1,6}\s", ln)
        ]
        if structural:
            fails.append(
                "last @constraint swallows structural content that will not "
                f"render: {structural[0].strip()!r} (#156)"
            )
    phases = list(re.finditer(r"^@phase\b.*$", body, re.M))
    if phases:
        tail = body[phases[-1].end() :]
        h2 = [ln for ln in tail.splitlines() if re.match(r"^##[ \t]", ln)]
        if len(h2) > 1:
            fails.append(
                f"last @phase swallows an appended section: {h2[1].strip()!r}; give "
                "it its own @phase or move it before the phases"
            )
    for open_tok, close_tok in (("@if", "@endif"), ("@macro", "@endmacro")):
        o = len(re.findall(rf"^{open_tok}\b", body, re.MULTILINE))
        c = len(re.findall(rf"^{close_tok}\b", body, re.MULTILINE))
        if o != c:
            fails.append(f"unbalanced {open_tok} ({o}) / {close_tok} ({c})")
    return fails


def check(rel: str, data: bytes) -> str | None:
    """For `Record.write_file`: `"clean"` for a well-formed `@markdownai` `.md`,
    None for anything else that is not checked (not `.md`, or plain markdown),
    and a `PermissionError` naming every problem for a malformed one."""
    if not rel.endswith(".md"):
        return None
    text = data.decode("utf-8", errors="replace")
    if not is_mai(text):
        return None
    bad = problems(text)
    if bad:
        raise PermissionError(
            f"record: '{rel}' is a malformed @markdownai document; nothing "
            "written: " + "; ".join(bad)
        )
    return "clean"
