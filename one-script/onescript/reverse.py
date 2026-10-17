"""7 reverse — re-check old assertions against what is true now.

Cites: CONST-XI (review resolves contradiction), App. A (four verdicts, never
pass/fail; every failing line says why), App. B (this module's own coverage
comes from outside it), CONST-IV.2 (independent witnesses: one per family).

Triggers: check-out, the heartbeat, a law change, the run's own version changing.
Everything here is computed. Same inputs, same report bytes.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import calibration
from .gate import verify_seal
from .record import TIP, canon, h256, tip_subject

WAGER = "wager:"

#: A wager row written by `api.forecast` carries this as `commit`: its outcome
#: counts only by chain order under the human's sealed tip (SPEC R3,
#: `wager.n <= tip.n < tip_seal.n < outcome_seal.n`), never by a timestamp.
#: A wager row without it is the older shape and is lined up as before.
COMMIT = "chain-order/v1"

#: Who a forecast is by: a role, never a person's name (SPEC-documented J1).
ROLES = ("human", "desk", "oracle", "coin")

KEEP_OUT = {"record.jsonl", "pile.json"}


def three_way(box: Path, pile: dict, rows: list[dict]) -> list[dict]:
    """What the pile lists x what is on disk x what the record says was written."""
    on_disk = {
        str(p.relative_to(box)): p
        for p in box.rglob("*")
        if p.is_file() and p.name not in KEEP_OUT and not p.name.endswith(".tmp")
    }
    written = {r["path"] for r in rows if r["kind"] == "write"}
    listed = {i["where"]: i for i in pile.get("items", [])}
    out = []
    for where in sorted(set(on_disk) | set(listed) | written):
        i, p = listed.get(where), on_disk.get(where)
        if i and p:
            if i.get("live"):
                v, why = "differently", "live: changes by design"
            elif h256(p.read_bytes()) == i["sha"]:
                v, why = "satisfied", ""
            else:
                v, why = (
                    "failing",
                    "changed since it was listed, and no row records why",
                )
        elif i and not p:
            v, why = (
                ("not applicable", "expected to vanish, recorded at write")
                if i.get("expect_vanish")
                else ("failing", "listed, gone, and no recorded cause")
            )
        elif p and not i:
            v, why = "failing", "on disk, never listed: written outside the run"
        else:
            v, why = (
                "failing",
                "the record says it was written; it is neither listed nor present",
            )
        out.append({"where": where, "verdict": v, "why": why})
    return out


def open_turns(rows: list[dict]) -> list[int]:
    opened = {r["turn"]: r for r in rows if r["kind"] == "turn_open"}
    closed = {r["turn"] for r in rows if r["kind"] == "turn_close"}
    return sorted(set(opened) - closed)


def repetitions(rows: list[dict], at: int = 3) -> list[dict]:
    """Counted from the record. The third time is an offer, never an apply."""
    fam: dict[str, list[int]] = {}
    for r in rows:
        if r["kind"] == "script":
            fam.setdefault(r["family_root"], []).append(r["n"])
    return [
        {"procedure": k, "seen": len(v), "rows": v, "offer": "make it a standing step?"}
        for k, v in sorted(fam.items())
        if len(v) >= at
    ]


def surfaced(rows: list[dict], changed_law: set[str], version: str) -> list[dict]:
    """Old assertions to re-check: they cited law that changed, or an older run made them."""
    out = []
    for r in rows:
        why = []
        hit = sorted(set(r.get("cites", [])) & changed_law)
        if hit:
            why.append(f"cites changed law {hit}")
        if r["kind"] in ("answer", "write", "script") and r["version"] != version:
            why.append(f"made by run version {r['version']}, now {version}")
        if why:
            out.append({"row": r["n"], "kind": r["kind"], "why": "; ".join(why)})
    return out


def witnesses(answers: list[dict]) -> dict:
    """Side to side. Different families are separate witnesses; one family is one."""
    by_q: dict[str, dict[str, set[str]]] = {}
    for a in answers:
        by_q.setdefault(a["q"], {}).setdefault(a["family"], set()).add(
            repr(a["answer"])
        )
    agreed, split = [], []
    for q, fams in sorted(by_q.items()):
        torn = sorted(f for f, v in fams.items() if len(v) > 1)
        votes: dict[str, list[str]] = {}
        for f, v in fams.items():
            if len(v) == 1:
                votes.setdefault(next(iter(v)), []).append(f)
        if len(votes) == 1 and not torn and len(fams) >= 2:
            ans, fs = next(iter(votes.items()))
            agreed.append(  # Opus P6 / VI.5: the absence of dissent is a recorded fact
                {
                    "q": q,
                    "answer": ans,
                    "families": sorted(fs),
                    "standing": "witnessed",
                    "dissent": "none recorded",
                }
            )
        else:
            split.append(
                {
                    "q": q,
                    "by_answer": {k: sorted(v) for k, v in sorted(votes.items())},
                    "torn_within_family": torn,
                    "families": len(fams),
                }
            )
    split.sort(
        key=lambda s: (-len(s["by_answer"]) - len(s["torn_within_family"]), s["q"])
    )
    return {"agreed": agreed, "split": split}


def grades(
    answers: list[dict], sealed: dict[str, str], task_of: dict[str, str]
) -> dict:
    """Per model, per kind of task, against what the human sealed. Floor = worst kind."""
    cells: dict[str, dict[str, list[int]]] = {}
    for a in answers:
        if a["q"] not in sealed:
            continue  # ungraded until a human seals the truth
        ok = int(repr(a["answer"]) == repr(sealed[a["q"]]))
        cells.setdefault(a["from"], {}).setdefault(task_of.get(a["q"], "?"), []).append(
            ok
        )
    out = {}
    for m, kinds in sorted(cells.items()):
        rate = {k: round(sum(v) / len(v), 3) for k, v in sorted(kinds.items())}
        worst = min(rate.items(), key=lambda kv: (kv[1], kv[0]))
        out[m] = {"by_task": rate, "floor": worst[1], "floor_task": worst[0]}
    return out


def _norm(path: object) -> str:
    return str(path or "").strip().casefold()


def _match_distribution(wager: dict, outcome: dict) -> tuple[int | None, dict | None]:
    """The distribution entry a sealed outcome names: by `path_index`; else by
    `path`, an exact match (stripped, casefolded) first, then a substring only
    when exactly one entry holds it. (None, None) when it names none, or more
    than one: "rain" names "rain", never "no rain"."""
    dist = wager.get("distribution") or []
    if "path_index" in outcome:
        i = outcome["path_index"]
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(dist):
            return i, dist[i]
        return None, None
    needle = _norm(outcome.get("path"))
    if not needle:
        return None, None
    for exact in (True, False):
        hits = [
            (i, e)
            for i, e in enumerate(dist)
            if (
                _norm(e.get("path")) == needle
                if exact
                else needle in _norm(e.get("path"))
            )
        ]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None, None  # ambiguous: named by more than one entry
    return None, None


def covered_through(rec, human_key: bytes | None) -> int | None:
    """The row number the human's sealed anchor tip provably covers, or None.

    The seal proof signs the subject only, never the outcome a seal row carries.
    The outcome is bound by the tip instead: the human signs the tip (row n and
    its full hash), and the chain hash commits to every row before it. So rows
    up to n are covered only while ALL hold: an anchor is on record, the whole
    chain verifies, the chain reaches the sealed tip with the human's proof
    checked, and the human's key is present to check it. Otherwise None."""
    if human_key is None or rec.anchor_state() != "sealed":
        return None
    if rec.verify_chain() or rec.verify_anchor(human_key):
        return None
    return rec.anchor()["n"]


