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
  * the fact is a fact of that proposal's own data, and `where` is not blank;
  * `who` is the proposal's author, and the proposal is not an own idea
    (`cites: []`; SPEC-documented J5: an own-idea proposal never yields lore).
Edit the data after sealing and the hash no longer matches: nothing is served,
and `serve.serve` says why (a broken chain, or a seal it cannot check).

A fact is a LOGICAL unit: one paragraph, or one list item with its continuation
lines joined by a space. A wrapped sentence is never cut into fragments (a cut
at "never" / "takes salt" would invert the meaning). Lines split at "\n" only,
the rule of `record.py:_read`: U+2028 and \x0c stay inside the fact.
"""

from __future__ import annotations

import re

from . import gate, proposals

KIND = "lore"
ROOT = "world/"
MAX_FACT = 1000  # a fact a person can read on a card; a longer one is not served

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


_SETEXT = re.compile(r"^(=+|-{2,})\s*$")
_RULE = re.compile(r"^([-*_])(\s*\1){2,}\s*$")
_TABLE = re.compile(r"^\|")


def facts_of(path: str, data: str) -> list[tuple[str, str]]:
    """[(scene, fact)] for a world document. A fact is a paragraph or one list
    item (continuation lines joined with a space). Not facts: frontmatter,
    headings (they set the scene; so does a setext title), code fences, indented
    code, HTML comments, table rows, rules. The scene is the nearest heading
    above the fact, else the file's stem."""
    stem = path.rsplit("/", 1)[-1][: -len(".md")]
    lines = [ln.rstrip("\r") for ln in data.split("\n")]
    if lines and lines[0].strip(" \t") == "---":  # frontmatter: not world text
        end = next(
            (i for i, x in enumerate(lines[1:], 1) if x.strip(" \t") == "---"), None
        )
        lines = lines[end + 1 :] if end is not None else []
    out: list[tuple[str, str]] = []
    scene, buf, fenced, comment, item = stem, [], False, False, False

    def flush() -> None:
        if buf:
            fact = " ".join(buf)
            if len(fact) <= MAX_FACT:
                out.append((scene, fact))
            buf.clear()

    for ln in lines:
        s = ln.strip(" \t")
        if comment:
            comment = "-->" not in s
            continue
        if _FENCE.match(s):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if s.startswith("<!--"):
            flush()
            comment = "-->" not in s
            continue
        if not s:
            flush()
            continue
        if _SETEXT.match(s) and len(buf) == 1 and not item:
            scene, _ = buf[0], buf.clear()  # "Title" over "=====": a heading
            continue
        h = _HEADING.match(s)
        if h:
            flush()
            scene = h.group(1).strip() or stem
            continue
        if _RULE.match(s) or _SETEXT.match(s) or _TABLE.match(s):
            flush()
            continue
        if not buf and (ln.startswith("    ") or ln.startswith("\t")):
            continue  # indented code; inside a paragraph or item it is a continuation
        if _BULLET.match(ln.lstrip(" \t")) and len(ln) - len(ln.lstrip(" \t")) < 4:
            flush()
            s = _BULLET.sub("", s, count=1).strip(" \t")
            item = True
        elif not buf:
            item = False
        if s:
            buf.append(s)
    flush()
    return out


class Index:
    """What `servable` needs, worked out once per serve (not once per row): the
    latest proposal row that really hashes to each subject, and its facts."""

    def __init__(self, rows: list[dict]):
        self.prop: dict[str, dict] = {}
        for r in rows:  # later rows win, as in `Run.find_proposal`
            if r.get("kind") != "proposal" or not isinstance(r.get("subject"), str):
                continue
            if all(k in r for k in proposals.KEYS) and _hashes_to(r, r["subject"]):
                self.prop[r["subject"]] = r
        self._facts: dict[str, set] = {}

    def facts(self, subject: str) -> set[tuple[str, str]]:
        if subject not in self._facts:
            p = self.prop[subject]
            self._facts[subject] = set(facts_of(p["path"], p["data"]))
        return self._facts[subject]


def _hashes_to(row: dict, subject: str) -> bool:
    try:
        return proposals.subject({k: row[k] for k in proposals.KEYS}) == subject
    except (TypeError, ValueError):
        return False


def author_problem(prop: dict) -> str | None:
    """Why a stored proposal row can never yield lore, or None."""
    if prop.get("verdict") != "pass":
        return f"the proposal is {prop.get('verdict')}, not a pass"
    if not is_world(prop.get("path")):
        return f"'{prop.get('path')}' is not a world/*.md document"
    if not prop.get("cites") or prop.get("own_idea"):
        return "an own-idea proposal (no cites) is not lore"
    return None


def servable(row: dict, ctx: Index | list[dict], held: set[str]) -> bool:
    """Whether `row` may be served. A row that is not lore always may (this
    gate adds a condition for lore, it removes none). `ctx` is an `Index`, or
    the rows to make one from."""
    if row.get("kind") != KIND:
        return True
    if not isinstance(ctx, Index):
        ctx = Index(ctx)
    subject, fact, scene = row.get("proposal"), row.get("fact"), row.get("where")
    if row.get("who") == "run" or not isinstance(subject, str):
        return False
    if not isinstance(fact, str) or not fact or not isinstance(scene, str):
        return False
    if not scene.strip() or subject not in held:
        return False  # the human's proof over this proposal, re-checked
    prop = ctx.prop.get(subject)  # the hash, re-checked
    if prop is None or author_problem(prop) or row.get("who") != prop.get("who"):
        return False
    return (scene, fact) in ctx.facts(subject)


def unchecked_seals(rows: list[dict], held, unchecked) -> dict[str, str]:
    """{proposal subject: why} for lore rows whose proposal's seal could not be
    checked here, so serve can say `unreachable` instead of dropping them."""
    out = {}
    for r in rows:
        s = r.get("proposal")
        if (
            r.get("kind") == KIND
            and r.get("who") != "run"
            and isinstance(s, str)
            and s not in held
            and s in unchecked
        ):
            out[s] = unchecked[s]
    return out


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
        elif (bad := author_problem(prop)) is not None:
            why = bad
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
