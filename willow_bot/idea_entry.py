"""idea_entry.py — an add-on idea issue, as the list entry it would become.

Willow's **An add-on idea** form (``.github/ISSUE_TEMPLATE/idea.yml``) asks
for the fields one entry in ``adapters/planned.json`` holds. Its **A language
for willow** form (``language.yml``) asks for a language pack's: the entry is
``words-`` and the language's code, in "Your own words", writing the record
and never going online, as every pack does. The list, not
the issue, is where an idea lives, so the bot answers a new idea issue with
that entry written out, ready to paste into a pull request. It writes the
entry; it never opens the pull request, edits the list or closes the issue.

On only where the operator profile names the repo:

    "idea_entries": {"repos": ["your-org/your-repo"]}

``entry(body)`` reads the idea form's markdown back into the entry, or
``None`` when a required field is missing or the name isn't one the list
takes; ``language_entry(body)`` does the same for the language form, and
``entry_for(labels, body)`` picks by the form's label.
``comment(entry)`` is what is posted. Nothing here calls GitHub.
"""

from __future__ import annotations

import json
import re

MARKER = "<!-- willow-bot:idea-entry -->"
LABEL = "idea"
LANGUAGE_LABEL = "language"

#: the form's field labels, as GitHub writes them as ``### `` headings
NAME = "Name"
LINE = "What it does, in one sentence"
SECTION = "Section"
WRITES = "What it writes"
NETWORK = "Where it goes online"
NEEDS = "What it needs installed, with its licence"
TAGS = "Tags"

#: the language form's
LANGUAGE = "Language"
CODE = "Its code"
WORDS = "Its common words"
SOURCE = "Where the list comes from, and its licence"

_NO_RESPONSE = "_No response_"
_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_WRITES = ("nothing", "record", "to")
_CODE_RE = re.compile(r"[a-z]{2,3}")
_TAG_RE = re.compile(r"[a-z]+(-[a-z]+)*")


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


def language_entry(body: str) -> dict[str, str] | None:
    """The ``planned.json`` entry for the language pack the form describes,
    shaped like the packs already built, or ``None`` when the code isn't two
    or three lowercase letters or the name, words or source is missing."""
    f = fields(body)
    code = f.get(CODE, "").strip().strip("`")
    name = re.split(r"[(/,]", f.get(LANGUAGE, ""), maxsplit=1)[0].strip()
    # ``render: text`` puts the answer in a fenced block; the fence isn't a word
    listed = "\n".join(ln for ln in f.get(WORDS, "").splitlines() if not ln.strip().startswith("```"))
    words = [w.strip().strip("\"'`") for w in re.split(r"[,\n]", listed)]
    words = [w for w in words if w]
    if not (_CODE_RE.fullmatch(code) and name and words and f.get(SOURCE, "").strip()):
        return None
    shown = ", ".join(f'"{w}"' for w in words[:3])
    tag = name.lower().replace(" ", "-")
    tags = ["no-install", "offline", "records", "words"] + ([tag] if _TAG_RE.fullmatch(tag) else [])
    return {
        "name": f"words-{code}",
        "kind": "stdlib",
        "section": "Your own words",
        "line": f"{name} common words ({shown}).",
        "source": "new",
        "writes": "record",
        "network": "none",
        "tags": ", ".join(tags),
    }


def entry_for(labels: set[str], body: str) -> dict[str, str] | None:
    """The entry for whichever form the issue's label says it came from."""
    if LANGUAGE_LABEL in labels:
        return language_entry(body)
    if LABEL in labels:
        return entry(body)
    return None


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
        + (
            "\nThe words, the quirks and where the list comes from stay on this issue "
            "for whoever builds the pack; the source goes on its card.\n"
            if item["name"].startswith("words-")
            else ""
        )
    )