def pinned(rec, human_key: bytes | None) -> tuple[int | None, str | None]:
    """How far seal rows with no proof on them may count: (n, None) for the
    row number of the tip the human had sealed when this code first wrote to
    the box (its `pin` row), (None, None) when nothing may count, and
    (None, why) when the box has no pin yet.

    The human's tip says the rows up to it never change, not that the human
    wrote each of them; a seal row with no proof could have been appended by
    anyone before the tip was sealed. So only rows from before seal rows
    carried proofs may lean on the tip, and the pin is where that stops. The
    earliest `pin` row is the one: it was written before anything else this
    code wrote, so a later one changes nothing. It holds only while its proof
    re-checks with the human's key and the chain's row at its tip still has
    its hash. A pin with no tip (no anchor at first run) lets nothing count."""
    rows = rec.rows()
    pin = next((r for r in rows if r["kind"] == "pin"), None)
    if pin is None:
        return None, "unreachable: old seals are not pinned yet; check in"
    tn, th, pf = pin.get("tip_n"), pin.get("tip_hash"), pin.get("proof")
    if human_key is None or not isinstance(tn, int) or tn >= pin["n"]:
        return None, None
    if not isinstance(th, str) or not isinstance(pf, str):
        return None, None
    if not verify_seal(tip_subject({"n": tn, "hash": th}), pf, human_key):
        return None, None
    at = next((r for r in rows if r["n"] == tn), None)
    return (tn, None) if at is not None and at["hash"] == th else (None, None)


