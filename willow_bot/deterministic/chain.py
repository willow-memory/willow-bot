"""The deterministic chain: D0, then the local tier, then a flowering row.

One act at a time:

1. **D0** (``resolvers.resolve_fixture``) closes what code can close.
2. Only a D0 ``escalate`` reaches the **local tier**: one loopback Ollama
   call under a JSON format schema in which ``ESCALATE`` is always a valid
   answer (gap 81451b988833).
3. Whatever the local tier escalates, answers outside the schema, cites
   outside the pool, or cannot be reached for, becomes a **flowering** row
   addressed to ``willow``. The chain never calls a cloud model: flowering
   is the desk's act (forge-convergence §1.5, the bud rule).

stdlib only. One JSONL row per act. Parent: willows-grove
``docs/design/forge-convergence-flowering-experiment.md`` §4.5 and §9.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot.deterministic.ollama import chat
from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.resolvers import (
    ROUTE_TARGET,
    STATUS_ESCALATE,
    STATUS_FLOWERING_REQUIRED,
    STATUS_RESOLVED,
    _fixture_sha256,
    list_fixture_files,
    resolve_fixture,
    score_resolution,
)

TIER_D0 = "d0"
TIER_LOCAL = "local"
TIER_FLOWERING = "flowering"

LOCAL_ANSWER = "answer"
LOCAL_ESCALATE = "ESCALATE"

# Ollama ``format``: the reply must be this object, nothing else.
LOCAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": [LOCAL_ANSWER, LOCAL_ESCALATE]},
        "answer": {"type": "string"},
        "cites": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "answer", "cites"],
}

# Classes the protocol scores with an operator rubric on top of the machine
# check (flowering experiment §5).
_RUBRIC_MIN = {"G1": 4, "G3": 3}

ChatFn = Callable[..., tuple[str, int, "str | None"]]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _excerpt_ids(fixture: dict) -> list[str]:
    return [
        ex["id"]
        for ex in fixture.get("excerpts") or []
        if isinstance(ex, dict) and isinstance(ex.get("id"), str) and ex["id"]
    ]


def local_prompt(fixture: dict) -> str:
    """The brief, the pool's excerpts, and the reply contract -- nothing
    else. No transcript, no retrieval, no sealed-pair injection."""
    parts = [str(fixture.get("brief") or "").strip()]
    blocks = []
    for ex in fixture.get("excerpts") or []:
        if not isinstance(ex, dict):
            continue
        blocks.append(f"[{ex.get('id', 'ex')}]\n{ex.get('text', '')}")
    if blocks:
        parts.append("Excerpts:\n" + "\n\n".join(blocks))
    parts.append(
        "Reply with one JSON object: "
        '{"status": "answer" or "ESCALATE", "answer": "<text>", '
        '"cites": ["<excerpt id>", ...]}. '
        'Use "ESCALATE" when the excerpts do not hold enough to answer, or '
        "when no one the brief names can act on it; then leave answer empty. "
        "Cite only excerpt ids shown above."
    )
    return "\n\n".join(p for p in parts if p)


def parse_local_reply(reply: str, pool_ids: list[str]) -> dict:
    """Judge one local reply against the schema and the pool.

    Returns ``{"outcome", "answer", "cites", "reason"}`` where ``outcome``
    is ``answer`` or ``escalate``, or ``refused`` for a reply the chain
    must not trust (``schema_fail``: not the contracted object;
    ``link_fail``: a cite that is not in the pool, flowering §1.3).
    """
    try:
        obj = json.loads(reply)
    except (TypeError, ValueError):
        return {"outcome": "refused", "answer": None, "cites": [], "reason": "schema_fail"}
    if not isinstance(obj, dict):
        return {"outcome": "refused", "answer": None, "cites": [], "reason": "schema_fail"}
    status = obj.get("status")
    answer = obj.get("answer")
    cites = obj.get("cites")
    if (
        status not in (LOCAL_ANSWER, LOCAL_ESCALATE)
        or not isinstance(answer, str)
        or not isinstance(cites, list)
        or not all(isinstance(c, str) for c in cites)
    ):
        return {"outcome": "refused", "answer": None, "cites": [], "reason": "schema_fail"}
    if status == LOCAL_ESCALATE:
        return {"outcome": "escalate", "answer": None, "cites": list(cites), "reason": "local_escalate"}
    if not answer.strip():
        return {"outcome": "refused", "answer": None, "cites": [], "reason": "schema_fail"}
    stray = [c for c in cites if c not in pool_ids]
    if stray:
        return {
            "outcome": "refused",
            "answer": answer,
            "cites": list(cites),
            "reason": "link_fail",
        }
    return {"outcome": "answer", "answer": answer.strip(), "cites": list(cites), "reason": None}


def _flowering(row: dict, reason: str) -> dict:
    row["tier"] = TIER_FLOWERING
    row["status"] = TIER_FLOWERING
    row["route_to"] = ROUTE_TARGET
    row["reason"] = reason
    return row


def chain_act(
    fixture: dict,
    *,
    policy: Policy,
    model: str,
    chat_fn: ChatFn = chat,
) -> dict:
    """Run one act through the chain and return its row (unscored fields
    included). Never raises on a malformed fixture or a failed call."""
    d0 = resolve_fixture(fixture)
    fx = fixture if isinstance(fixture, dict) else {}
    row: dict[str, Any] = {
        "fixture_id": d0.get("fixture_id"),
        "class": d0.get("class"),
        "excluded_from_T": bool(fx.get("excluded_from_T", False)),
        "generative": bool(fx.get("generative", False)),
        "d0_status": d0.get("status"),
        "d0_reason": d0.get("reason"),
        "model": None,
        "latency_ms": None,
        "raw_reply": None,
        "local_error": None,
    }

    if d0.get("status") != STATUS_ESCALATE:
        score = score_resolution(fixture, d0)
        row.update(
            tier=TIER_D0,
            status=d0["status"],
            answer=d0.get("answer"),
            cites=d0.get("cites") or [],
            reason=d0.get("reason"),
            scored=score["scored"],
            correct=score["correct"],
            score_detail=score["detail"],
        )
        if d0["status"] == STATUS_FLOWERING_REQUIRED:
            row["route_to"] = ROUTE_TARGET
        return row

    row.update(answer=None, cites=[], scored=False, correct=None, score_detail=None)
    if not isinstance(fixture, dict) or d0.get("reason") == "malformed_fixture":
        return _flowering(row, "malformed_fixture")

    row["model"] = model
    reply, latency_ms, err = chat_fn(
        base_url=policy.ollama_base,
        model=model,
        prompt=local_prompt(fixture),
        timeout_s=policy.ollama_chat_timeout_s,
        fmt=LOCAL_SCHEMA,
    )
    row["latency_ms"] = latency_ms
    row["raw_reply"] = reply
    if err:
        # Unreachable or timed out is not an escalation: the local tier
        # never answered, and the row says so.
        row["local_error"] = err
        return _flowering(row, "local_unreachable")

    judged = parse_local_reply(reply, _excerpt_ids(fixture))
    if judged["outcome"] != "answer":
        row["cites"] = judged["cites"]
        return _flowering(row, judged["reason"])

    resolution = {
        "class": fixture.get("class"),
        "status": STATUS_RESOLVED,
        "answer": judged["answer"],
        "cites": judged["cites"],
    }
    score = score_resolution(fixture, resolution)
    row.update(
        tier=TIER_LOCAL,
        status=STATUS_RESOLVED,
        answer=judged["answer"],
        cites=judged["cites"],
        reason=None,
        scored=score["scored"],
        correct=score["correct"],
        score_detail=score["detail"],
        rubric_min=_RUBRIC_MIN.get(str(fixture.get("class"))),
    )
    return row


def summarize_chain(rows: list[dict]) -> dict:
    """Coverage and precision per tier, over counted acts only.

    ``counted`` excludes ``excluded_from_T`` rows (G4, flowering §5).
    ``cloud_per_act_max`` is ``flowering / counted``: each flowering act
    costs at most one cloud call, and may cost none (an operator act).
    """
    counted = [r for r in rows if not r.get("excluded_from_T")]
    by_tier: dict[str, int] = {}
    flowering_reasons: dict[str, int] = {}
    precision: dict[str, dict[str, int]] = {}
    for r in counted:
        tier = r.get("tier") or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if tier == TIER_FLOWERING or r.get("status") == STATUS_FLOWERING_REQUIRED:
            reason = r.get("reason") or r.get("status") or "unknown"
            flowering_reasons[reason] = flowering_reasons.get(reason, 0) + 1
        if r.get("scored"):
            bucket = precision.setdefault(tier, {"total": 0, "correct": 0})
            bucket["total"] += 1
            if r.get("correct"):
                bucket["correct"] += 1
    n_counted = len(counted)
    closed_code = sum(
        1
        for r in counted
        if r.get("tier") == TIER_D0 and r.get("status") != STATUS_FLOWERING_REQUIRED
    )
    closed_local = by_tier.get(TIER_LOCAL, 0)
    flowering = n_counted - closed_code - closed_local
    return {
        "n": len(rows),
        "counted": n_counted,
        "closed_code": closed_code,
        "closed_local": closed_local,
        "flowering": flowering,
        "grown_share": (closed_code + closed_local) / n_counted if n_counted else None,
        "cloud_per_act_max": flowering / n_counted if n_counted else None,
        "by_tier": by_tier,
        "flowering_reasons": flowering_reasons,
        "precision": precision,
    }


def d0_wrong(rows: list[dict]) -> bool:
    """D0 precision must be 1.0 -- a wrong code answer is a defect. A wrong
    local answer is a measurement, not a failed run."""
    return any(
        r.get("tier") == TIER_D0 and r.get("scored") and not r.get("correct")
        for r in rows
    )


def run_chain(
    policy: Policy,
    *,
    fixtures_dir: Path,
    model: str,
    out_path: Path | None = None,
    chat_fn: ChatFn = chat,
) -> dict:
    """Run every ``S-growth-*.json`` under ``fixtures_dir`` through the
    chain; write ``chain-<UTC stamp>.jsonl`` under ``policy.runs_dir``
    unless ``out_path`` is given. Returns ``{"summary", "rows", "out_path"}``.
    """
    fixtures_dir = Path(fixtures_dir).resolve()
    rows: list[dict] = []
    for fp in list_fixture_files(fixtures_dir):
        try:
            fixture = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            row = {
                "fixture_id": fp.stem,
                "class": None,
                "excluded_from_T": False,
                "answer": None,
                "cites": [],
                "scored": False,
                "correct": None,
                "local_error": None,
            }
            rows.append(_flowering(row, f"fixture_read_error: {exc}"))
            continue
        row = chain_act(fixture, policy=policy, model=model, chat_fn=chat_fn)
        row["fixture_sha256"] = _fixture_sha256(fp)
        rows.append(row)
    if out_path is None:
        out_path = policy.runs_dir / f"chain-{_utc_stamp()}.jsonl"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    summary = summarize_chain(rows)
    summary["fixtures_dir"] = str(fixtures_dir)
    summary["model"] = model
    summary["out_path"] = str(out_path)
    return {"summary": summary, "rows": rows, "out_path": out_path}
