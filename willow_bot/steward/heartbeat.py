"""Curated willow-mcp tool heartbeat for the steward (prove phase).

Calls platform verbs over MCP; does NOT own commitment dew (Kart
``CommitmentProactiveHook`` stays until proven). Honest absence when MCP off.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot.steward.config import willow_home


# Status verbs plus the two seal-driven ticks. None of these publish dew or
# seal anything: seal_drain mirrors a HUMAN's Nestor seal onto its SOIL
# record (willow-mcp seal_handler.on_seal) and advances a local offset —
# sealed decision 72292afd, "the seal watch runs on the same tick as the
# bot". net_authority_drain is its sibling for egress (sealed c8572a92 +
# 6b305258, willow-mcp #582/#584): for every task held on network authority
# whose pair the operator sealed, it asks the uid-994 signer for the
# envelope and releases the row — the seal is the operator's yes; this
# tick is only the hand. Gap 6031199ac4e1: the drain had no caller on the
# tick and the first sealed row sat held until the operator typed it.
DEFAULT_CURATED: list[tuple[str, dict[str, Any]]] = [
    ("fleet_health", {}),
    ("commitment_surface", {}),
    ("human_required_list", {}),
    ("diagnostic_summary", {}),
    ("seal_drain", {}),
    ("net_authority_drain", {}),
]

# Result fields worth carrying into the receipt verbatim (small scalars /
# short lists), so bot_status can show a tool's three-state without the
# reader opening the tool's own journal. Everything else stays keys-only.
_RECEIPT_FIELDS = ("state", "reason", "drained", "upgraded", "results", "offset_after")

# Nested fields, mirrored as dotted keys. net_authority_drain answers with two
# halves under one three-state (`tasks` and `leases`, each a receipt or None
# when that half was blind); the numbers a reader needs sit one level down.
# A half that is None mirrors nothing — the top-level `state`/`reason`
# already says it was unreachable, and an absent key is not a zero.
_RECEIPT_NESTED_FIELDS = (
    ("tasks", "held"),
    ("tasks", "counts"),
    ("tasks", "truncated"),
    ("leases", "requests"),
)


def _app_id() -> str:
    return os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"


def _receipts_path() -> Path:
    return willow_home() / "willow-bot" / "steward_heartbeat.jsonl"


def curated_calls() -> list[tuple[str, dict[str, Any]]]:
    raw = os.environ.get("WILLOW_BOT_STEWARD_TOOLS", "").strip()
    if not raw:
        app = _app_id()
        return [(name, {**args, "app_id": app}) for name, args in DEFAULT_CURATED]
    # comma-separated tool names; each gets app_id only
    app = _app_id()
    return [(n.strip(), {"app_id": app}) for n in raw.split(",") if n.strip()]


def run_heartbeat(*, enable_mcp: bool | None = None) -> dict[str, Any]:
    """Run one curated pass. Returns a receipt dict (also appended to JSONL)."""
    if enable_mcp is None:
        enable_mcp = os.environ.get("WILLOW_BOT_MCP", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    receipt: dict[str, Any] = {
        "event": "steward_heartbeat",
        "at": datetime.now(timezone.utc).isoformat(),
        "tools": [],
    }

    if not enable_mcp:
        receipt["status"] = "absent"
        receipt["detail"] = "WILLOW_BOT_MCP not enabled — no tool calls"
        _append(receipt)
        print(json.dumps(receipt), flush=True)
        return receipt

    try:
        from willow_bot.steward import mcp_client
    except ImportError as exc:
        receipt["status"] = "could-not-run"
        receipt["detail"] = f"mcp package missing: {exc}"
        _append(receipt)
        print(json.dumps(receipt), flush=True)
        return receipt

    receipt["status"] = "ok"
    for name, args in curated_calls():
        entry: dict[str, Any] = {"tool": name, "args_keys": sorted(args)}
        try:
            result = mcp_client.call(name, args)
            entry["outcome"] = "ok"
            # Keep receipt small — summarize, but carry the three-state
            # fields through so the journal says what happened, not just
            # that something answered.
            if isinstance(result, dict):
                entry["result_keys"] = sorted(result.keys())[:20]
                for field in _RECEIPT_FIELDS:
                    if field in result:
                        entry[field] = result[field]
                for outer, inner in _RECEIPT_NESTED_FIELDS:
                    half = result.get(outer)
                    if isinstance(half, dict) and inner in half:
                        entry[f"{outer}.{inner}"] = half[inner]
            else:
                entry["result_preview"] = str(result)[:240]
        except Exception as exc:  # noqa: BLE001
            entry["outcome"] = "could-not-run"
            entry["detail"] = str(exc)[:400]
        receipt["tools"].append(entry)

    _append(receipt)
    print(json.dumps(receipt), flush=True)
    return receipt


def _append(receipt: dict[str, Any]) -> None:
    path = _receipts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, separators=(",", ":")) + "\n")