def forecast_subject(wager: str) -> str:
    """What the human signs to say a forecast is theirs (`api.seal_forecast`):
    the wager's row hash under its own prefix, so it is never an outcome."""
    return "forecast:" + wager


def outcome_subject(wager: str, outcome: dict) -> str:
    """What the human signs to seal a wager's outcome: one hash over the
    wager's row hash and the outcome together, so the outcome is signed."""
    return WAGER + h256(canon({"wager": wager, "outcome": outcome}))


def _outcome_row(
    wager: dict,
    rows: list[dict],
    through: int | None = None,
    held: set[str] | None = None,
) -> dict | None:
    """The human seal row that carries an outcome over this wager, or None.
    Only a `seal` row whose subject is `outcome_subject` of this wager and the
    outcome on the row, whose proof re-checks (`held`, from
    `serve.sealed_verified`), and that the sealed anchor tip covers (row
    number <= `through`); the latest such seal wins. A row's `who` is a label,
    so it alone counts for nothing, and without `held` no outcome counts."""
    if through is None or not held:
        return None
    got = [
        r
        for r in rows
        if r["kind"] == "seal"
        and r.get("wager") == wager["hash"]
        and isinstance(r.get("outcome"), dict)
        and r.get("subject") == outcome_subject(wager["hash"], r["outcome"])
        and r["subject"] in held
        and r["n"] <= through
    ]
    return got[-1] if got else None


def _outcome_of(
    wager: dict,
    rows: list[dict],
    through: int | None = None,
    held: set[str] | None = None,
) -> dict | None:
    """The outcome a human seal carries over this wager, or None (`_outcome_row`)."""
    row = _outcome_row(wager, rows, through, held)
    return row["outcome"] if row else None


def check_distribution(dist: object) -> str | None:
    """Why a forecast's distribution is refused, or None when it stands
    (SPEC-documented J1 `api.forecast`): a non-empty list of {path, p, band?,
    tags?}; every p a real number in [0, 1]; the ps sum to 1 within 1e-9; no
    path twice."""
    if not isinstance(dist, list) or not dist:
        return "a forecast needs a non-empty distribution"
    seen: set[str] = set()
    total = 0.0
    for i, e in enumerate(dist):
        if not isinstance(e, dict):
            return f"entry {i} is not an object"
        extra = set(e) - {"path", "p", "band", "tags"}
        if extra:
            return f"entry {i} carries unknown keys {sorted(extra)}"
        path = e.get("path")
        if not isinstance(path, str) or not path.strip():
            return f"entry {i} has no path"
        if any(c in path for c in "\n\r"):
            return f"entry {i}: a path is one line"
        if _norm(path) in seen:
            return f"path {path!r} is declared twice"
        seen.add(_norm(path))
        p = e.get("p")
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            return f"entry {i} ({path!r}): p is not a number"
        if not math.isfinite(p) or not 0 <= p <= 1:
            return f"entry {i} ({path!r}): p {p!r} is outside [0, 1]"
        if "band" in e and not isinstance(e["band"], str):
            return f"entry {i} ({path!r}): band is not text"
        tags = e.get("tags", [])
        if not isinstance(tags, list) or not all(
            isinstance(t, str) and t for t in tags
        ):
            return f"entry {i} ({path!r}): tags is not a list of names"
        if len({t.casefold() for t in tags}) != len(tags):
            return f"entry {i} ({path!r}): a tag is given twice"
        total += p
    if abs(total - 1.0) > 1e-9:
        return f"the ps sum to {total!r}, not 1"
    return None


def in_place(rows: list[dict]) -> bool:
    """Every row's `n` is its place in `rows` (`Record.verify_chain` checks the
    same on the file). A reader handed rows directly checks it again, since
    every order here is read from `n`."""
    return all(
        isinstance(r.get("n"), int) and not isinstance(r["n"], bool) and r["n"] == i
        for i, r in enumerate(rows)
    )


