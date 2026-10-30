"""idea_entry.py — an add-on idea issue, as the list entry it would become.

Willow's **An add-on idea** form (``.github/ISSUE_TEMPLATE/idea.yml``) asks
for the fields one entry in ``adapters/planned.json`` holds. The list, not
the issue, is where an idea lives, so the bot answers a new idea issue with
that entry written out, ready to paste into a pull request. It writes the
entry; it never opens the pull request, edits the list or closes the issue.

On only where the operator profile names the repo:

    "idea_entries": {"repos": ["your-org/your-repo"]}

``entry(body)`` reads the form's markdown back into the entry, or ``None``
when a required field is missing or the name isn't one the list takes.
``comment(entry)`` is what is posted. Nothing here calls GitHub.
"""

from __future__ import annotations

import json
import re

MARKER = "<!-- willow-bot:idea-entry -->"
LABEL = "idea"

#: the form's field labels, as GitHub writes them as ``### `` headings
NAME = "Name"
LINE = "What it does, in one sentence"
SECTION = "Section"
WRITES = "What it writes"
NETWORK = "Where it goes online"
NEEDS = "What it needs installed, with its licence"
TAGS = "Tags"

_NO_RESPONSE = "_No response_"
_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_WRITES = ("nothing", "record", "to")


def fields(body: str) -> dict[str, str]:
    """The form's answers by heading. An empty answer is ``""``."""
    out: dict[str, str] = {}
    heading = None
    lines: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("### "):
            if heading is not None:
                out[heading] = "\n".join(lines).strip()
            heading, lines = line[4:].strip(), []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        out[heading] = "\n".join(lines).strip()
    return {k: ("" if v == _NO_RESPONSE else v) for k, v in out.items()}


def entry(body: str) -> dict[str, str] | None:
    """The ``planned.json`` entry the form describes, keys in the order
    CONTRIBUTING.md writes them, or ``None`` when it can't be one."""
    f = fields(body)
    name, line, section = f.get(NAME, ""), f.get(LINE, ""), f.get(SECTION, "")
    writes = f.get(WRITES, "").split(" ", 1)[0]
    if not (_NAME_RE.fullmatch(name) and line and section and writes in _WRITES):
        return None
    needs = f.get(NEEDS, "")
    out = {"name": name, "kind": "deps" if needs else "stdlib", "section": section, "line": line}
    if needs:
        out["needs"] = needs
    out.update(source="new", writes=writes, network=f.get(NETWORK, "") or "none")
    if f.get(TAGS):
        out["tags"] = f[TAGS]
    return out


def comment(item: dict[str, str]) -> str:
    """The one comment a new idea issue gets."""
    block = json.dumps(item, indent=1, ensure_ascii=False)
    return (
        f"{MARKER}\n"
        f"This idea as one entry for `adapters/planned.json`:\n\n"
        f"```json\n{block}\n```\n\n"
        "To put it on the list, add it in a pull request, in name order. "
        "To build it, open a draft pull request that names it. "
        "CONTRIBUTING.md says how, and CI's run shows what's already close to it.\n"
    )
