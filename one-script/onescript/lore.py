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
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})")
_COMMENT = re.compile(r"<!--.*?-->")
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_QUOTE = re.compile(r"^(>\s?)+")


def _indent(ln: str) -> int:
    return len(ln.expandtabs(4)) - len(ln.expandtabs(4).lstrip(" "))


def _clean(s: str) -> str:
    """Inline HTML comments and tags are markup, not world text."""
    return re.sub(r" {2,}", " ", _TAG.sub("", _COMMENT.sub("", s))).strip(" \t")


def facts_of(path: str, data: str) -> list[tuple[str, str]]:
    """[(scene, fact)] for a world document. A fact is a paragraph or one list
    item: continuation lines are joined with a space, and nested list items stay
    attached to their parent, joined with "; " (a parent is never a fact on its
    own once it has children). Not facts: frontmatter, headings (they set the
    scene; so does a setext title), code fences (closed only by their own
    marker and length), indented code, HTML comments, table rows, rules.
    Blockquote `>` markers and inline HTML are stripped. The scene is the nearest
    heading above the fact, else the file's stem."""
    stem = path.rsplit("/", 1)[-1][: -len(".md")]
    lines = [ln.rstrip("\r") for ln in data.split("\n")]
    if lines and lines[0].strip(" \t") == "---":  # frontmatter: not world text
        end = next(
            (i for i, x in enumerate(lines[1:], 1) if x.strip(" \t") == "---"), None
        )
        lines = lines[end + 1 :] if end is not None else []
    out: list[tuple[str, str]] = []
    st = {"scene": stem, "cur": "", "item": False, "ind": 0, "blank": False}
    fence: str | None = None  # the opener being waited out
    comment = False

    def flush() -> None:
        if st["cur"] and len(st["cur"]) <= MAX_FACT:
            out.append((st["scene"], st["cur"]))
        st["cur"], st["item"], st["blank"] = "", False, False

    for raw in lines:
        ln = (
            _QUOTE.sub("", raw.lstrip(" \t"))
            if raw.lstrip(" \t").startswith(">")
            else raw
        )
        s = ln.strip(" \t")
        if comment:
            comment = "-->" not in s
            continue
        if fence is not None:
            m = _FENCE_OPEN.match(s)
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and s == m.group(1)
            ):
                fence = None
            continue
        if not s:
            st["blank"] = bool(st["cur"])  # a child list item may still follow
            continue
        bullet = bool(_BULLET.match(ln.lstrip(" \t"))) and (
            _indent(ln) < 4 or bool(st["cur"] and st["item"])
        )
        child = bool(st["cur"] and st["item"] and bullet and _indent(ln) > st["ind"])
        if st["blank"] and not child:
            flush()
        st["blank"] = False
        m = _FENCE_OPEN.match(s)
        if m:
            flush()
            fence = m.group(1)
            continue
        if s.startswith("<!--"):
            flush()
            comment = "-->" not in s
            continue
        if _SETEXT.match(s) and st["cur"] and not st["item"]:
            st["scene"], st["cur"] = st["cur"], ""  # "Title" over "=====": a heading
            continue
        h = _HEADING.match(s)
        if h:
            flush()
            st["scene"] = _clean(h.group(1).strip()) or stem
            continue
        if _RULE.match(s) or _SETEXT.match(s) or _TABLE.match(s):
            flush()
            continue
        if not st["cur"] and _indent(ln) >= 4:
            continue  # indented code; inside a paragraph or item it is a continuation
        if bullet:
            text = _clean(_BULLET.sub("", s, count=1))
            if child:
                st["cur"] += "; " + text
            else:
                flush()
                st["cur"], st["item"], st["ind"] = text, True, _indent(ln)
        else:
            if not st["cur"]:
                st["item"] = False
            text = _clean(s)
            if text:
                st["cur"] = (st["cur"] + " " + text) if st["cur"] else text
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


def unchecked_seals(rows: list[dict], ctx: Index, held, unchecked) -> dict[str, str]:
    """{proposal subject: why} for lore rows whose proposal's seal could not be
    checked here, so serve can mark their table `unreachable` instead of dropping
    them silently. Only a proposal whose stored row could yield lore at all
    (`author_problem` is None, same author) counts: a forged row over a proposal
    that never could be lore must not be a lever to mark a table unreachable."""
    out = {}
    for r in rows:
        s = r.get("proposal")
        prop = ctx.prop.get(s) if isinstance(s, str) else None
        if (
            r.get("kind") == KIND
            and r.get("who") != "run"
            and prop is not None
            and author_problem(prop) is None
            and r.get("who") == prop.get("who")
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
