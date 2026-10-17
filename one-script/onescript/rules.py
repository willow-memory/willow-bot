"""rules — a sealed `rules/*.md` `@markdownai` proposal becomes `rules` rows, one phase each.

Cites: SPEC.md ruling R15; SPEC-tools.md phase T-mai-2 (a rules lookup by exact
`@phase` name as a D0 `verbatim` answer). Built beside `lore.py` and on the same
sealing: a document is rules only through a proposal the human sealed (the
seal's proof re-checked, the stored bytes re-hashed to the subject), and only
then does a `rules` row serve. An unsealed document, a tampered one, an
own-idea one (`cites: []`) and a header-less one never answers.

A rules row:  kind "rules", who = the proposal's author, where = the phase name
              exactly as written, body = the phase's text (`mai_shape.phases_of`:
              the span to the next `@phase`, outer whitespace stripped, otherwise
              byte for byte), sha = sha256 of the sealed document's bytes,
              proposal = its subject.

Served only when ALL hold (`servable`), checked from the record on every serve:
  * the row is not the run's (`who != "run"`);
  * `proposal` is a subject whose human seal proof re-checks (`held`);
  * a stored proposal row hashes to that subject, is a pass, lies under
    `rules/` as `.md`, starts with `@markdownai`, and has cites;
  * `(where, body)` is a phase of that proposal's own data and `sha` is the
    hash of that data;
  * the body carries no 64-hex run: the served view withholds those, and a
    verbatim answer is never served altered.
Edit the data after sealing and the hash no longer matches: nothing is served.

`served.json` keeps `where` and `body` as written (no NFKC, no zero-width
strip, which `serve.view` does only for lore): a lookup by exact name needs the
name exact. Two phases of one document with the same name are not served at all
(the lookup would have to pick one).
"""

from __future__ import annotations

import re

from . import gate, lore, mai_shape
from .record import h256

KIND = "rules"
ROOT = "rules/"
MAX_BODY = 20000  # a phase a person can read on a card; a longer one is not served
_HEX64 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)


def is_rules(path: object) -> bool:
    return (
        isinstance(path, str)
        and path.startswith(ROOT)
        and path.endswith(".md")
        and len(path) > len(ROOT) + 3
    )


def phases(data: str) -> list[tuple[str, str]]:
    """The phases of a document that a lookup may answer from: each name once
    (a repeated name is dropped from both), body non-empty, in size, with no
    full-width hash in it."""
    got = mai_shape.phases_of(data)
    names = [n for n, _ in got]
    return [
        (n, b)
        for n, b in got
        if names.count(n) == 1 and b and len(b) <= MAX_BODY and not _HEX64.search(b)
    ]


class Index(lore.Index):
    """`lore.Index` plus, once per serve, each proposal's phases."""

    def __init__(self, rows: list[dict]):
        super().__init__(rows)
        self._phases: dict[str, set] = {}

    def phases(self, subject: str) -> set[tuple[str, str]]:
        if subject not in self._phases:
            self._phases[subject] = set(phases(self.prop[subject]["data"]))
        return self._phases[subject]


def author_problem(prop: dict) -> str | None:
    """Why a stored proposal row can never yield rules, or None."""
    if prop.get("verdict") != "pass":
        return f"the proposal is {prop.get('verdict')}, not a pass"
    if not is_rules(prop.get("path")):
        return f"'{prop.get('path')}' is not a rules/*.md document"
    if not prop.get("cites") or prop.get("own_idea"):
        return "an own-idea proposal (no cites) is not rules"
    data = prop.get("data") or ""
    if not mai_shape.is_mai(data):
        return "the document is not an @markdownai document"
    if bad := mai_shape.problems(data):
        return "the @markdownai document is malformed: " + "; ".join(bad)
    return None


def servable(row: dict, ctx: Index | list[dict], held: set[str]) -> bool:
    """Whether `row` may be served. A row that is not a rules row always may."""
    if row.get("kind") != KIND:
        return True
    if not isinstance(ctx, Index):
        ctx = Index(ctx)
    subject, name, body = row.get("proposal"), row.get("where"), row.get("body")
    if row.get("who") == "run" or not isinstance(subject, str):
        return False
    if not isinstance(name, str) or not name or not isinstance(body, str) or not body:
        return False
    if subject not in held:
        return False  # the human's proof over this proposal, re-checked
    prop = ctx.prop.get(subject)  # the hash, re-checked
    if prop is None or author_problem(prop) or row.get("who") != prop.get("who"):
        return False
    if row.get("sha") != h256(prop["data"]):
        return False
    return (name, body) in ctx.phases(subject)


def unchecked_seals(rows: list[dict], ctx: Index, held, unchecked) -> dict[str, str]:
    """{proposal subject: why} for rules rows whose proposal's seal could not be
    checked here, so serve can say `unreachable` instead of dropping them
    silently (lore's rule, `lore.unchecked_seals`)."""
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
    """Append the `rules` rows of a sealed `rules/*.md` proposal. Returns the new
    rows; an empty list when there is nothing to add; a single `refused` row
    when the proposal is not one (unsealed, tampered, not under rules/, not
    `@markdownai`, no author key). Idempotent. Never writes a seal and never
    writes the file (`Run.write_proposal` does)."""
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
                at="rules",
                reason=f"{why}; no rules written",
                subject=subject,
            )
        ]
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
        (r.get("proposal"), r.get("where"), r.get("body"))
        for r in run.rec.rows()
        if r.get("kind") == KIND
    }
    sha = h256(prop["data"])
    out = []
    for name, body in phases(prop["data"]):
        if (subject, name, body) in have:
            continue
        have.add((subject, name, body))
        out.append(
            run.rec.append(
                KIND, author, where=name, body=body, sha=sha, proposal=subject
            )
        )
    return out