def tip_seals(rows: list[dict], human_key: bytes | None) -> list[tuple[int, int]]:
    """(seal row n, tip n) for every tip seal whose own proof re-checks against
    the human's key and whose tip is still the row the human signed (row n
    with that hash). Per row, never per subject: a proofless row over a subject
    the human sealed later is no seal of its own."""
    if human_key is None:
        return []
    by_n = {r["n"]: r for r in rows}
    out = []
    for r in rows:
        sub = str(r.get("subject") or "")
        if r.get("kind") != "seal" or not sub.startswith(TIP):
            continue
        proof = r.get("proof")
        if not isinstance(proof, str) or not verify_seal(sub, proof, human_key):
            continue
        n, _, hsh = sub[len(TIP) :].partition(":")
        if not n.isdigit() or by_n.get(int(n), {}).get("hash") != hsh:
            continue
        if int(n) < r["n"]:
            out.append((r["n"], int(n)))
    return out


def committed_under(
    wager: dict, seal_n: int, tips: list[tuple[int, int]]
) -> tuple[int, int] | None:
    """The first sealed tip that holds this wager before its outcome seal:
    `wager.n <= tip.n < tip_seal.n < seal_n` (SPEC R3). None when no tip does."""
    return next(
        ((t, tn) for t, tn in sorted(tips) if wager["n"] <= tn and t < seal_n),
        None,
    )


def wager_pairs(wager: dict, path_index: int) -> list[tuple[float, bool]]:
    """(p, happened) pairs for a wager whose path `path_index` happened. Two
    paths: one pair, (p of path 0, path 0 happened), as `play.py`'s Brier is.
    Any other count: one pair per path, one against the rest, whose mean is
    the multi-class Brier over n. Unfolded: `calibration.fold` is for bins."""
    dist = wager.get("distribution") or []
    ps = [float(e["p"]) for e in dist]
    if len(ps) == 2:
        return [(ps[0], path_index == 0)]
    return [(p, i == path_index) for i, p in enumerate(ps)]


def score(wager: dict, path_index: int) -> dict:
    """Code's score of one reconciled wager: Brier (mean over its pairs) and
    log score (of the p it gave what happened). A score, never a grade."""
    pairs = wager_pairs(wager, path_index)
    p_actual = float(wager["distribution"][path_index]["p"])
    return {
        "brier": sum(calibration.brier(p, o) for p, o in pairs) / len(pairs),
        # + 0.0: a sure thing that happened scores 0.0, not -0.0
        "log_score": calibration.log_score(p_actual, True) + 0.0,
    }


