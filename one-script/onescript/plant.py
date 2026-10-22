"""plant.py -- the seed's person-facing half, in the one script (IDEAS 121).

The operator, 2026-10-22: "pull the missing seed pieces into the one script."
Every seed before this (willow-seed's `seed.py`, willow-2.0's SEED9, the
charter's SEED10) was the one file a person runs first, and the one script
already held its mechanics: the person's own key, the refusals, the first
bite, the return run. What it lacked was the half a person meets. Three
pieces of it, each in the one script's own terms:

  plant    ``plant(cfg, allow)`` -- once per box, after its first check-in:
           the canon's six movements, read where the Grove reads them
           (``$WILLOW_HOME/seed/canon``, then willows-grove's
           ``governance/seed/canon``; D16: "the six-part canon *is* the human
           onboarding"), handed back to be read, and one ``plant`` row holding
           each movement's hash, never its words. No canon is `unreachable`,
           said, and nothing is planted: the Grove serves a stub there, the
           one script says so instead.
  consent  ``consent(cfg, allow)`` -- once per check-in, the way willow-seed's
           README asks ("May I access your journal today?") and willow-2.0's
           fylgja session module records: which repos this session may read,
           training consent off unless given, ``expires: session``. On a
           planted box serve and the prompt hook take no chunk from a repo
           the session didn't consent to (`allowed_repos`), and with no
           consent at all they take none.
  letter   ``letter(cfg, text)`` -- the handoff letter (willow-2.0's
           ``fylgja/handoff_write.py``, the charter's handoff letter): a
           markdown file in the box's ``letters/``, lettered by day so the
           newest sorts last, written through the record so its hash is on a
           row. The next check-in's NEEDS YOU names it (`latest_letter`).

Left where they are, said here so their absence is a choice: SEED9's card
(a dashboard card has no home in a box) and Nestor's seeded demo store (a
first screen that isn't empty is Nestor's own surface). Stdlib only; the
helpers the prompt hook reads (`planted`, `allowed_repos`) import nothing.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from pathlib import Path

from .plant_rule import (  # noqa: F401  (the rules, read here as before)
    CONSENT,
    LETTERS,
    PLANT,
    allowed_repos,
    consented,
    latest_letter,
    planted,
    refusal,
)
from .states import POPULATED, UNREACHABLE

LETTER = "letter"
MAX_LETTER = 50_000  # characters in one handoff letter
_MOVEMENT = re.compile(r"^(\d\d)-[a-z0-9-]+\.md$")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


# -- the canon ------------------------------------------------------------------
def canon_dirs(grove: Path | None) -> list[Path]:
    """Where the canon is looked for, in the Grove's order."""
    out = []
    if os.environ.get("WILLOW_HOME"):
        out.append(Path(os.environ["WILLOW_HOME"]) / "seed" / "canon")
    if grove is not None:
        out.append(Path(grove) / "governance" / "seed" / "canon")
    return out


