"""6 view — the morning screen, drawn from the record and the reverse report.

Cites: CONST-VI (the view never overrides the record), CONST-IV.5 (standing and
ground are reported separately, never one as the other).

Order is fixed: NEEDS YOU, DISAGREEMENTS, AGREED NOT SEALED, GRADES, QUIET.
Outliers first; nothing smoothed. No model text; same report, same bytes.
"""

from __future__ import annotations

import json


def proposal_line(row: dict) -> str:
    """One proposal row for a person: `own_idea` leads, then where it lands, the
    verdict and why. Said the same on the check-out screen and the CLI. A row
    that never parsed has no `own_idea` to report, and says so."""
    own = row.get("own_idea")
    lead = "own_idea: n/a" if own is None else f"own_idea: {'yes' if own else 'no'}"
    where = row.get("path") or f"line {row.get('line')}"
    why = f" · {row['reason']}" if row.get("reason") else ""
    return f"{lead} · {where} · {row.get('verdict')}{why}"


def cover_line(c: dict) -> str:
    """One row a tip would newly cover, as `seal-tip` lists it before the human
    signs (`api.covers`): a forecast or outcome, a table verdict session, or an
    agm_head row. `who` is the record's label, never a signature."""
    if c.get("what") == "agm_head":
        return (
            f"row {c['n']} · agm_head · {c.get('ledger')!r} n {c.get('ledger_n')!r}"
            f" head {c.get('head')!r} · who {c.get('who')!r} · {c.get('ts')}"
        )
    if c.get("what") == "verdict session":
        f = c.get("forecast")
        if f is None:
            fc = "none"
        elif c.get("forecast_on_record") is False:
            fc = f"{f!r} not on record"
        else:
            fc = repr(f)
        return (
            f"row {c['n']} · verdict session {c.get('session')!r}"
            f" · {c.get('rows')} row(s) · forecast {fc}"
            f" · who {', '.join(repr(w) for w in c.get('who') or [])} · {c.get('ts')}"
        )
    return (
        f"row {c['n']} · {c['what']} · {c.get('id')}"
        f" · by {c.get('by_claimed')} (claimed) · {c.get('ts')}"
    )


def needs_you(
    report: dict,
    boot: dict | None = None,
    claims: list | None = None,
    acts: list | None = None,
) -> list[str]:
    """The NEEDS YOU lines, one string each (no bullet), in the screen's order.
    The morning screen prints them; `api.needs` hands them to a seat as data."""
    needs: list[str] = []
    if boot and boot["hard_close"]:
        needs += [f"HARD CLOSE · {x}" for x in boot["lines"]]
    needs += [
        f"turn {t} never closed"
        for t in report["open_turns"]
        if not any(
            x.startswith(f"turn {t} never closed")
            for x in (boot or {}).get("lines", [])
        )
    ]
    needs += [f"{i['where']} · {i['why']}" for i in report["pile"]["failing"]]
    for a in report["awaiting"]:
        card = json.dumps(a.get("card", {}), sort_keys=True)
        needs.append(f"{a['verdict']} · {a.get('reason', '')} · {card}{_seal(a)}")
    needs += [
        f"proposal · {proposal_line(p)}{_seal(p)}" for p in report.get("proposals", [])
    ]
    needs += [
        f"seen {o['seen']}x · {o['procedure']} · {o['offer']}" for o in report["offers"]
    ]
    needs += [
        f"re-check row {s['row']} ({s['kind']}) · {s['why']}"
        for s in report["surfaced"]
    ]
    for a in acts or []:  # layers 6 and 7: what would leave, what wasn't asked for
        if a["verdict"] != "pass":
            card = json.dumps(a.get("card", {}), sort_keys=True)
            needs.append(f"{a['verdict']} · {a['kind']} · {a['reason']} · {card}")
    for c in claims or []:  # layer 5: what was said that the record doesn't back
        if c["verdict"] == "unverified":
            needs.append(
                f"unverified {c['kind']} · {c['claim']!r} · record: {c['record']}"
            )
    bp = report.get("backpressure", {})
    if bp.get("open"):
        needs.insert(
            0, f"queue: {bp['open']} waiting on you, oldest since {bp['oldest']}"
        )
    if boot and boot.get("report", {}).get("state") == "never":
        needs.append("coverage: no reconcile on record; enforcement status unknown")
    return needs