def reconcile_wagers(
    rows: list[dict],
    through: int | None = None,
    held: set[str] | None = None,
    human_key: bytes | None = None,
) -> dict:
    """Line each declared wager up against the outcome a human sealed. Never
    grades: `graded_by` stays None. `through` is the row number the human's
    verified anchor tip covers (see `covered_through`); a seal after it, or no
    verified anchor at all, counts for nothing. `held` is the subjects whose
    seal proof re-checks (`Run.held`); an outcome seal outside it counts for
    nothing either. With no covered outcome, or one
    that names nothing the wager declared, the row escalates to the human,
    never to a model.

    A forecast (`commit == COMMIT`) counts only by chain order under the
    human's tip (SPEC R3): some tip seal whose proof re-checks against
    `human_key` holds the wager row and comes before the outcome seal. Any
    other order escalates. A reconciled forecast carries `brier` and
    `log_score`, code's score; the grade stays the human's sealed outcome."""
    if not in_place(rows):  # order is read from `n`: a label out of place
        through, held, human_key = None, None, None  # lets nothing count
    tips = tip_seals(rows, human_key)
    out = []
    for w in (r for r in rows if r["kind"] == "wager"):
        row = {"id": w.get("id", "?"), "subject": w["hash"], "graded_by": None}
        forecast = w.get("commit") == COMMIT
        if forecast:
            # `by` is what the run wrote, a claim; "human" stands only when the
            # human's own seal over `forecast_subject` vouches for it (SPEC S4)
            row["by_claimed"] = w.get("by")
            row["by_vouched"] = bool(held) and forecast_subject(w["hash"]) in held
        seal = _outcome_row(w, rows, through, held)
        outcome = seal["outcome"] if seal else None
        under = committed_under(w, seal["n"], tips) if seal and forecast else None
        bad = check_distribution(w.get("distribution")) if forecast else None
        if outcome is None:
            row |= {
                "state": "escalate",
                "escalate_to": "human",
                "reason": "no human seal covered by the sealed anchor tip names an "
                "outcome; the grade is the human's",
            }
        elif forecast and bad is not None:
            row |= {
                "state": "escalate",
                "escalate_to": "human",
                "reason": f"the forecast's distribution does not stand: {bad}",
            }
        elif forecast and under is None:
            row |= {
                "state": "escalate",
                "escalate_to": "human",
                "reason": "no tip the human sealed holds this forecast before its "
                "outcome seal (wager.n <= tip.n < tip_seal.n < outcome.n)",
            }
        elif w.get("distribution"):
            idx, entry = _match_distribution(w, outcome)
            if entry is None:
                row |= {
                    "state": "escalate",
                    "escalate_to": "human",
                    "reason": "the sealed outcome names no distribution path",
                }
            else:
                row |= {
                    "state": "reconciled",
                    "path_index": idx,
                    "predicted_p": entry.get("p"),
                    "predicted_band": entry.get("band"),
                    "actual_path": entry.get("path"),
                }
                if forecast and under is not None:
                    row |= score(w, idx)  # type: ignore[arg-type]
                    row["committed_under"] = {"tip_seal": under[0], "tip": under[1]}
                    # rival T6: a tip the human sealed late still commits; the
                    # times are shown beside it so a late wager is visible
                    row["wager_ts"] = w.get("ts")
                    row["tip_seal_ts"] = rows[under[0]].get("ts")
                    c = w.get("committed")
                    if isinstance(c, dict) and "beacon_then" in c:
                        row["beacon_then"] = c["beacon_then"]
        elif w.get("scores") is not None and "winner" in outcome:
            row |= {
                "state": "reconciled",
                "winner": outcome["winner"],
                "scores": w["scores"],
            }
        else:
            row |= {
                "state": "escalate",
                "escalate_to": "human",
                "reason": "the wager declares no distribution or scores to line up",
            }
        out.append(row)
    return {
        "reconciled": [r["id"] for r in out if r["state"] == "reconciled"],
        "escalate": [r["id"] for r in out if r["state"] == "escalate"],
        "escalate_to": "human" if any(r["state"] == "escalate" for r in out) else None,
        "rows": out,
    }


def role_of(report_row: dict) -> str:
    """The group a scored forecast counts under. `by_claimed` is the run's
    word; a forecast claimed as the human's counts as "human" only when the
    human sealed `forecast_subject` over it, else "human (claimed)". The other
    roles are code's or the desk's and are shown as claimed."""
    claimed = str(report_row.get("by_claimed") or "?")
    if claimed == "human" and not report_row.get("by_vouched"):
        return "human (claimed)"
    return claimed


def _scored(report_rows: list[dict]) -> list[dict]:
    return [r for r in report_rows if r.get("state") == "reconciled" and "brier" in r]


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def calibration_of(report_rows: list[dict], rows: list[dict]) -> dict:
    """Over the scored forecasts of a `reconcile_wagers` report: the mean
    Brier and log score, overall and per role (`role_of`), and the reliability bins
    over every pair restated to the believed side (SPEC R5: the fold is for
    the bins only; Brier and log score are computed unfolded)."""
    wagers = {r["hash"]: r for r in rows if r["kind"] == "wager"}
    scored = _scored(report_rows)

    def card(group: list[dict]) -> dict:
        return {
            "n": len(group),
            "brier": _mean([r["brier"] for r in group]),
            "log_score": _mean([r["log_score"] for r in group]),
        }

    by: dict[str, list[dict]] = {}
    for r in scored:
        by.setdefault(role_of(r), []).append(r)
    pairs = [
        calibration.fold(pr)
        for r in scored
        for pr in wager_pairs(wagers[r["subject"]], r["path_index"])
    ]
    return {
        "all": card(scored),
        "by": {k: card(v) for k, v in sorted(by.items())},
        "bins": calibration.bins(pairs),
    }


