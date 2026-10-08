"""7 reverse — re-check old assertions against what is true now.

Cites: CONST-XI (review resolves contradiction), App. A (four verdicts, never
pass/fail; every failing line says why), App. B (this module's own coverage
comes from outside it), CONST-IV.2 (independent witnesses: one per family).

Triggers: check-out, the heartbeat, a law change, the run's own version changing.
Everything here is computed. Same inputs, same report bytes.
"""

from __future__ import annotations

from pathlib import Path

from .record import h256

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


def _match_distribution(wager: dict, outcome: dict) -> tuple[int | None, dict | None]:
    """The distribution entry a sealed outcome names: by `path_index`, else by a
    case-insensitive substring of `path`. (None, None) when it names none."""
    dist = wager.get("distribution") or []
    if "path_index" in outcome:
        i = outcome["path_index"]
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(dist):
            return i, dist[i]
        return None, None
    needle = str(outcome.get("path") or "").strip().lower()
    if needle:
        for i, entry in enumerate(dist):
            if needle in str(entry.get("path") or "").lower():
                return i, entry
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


def _outcome_of(
    wager: dict, rows: list[dict], through: int | None = None
) -> dict | None:
    """The outcome a human seal carries over this wager, or None. Only a
    `seal` row stamped by the human counts, only one whose subject is the
    wager's own hash, and only one the sealed anchor tip covers (row number
    <= `through`); the latest such seal wins."""
    if through is None:
        return None
    got = [
        r
        for r in rows
        if r["kind"] == "seal"
        and r["who"] == "human"
        and r.get("subject") == wager["hash"]
        and isinstance(r.get("outcome"), dict)
        and r["n"] <= through
    ]
    return got[-1]["outcome"] if got else None


def reconcile_wagers(rows: list[dict], through: int | None = None) -> dict:
    """Line each declared wager up against the outcome a human sealed. Never
    grades: `graded_by` stays None. `through` is the row number the human's
    verified anchor tip covers (see `covered_through`); a seal after it, or no
    verified anchor at all, counts for nothing. With no covered outcome, or one
    that names nothing the wager declared, the row escalates to the human,
    never to a model."""
    out = []
    for w in (r for r in rows if r["kind"] == "wager"):
        row = {"id": w.get("id", "?"), "subject": w["hash"], "graded_by": None}
        outcome = _outcome_of(w, rows, through)
        if outcome is None:
            row |= {
                "state": "escalate",
                "escalate_to": "human",
                "reason": "no human seal covered by the sealed anchor tip names an "
                "outcome; the grade is the human's",
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
) -> dict:
    items = three_way(box, pile, rows)
    sealed_rows = {r["subject"] for r in rows if r["kind"] == "seal"}
    awaiting = sorted(
        (
            r
            for r in rows
            if r["kind"] == "door"
            and r["hash"] not in sealed_rows
            and r["verdict"] in ("awaiting_seal", "awaiting_grant")
        ),
        key=lambda r: r["ts"],
    )
    # proposals nobody has sealed: shown with `own_idea` leading each row
    proposals = [
        {k: r[k] for k in ("path", "line", "verdict", "reason", "own_idea") if k in r}
        for r in rows
        if r["kind"] == "proposal" and r.get("subject") not in sealed_rows
    ]
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
        "wagers": reconcile_wagers(
            rows, wagers_through
        ),  # declared wagers vs human seals
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