def morning(
    report: dict,
    boot: dict | None = None,
    claims: list | None = None,
    acts: list | None = None,
) -> str:
    L: list[str] = []
    needs = needs_you(report, boot, claims, acts)
    L.append("NEEDS YOU")
    L += [f"  · {x}" for x in needs] or ["  · nothing"]
    if boot and boot["hard_close"]:
        L.append("  options: " + " · ".join(boot["options"]))

    L.append("DISAGREEMENTS")
    split = report["witness"]["split"]
    L += [
        f"  · {s['q']} · "
        + " | ".join(f"{a}: {', '.join(f)}" for a, f in s["by_answer"].items())
        + (
            f" · torn inside: {', '.join(s['torn_within_family'])}"
            if s["torn_within_family"]
            else ""
        )
        for s in split
    ] or ["  · none"]

    L.append("AGREED, NOT SEALED")
    L += [
        f"  · {a['q']} · {a['answer']} · {len(a['families'])} families · {a['standing']}"
        f" · dissent: {a.get('dissent', 'not recorded')}"
        for a in report["witness"]["agreed"]
    ] or ["  · none"]

    L.append("GRADES")
    L += [
        f"  · {p['likely']} predicted, {p['actual']} happened"
        + (" · unforeseen" if p["unforeseen"] else "")
        for p in report["predictions"]
    ]
    L += [
        f"  · {m}: floor {g['floor']} on {g['floor_task']} · {json.dumps(g['by_task'], sort_keys=True)}"
        for m, g in report["grades"].items()
    ]
    for w in report.get("wagers", {}).get("rows", []):
        if w["state"] == "reconciled":
            what = (
                f"band {w['predicted_band']} p {w['predicted_p']}"
                f" · happened: {w['actual_path']}"
                if "actual_path" in w
                else f"winner {w['winner']}"
            )
            if "brier" in w:  # code's score beside it; the grade stays the human's
                what += f" · brier {w['brier']:.3f} · log {w['log_score']:.2f}"
                who = w.get("by_claimed")
                what += f" · by {who}" + (
                    "" if who == "human" and w.get("by_vouched") else " (claimed)"
                )
                what += (
                    f" · wager {w.get('wager_ts')} · tip sealed {w.get('tip_seal_ts')}"
                )
                if "beacon_then" in w:
                    what += f" · beacon then {w['beacon_then']}"
            L.append(f"  · wager {w['id']} reconciled · {what} · grade is yours")
        elif w["state"] == "unreachable":  # a seal on record, unchecked here
            L.append(f"  · wager {w['id']} seal unreachable · {w['why']}")
        else:
            L.append(f"  · wager {w['id']} escalate to human · {w['reason']}")
    cal = report.get("calibration") or {}
    for role, c in (cal.get("by") or {}).items():
        L.append(
            f"  · calibration {role}: n {c['n']} · brier {c['brier']:.5f}"
            f" · log {c['log_score']:.3f}"
        )
    if (cal.get("all") or {}).get("n"):
        m = report.get("misses") or {}
        L.append(
            f"  · misses: {len(m.get('misses', []))}, clusters: {len(m.get('clusters', []))}"
        )
    if not report["predictions"] and not report["grades"]:
        if not report.get("wagers", {}).get("rows"):
            L.append("  · nothing graded yet")

    c = report["pile"]["counts"]
    L.append("QUIET")
    L.append(
        f"  · pile {json.dumps(c, sort_keys=True)} · {report['chain_rows']} rows"
        f" · run {report['version']}"
    )
    if boot and boot.get("gates"):
        tally: dict[str, int] = {}
        for g in boot["gates"]:
            tally[g["verdict"]] = tally.get(g["verdict"], 0) + 1
        L.append(f"  · gates {json.dumps(tally, sort_keys=True)}")
    if claims:
        ok = sum(c["verdict"] == "verified" for c in claims)
        L.append(f"  · claims checked: {ok}/{len(claims)} verified")
    if boot:
        held = sum(p["held"] for p in boot["probes"])
        L.append(
            f"  · probes held: {held}/{len(boot['probes'])}"
            f" · egress: {', '.join(boot['egress']) or 'none'}"
        )
    return "\n".join(L) + "\n"


def _seal(item: dict) -> str:
    """A seal on record that can't be checked here: said, never dropped."""
    if item.get("seal") != "unreachable":
        return ""
    return f" · seal unreachable: {item.get('seal_why', '')}"