def read_misses(report_rows: list[dict], rows: list[dict]) -> dict:
    """The misses among scored forecasts, clustered, not only counted
    (chapter 8: "Read the misses. The misses are the map."). A miss: what
    happened was not the path given the most p (a tie at the top is no miss;
    two paths: p of what happened < 0.5). A cluster: two or more misses that
    share a key, where a key is `forecast->happened` or a tag any entry of the
    wager carries. Read-only: it grades nothing."""
    wagers = {r["hash"]: r for r in rows if r["kind"] == "wager"}
    misses = []
    for r in _scored(report_rows):
        w = wagers[r["subject"]]
        dist = w["distribution"]
        top = max(float(e["p"]) for e in dist)
        p_hap = float(dist[r["path_index"]]["p"])
        if p_hap >= top:
            continue
        said = next(e["path"] for e in dist if float(e["p"]) == top)
        tags = sorted({t for e in dist for t in e.get("tags", [])})
        misses.append(
            {
                "id": r["id"],
                "forecast": said,
                "happened": r["actual_path"],
                "p_happened": p_hap,
                "tags": tags,
            }
        )
    keyed: dict[str, list[str]] = {}
    for m in misses:
        keyed.setdefault(f"{m['forecast']}->{m['happened']}", []).append(m["id"])
        for t in m["tags"]:
            keyed.setdefault(f"tag:{t}", []).append(m["id"])
    clusters = [{"key": k, "ids": v} for k, v in sorted(keyed.items()) if len(v) >= 2]
    return {"misses": misses, "clusters": clusters}


def _marked(item: dict, why: str | None) -> dict:
    """`item` as it is, or a copy marked: its seal is on record but can't be
    checked here."""
    return item if why is None else {**item, "seal": "unreachable", "seal_why": why}


def reconcile(
    box: Path,
    pile: dict,
    rows: list[dict],
    version: str,
    *,
    changed_law=frozenset(),
    answers=(),
    sealed=None,
    task_of=None,
    predictions=(),
    wagers_through: int | None = None,
    held: set[str] | None = None,
    unreachable: dict[str, str] | None = None,
    wagers_key: bytes | None = None,
) -> dict:
    """`held` is the subjects the human's seal still holds over
    (`serve.sealed_verified`, via `Run.held`). A seal row is only a claim, so
    without `held` nothing counts as sealed and every door and proposal stays
    listed. `unreachable` is the subjects whose seal could not be checked here,
    with why: those stay listed too, each marked `seal: "unreachable"`, so a
    seal that can't be checked never reads as no seal at all."""
    items = three_way(box, pile, rows)
    sealed_rows = held if held is not None else set()
    unchecked = unreachable or {}
    awaiting = sorted(
        (
            _marked(r, unchecked.get(r["hash"]))
            for r in rows
            if r["kind"] == "door"
            and r["hash"] not in sealed_rows
            and r["verdict"] in ("awaiting_seal", "awaiting_grant")
        ),
        key=lambda r: r["ts"],
    )
    # proposals nobody has sealed: shown with `own_idea` leading each row
    proposals = [
        _marked(
            {
                k: r[k]
                for k in ("path", "line", "verdict", "reason", "own_idea")
                if k in r
            },
            unchecked.get(r.get("subject")),  # type: ignore[arg-type]
        )
        for r in rows
        if r["kind"] == "proposal" and r.get("subject") not in sealed_rows
    ]
    wagers = reconcile_wagers(rows, wagers_through, held, wagers_key)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["verdict"]] = counts.get(i["verdict"], 0) + 1
    return {
        "version": version,
        "pile": {
            "counts": dict(sorted(counts.items())),
            "failing": [i for i in items if i["verdict"] == "failing"],
        },
        "open_turns": open_turns(rows),
        "offers": repetitions(rows),
        "surfaced": surfaced(rows, set(changed_law), version),
        "witness": witnesses(list(answers)),
        "grades": grades(list(answers), sealed or {}, task_of or {}),
        "predictions": list(predictions),  # the mechanical bite grades, untouched
        "wagers": wagers,  # declared wagers vs human seals
        "calibration": calibration_of(wagers["rows"], rows),
        "misses": read_misses(wagers["rows"], rows),
        "awaiting": awaiting,
        "proposals": proposals,
        # Opus P3: the queue's depth and age are reported state. It authorizes
        # nothing; it only stops a stall from looking like a quiet period.
        "backpressure": {
            "open": len(awaiting),
            "oldest": awaiting[0]["ts"] if awaiting else None,
        },
        "chain_rows": len(rows),
    }
