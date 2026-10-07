"""2 predict — declare the next bite before the work; grade it after.

Cites: CONST-IV (a prediction is a Proposed claim), §0.2 (it never grades itself
into truth: the record grades it).

The distribution is counted, not guessed: what kinds of bites followed before.
Singletons stay in as outliers, kept equal. Nothing smooths.
"""

from __future__ import annotations


def declare(rows: list[dict]) -> dict:
    kinds = [r["bite"] for r in rows if r["kind"] == "bite"]
    if not kinds:
        return {"likely": None, "median": [], "outliers": [], "n": 0}
    counts: dict[str, int] = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    total = len(kinds)
    dist = [{"bite": k, "p": round(c / total, 3), "seen": c} for k, c in ranked]
    return {
        "likely": dist[0],
        "median": [d for d in dist[1:] if d["seen"] > 1],
        "outliers": [d for d in dist if d["seen"] == 1 and d is not dist[0]],
        "n": total,
    }


def grade(prediction: dict, actual: str) -> dict:
    p = {
        d["bite"]: d["p"]
        for d in ([prediction["likely"]] if prediction.get("likely") else [])
        + prediction.get("median", [])
        + prediction.get("outliers", [])
    }
    likely = prediction["likely"]["bite"] if prediction.get("likely") else None
    return {
        "actual": actual,
        "likely": likely,
        "hit": actual == likely,
        "p_actual": p.get(actual, 0.0),
        "unforeseen": actual not in p,
    }