def movements(grove: Path | None) -> tuple[list[dict] | None, str]:
    """The canon's movements in order, each {n, title, path, sha256, text}, from
    the first canon dir that holds any; (None, why) when none does."""
    for d in canon_dirs(grove):
        files = sorted(p for p in d.glob("*.md") if _MOVEMENT.match(p.name))
        if not files:
            continue
        out = []
        for i, p in enumerate(files, 1):
            text = p.read_text(encoding="utf-8")
            title = next(
                (ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")),
                p.stem,
            )
            sha = hashlib.sha256(text.encode()).hexdigest()
            out.append(
                {"n": i, "title": title, "path": str(p), "sha256": sha, "text": text}
            )
        return out, str(d)
    looked = ", ".join(str(d) for d in canon_dirs(grove)) or "nowhere (no Grove set)"
    return None, f"no canon found; looked in {looked}"


# -- the verbs --------------------------------------------------------------------
def _known_repos(run, cfg) -> set[str]:
    """The repos this box reads: its index's, and any the config names."""
    from . import seed

    index, _, _ = seed.read_index(run.rec)
    return set((index or {}).get("arrival", {})) | set(cfg.repos or {})


def _consent_row(run, cfg, allow, training: bool) -> tuple[dict | None, str | None]:
    allow = sorted(set(allow))
    unknown = sorted(set(allow) - _known_repos(run, cfg))
    if unknown:
        return None, f"no repo named {', '.join(unknown)} in this box"
    row = run.rec.append(
        CONSENT,
        run.sys,
        where="session",
        streams=allow,
        training=bool(training),
        expires="session",
    )
    return row, None


def plant(cfg, allow: Iterable[str] = (), *, training=False, argv=("plant",)) -> dict:
    """Plant the box: the canon to read, its hashes on a ``plant`` row, and this
    session's consent. Once per box; a second asks for `consent` instead."""
    from . import api  # the verbs load the whole one script; the rules don't

    run, info = api._open(cfg, PLANT, argv)
    if run is None:
        return info
    if (no := api._need_open(run, PLANT)) is not None:
        return no
    rows = run.rec.rows()
    if (done := planted(rows)) is not None:
        return api._refused(
            f"this box was planted at row {done['n']}; each session gives "
            "`consent` instead",
            1,
        )
    moves, where = movements(cfg.grove)
    if moves is None:
        return {"state": UNREACHABLE, "why": where, "code": 1}
    row = run.rec.append(
        PLANT,
        run.sys,
        where=where,
        canon=[{"n": m["n"], "sha256": m["sha256"]} for m in moves],
    )
    given, why = _consent_row(run, cfg, allow, training)
    if why is not None:
        return {"state": POPULATED, "row": row["n"], "consent": why, "code": 1,
                "movements": moves}  # fmt: skip
    return {
        "state": POPULATED,
        "row": row["n"],
        "consent": {"row": given["n"], "streams": given["streams"]},
        "movements": moves,
        "code": 0,
    }


def consent(
    cfg, allow: Iterable[str] = (), *, training=False, argv=("consent",)
) -> dict:
    """This session's consent: the repos it may read. Expires at check-out."""
    from . import api

    run, info = api._open(cfg, CONSENT, argv)
    if run is None:
        return info
    if (no := api._need_open(run, CONSENT)) is not None:
        return no
    row, why = _consent_row(run, cfg, allow, training)
    if why is not None:
        return api._refused(why, 1)
    return {"state": POPULATED, "row": row["n"], "streams": row["streams"],
            "training": row["training"], "code": 0}  # fmt: skip


def letter_name(box: Path, day: str) -> str:
    """``letters/<day><a-z>.md``: always lettered, so the newest sorts last."""
    used = set(sorted(p.name for p in (Path(box) / LETTERS).glob(f"{day}?.md")))
    for c in _LETTERS:
        if f"{day}{c}.md" not in used:
            return f"{LETTERS}/{day}{c}.md"
    return f"{LETTERS}/{day}z.md"


def letter(cfg, text: str, *, argv=("letter",)) -> dict:
    """Write the handoff letter for the next session, through the record."""
    from . import api, mai_shape

    if not isinstance(text, str) or not text.strip():
        return api._refused("a letter with nothing in it", 1)
    if len(text) > MAX_LETTER:
        return api._refused(f"a letter is at most {MAX_LETTER} characters", 1)
    run, info = api._open(cfg, LETTER, argv)
    if run is None:
        return info
    if (no := api._need_open(run, LETTER)) is not None:
        return no
    ts = run.rec.clock()
    rel = letter_name(cfg.box, ts[:10])
    body = f"---\nkind: handoff\nwritten: {ts}\n---\n\n{text.rstrip()}\n"
    row = run.rec.write_file(
        run.sys, rel, body.encode(), provenance="authored", check=mai_shape.check
    )
    return {"state": POPULATED, "path": rel, "row": row["n"], "code": 0}
