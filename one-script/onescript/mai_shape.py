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

A document is `@markdownai` (mai_lint's `_is_mai`) when the first non-blank line
of its body, after an optional frontmatter block, is `@markdownai vN`. Anything
else is plain markdown and passes untouched: prose, indented or fenced mentions
of the word, a header-less `@markdownai`. One stricter rule than mai_lint: a
column-0 `@markdownai vN` line outside a fence anywhere but the first body line
(or inside frontmatter) is refused as a misplaced header, because a doc that
carries one meant to be mai. A leading BOM is stripped first, `.MD` counts, and
CR / CRLF line ends are read as line ends.

Frontmatter: mai_lint parses the YAML and checks a JSON schema. This stdlib
shape check refuses an unclosed `---` block (when a header follows) and a line
that is neither `key: value`, an indented / list continuation, nor a comment.
THE REMAINING GAP against yaml: quoting, nesting and duplicate-key errors, and
the schema (`kind`, `name`, ... types), are not checked here.
"""

from __future__ import annotations

import re

HEADER_RE = re.compile(r"^@markdownai\s+v\d+(\.\d+)*\s*$")
# the parser's own constraint regex (mai_lint.CONSTRAINT_RE), reproduced
CONSTRAINT_RE = re.compile(
    r"@constraint(?:\s+severity=[\"']?(\w+)[\"']?)?\s+(.*?)(?=@constraint|$)", re.DOTALL
)
_FENCE = re.compile(r"^\s*(```|~~~)")
_KEY = re.compile(r"^[A-Za-z_][\w.-]*\s*:(\s.*)?$")


def _norm(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _split(text: str) -> tuple[list[str] | None, str, bool]:
    """(frontmatter lines, body, unclosed). Frontmatter is a `---` first line to
    the next `---` line; with no closing line the whole text is the body."""
    lines = text.split("\n")
    if lines and lines[0].rstrip() == "---":
        for i, ln in enumerate(lines[1:], 1):
            if ln.rstrip() == "---":
                return lines[1:i], "\n".join(lines[i + 1 :]), False
        return None, text, True
    return None, text, False


def _unfenced(text: str):
    fenced = False
    for ln in text.split("\n"):
        if _FENCE.match(ln):
            fenced = not fenced
            continue
        if not fenced:
            yield ln


def _first(body: str) -> str | None:
    return next((ln.strip() for ln in body.splitlines() if ln.strip()), None)


def is_mai(text: str) -> bool:
    """A document whose first body line is `@markdownai vN` (mai_lint's rule)."""
    text = _norm(text)
    fm, body, _ = _split(text)
    first = _first(body)
    return bool(first and HEADER_RE.match(first))


def _misplaced(text: str) -> bool:
    """A column-0 `@markdownai vN` outside a fence that is not the first body
    line: the doc meant to be mai and the header is in the wrong place."""
    fm, body, unclosed = _split(text)
    if fm is not None and any(HEADER_RE.match(ln) for ln in fm):
        return True
    first = _first(body)
    seen = False
    for ln in _unfenced(body):
        if HEADER_RE.match(ln):
            if ln.strip() == first and not seen and not unclosed:
                seen = True
                continue
            return True
    return False


def problems(text: str) -> list[str]:
    """Why `text` is not a well-formed `@markdownai` document, each a sentence.
    Empty when it is clean, and for a plain markdown document."""
    text = _norm(text)
    fm, body, unclosed = _split(text)
    mai = is_mai(text)
    if not mai:
        if unclosed and any(HEADER_RE.match(ln) for ln in _unfenced(text)):
            return ["frontmatter is not closed: no second `---` line"]
        if _misplaced(text):
            return [
                "first body line is not a @markdownai header: "
                f"{_first(body)!r} (the header is placed later or in frontmatter)"
            ]
        return []
    fails: list[str] = []
    if _misplaced(text):
        fails.append("a second @markdownai header appears after the first body line")
    fm_raw = "\n".join(fm or [])
    for n, ln in enumerate(fm or [], 2):
        t = ln.strip()
        if (
            t
            and not t.startswith("#")
            and not (_KEY.match(ln) or ln[:1] in " \t" or t.startswith("- "))
        ):
            fails.append(f"frontmatter line {n} is not `key: value`: {t!r}")
    for hidden in ("@phase", "@macro"):
        if fm_raw and re.search(rf"^{hidden}\b", fm_raw, re.MULTILINE):
            fails.append(
                f"{hidden} appears inside frontmatter (invisible to the renderer, "
                "but the phase/macro tools parse raw; #157)"
            )
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
    if not rel.lower().endswith(".md"):
        return None
    text = data.decode("utf-8", errors="replace")
    bad = problems(text)
    if bad:
        raise PermissionError(
            f"record: '{rel}' is a malformed @markdownai document; nothing "
            "written: " + "; ".join(bad)
        )
    return "clean" if is_mai(text) else None
