"""lore — a sealed `world/*.md` proposal becomes `lore` rows, one fact each.

Cites: SPEC.md ruling R11; SPEC-rival.md section 5 (T14) and phase R4-lore-rows;
SPEC-documented.md phase J5-lore-payload. Rival finding 8: `gate.check_claims`
checks counts, a green sha and quotes only, so it can never strike a lore fact.
What strikes is `serve.check_cites` (a cite the file does not hold) and
`seat_say.check_nothing_lacking` (a word no served box holds). Both read rows, so
lore is rows, not a blob field, and a served document over `max_chars` is
`empty`, never truncated (ratatosk/onescript.py:37-40, serve._serve).

A lore row:   kind "lore", who = the proposal's author, where = the scene,
              fact = one line of the sealed document, proposal = its subject.

Served only when ALL hold (`servable`), checked from the record on every serve:
  * the row is not the run's (`who != "run"`: a `write` of world/*.md is the run's
    row about its own step, and its text is never lore);
  * `proposal` is a subject whose human seal proof re-checks (`held`);
  * a stored proposal row hashes to that subject (path, data, cites, claim), is a
    pass, and lies under `world/` as `.md`;
  * the fact is a line of that proposal's own data, and `where` is not blank.
Edit the data after sealing and the hash no longer matches: nothing is served.
"""

from __future__ import annotations

import re

from . import gate, proposals

KIND = "lore"
ROOT = "world/"
MAX_FACT = 400  # a fact is a line a person can read; a longer one is not a fact

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^(```|~~~)")


def is_world(path: object) -> bool:
    return (
        isinstance(path, str)
        and path.startswith(ROOT)
        and path.endswith(".md")
        and len(path) > len(ROOT) + 3
    )


def facts_of(path: str, data: str) -> list[tuple[str, str]]:
    """[(scene, fact)] for a world document: one fact per non-blank line.
    Frontmatter, headings, code fences and horizontal rules are not facts. The
    scene is the nearest heading above the fact, else the file's stem."""
    stem = path.rsplit("/", 1)[-1][: -len(".md")]
    lines = data.splitlines()
    if lines and lines[0].strip() == "---":  # frontmatter: not world text
        try:
            end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
            lines = lines[end + 1 :]
        except StopIteration:
            lines = []
    out, scene, fenced = [], stem, False
    for ln in lines:
        s = ln.strip()
        if _FENCE.match(s):
            fenced = not fenced
            continue
        if fenced or not s or re.fullmatch(r"[-*_=]{3,}", s):
            continue
        h = _HEADING.match(s)
        if h:
            scene = h.group(1).strip() or stem
            continue
        fact = _BULLET.sub("", s).strip()
        if fact and len(fact) <= MAX_FACT:
            out.append((scene, fact))
    return out


def _proposal_for(rows: list[dict], subject: str) -> dict | None:
    """The stored proposal row that really hashes to `subject`, or None."""
    for r in reversed(rows):
        if r.get("kind") != "proposal" or r.get("subject") != subject:
            continue
        if not all(k in r for k in proposals.KEYS):
            continue
        try:
            same = proposals.subject({k: r[k] for k in proposals.KEYS}) == subject
        except (TypeError, ValueError):
            continue
        if same and r.get("verdict") == "pass":
            return r
    return None


def servable(row: dict, rows: list[dict], held: set[str]) -> bool:
    """Whether `row` may be served. A row that is not lore always may (this
    gate adds a condition for lore, it removes none)."""
    if row.get("kind") != KIND:
        return True
    subject, fact, scene = row.get("proposal"), row.get("fact"), row.get("where")
    if row.get("who") == "run" or not isinstance(subject, str):
        return False
    if not isinstance(fact, str) or not fact or not isinstance(scene, str):
        return False
    if not scene.strip() or subject not in held:
        return False  # the human's proof over this proposal, re-checked
    prop = _proposal_for(rows, subject)  # the hash, re-checked
    if prop is None or not is_world(prop.get("path")):
        return False
    return (scene, fact) in facts_of(prop["path"], prop["data"])


def from_sealed(run, subject: str) -> list[dict]:
    """Append the `lore` rows of a sealed `world/*.md` proposal. Returns the new
    rows; an empty list when there is nothing to add; a single `refused` row
    when the proposal is not one (unsealed, tampered, not under world/, no
    author key). Idempotent: a fact already a row of this proposal is skipped.
    Never writes a seal and never writes the file (`Run.write_proposal` does)."""
    prop, why = run.find_proposal(subject)
    if why is None:
        held, unchecked = run.held()
        if subject not in held:
            why = unchecked.get(subject) or "no human seal over this proposal"
        elif not is_world(prop["path"]):
            why = f"'{prop['path']}' is not a world/*.md document"
        elif run.keys.get(prop["who"]) is None:
            why = f"no key for the proposal's author '{prop['who']}'"
    if why is not None:
        return [
            run.rec.append(
                "refused",
                run.sys,
                at="lore",
                reason=f"{why}; no lore written",
                subject=subject,
            )
        ]
    fam = run.rec.rows()
    author = gate.verify(
        {
            "who": prop["who"],
            "family": prop.get("family", ""),
            "sig": gate.sign(
                run.keys[prop["who"]], prop["who"], prop.get("family", "")
            ),
        },
        run.keys,
    )
    have = {
        (r.get("proposal"), r.get("where"), r.get("fact"))
        for r in fam
        if r.get("kind") == KIND
    }
    out = []
    for scene, fact in facts_of(prop["path"], prop["data"]):
        if (subject, scene, fact) in have:
            continue
        have.add((subject, scene, fact))
        out.append(
            run.rec.append(KIND, author, where=scene, fact=fact, proposal=subject)
        )
    return out
