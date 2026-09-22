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
    # envelope_retire_sweep (sealed decision 83faa340; gap 4c7512c57a7e):
    # revokes an active envelope whose bounds named a branch now merged and
    # gone, or whose max_count FRANK shows spent. Same standing as the two
    # above: mints no new authority, gate does not depend on it. dry_run
    # False here — the steward tick IS the unattended sweep the sealed
    # spec names; the desk calls the tool by hand with the True default to
    # prove one pass without writing anything.
    ("envelope_retire_sweep", {"dry_run": False}),
]

# Result fields worth carrying into the receipt verbatim (small scalars /
# short lists), so bot_status can show a tool's three-state without the
# reader opening the tool's own journal. Everything else stays keys-only.
_RECEIPT_FIELDS = ("state", "reason", "drained", "upgraded", "results", "offset_after",
                   "examined", "kept_standing", "dry_run", "truncated", "cursor")

#: `unreachable`, `retired`, and `kept_in_force` are deliberately NOT in
#: `_RECEIPT_FIELDS` above — all three are per-row lists that can grow
#: unbounded (gap b7a4ccdc8bbb, measured live: a first-tick
#: envelope_retire_sweep receipt carried 187 `retired` + 139
#: `kept_in_force` + 20 `unreachable` rows, one heartbeat row ~40 KB —
#: bigger than the 8 KiB tail `willow_bot.status._read_last_receipt` used
#: to read, so `bot_status` reported the whole heartbeat file
#: `unreachable`). Only each list's COUNT rides the heartbeat entry, via
#: this map (`result[key]` -> `entry[value]`, `len(...)` not the list
#: itself); `retired` additionally gets a bounded ID sample so an operator
#: reading the heartbeat at a glance sees WHAT got retired, not just how
#: many — the full receipt (every row, every `why`) is appended whole to
#: its own file every tick regardless of size, see `_append_full_receipt`.
_COUNT_ONLY_FIELDS = {"unreachable": "unreachable_count", "kept_in_force": "kept_in_force_count"}

#: How many `retired` ids to sample into the heartbeat entry (`_id_sample`
#: below) — enough to be useful at a glance, small enough to never be the
#: reason a row exceeds the tail reader's window on its own.
_RETIRED_SAMPLE_N = 10

# Nested fields, mirrored as dotted keys. net_authority_drain answers with two
# halves under one three-state (`tasks` and `leases`, each a receipt or None
# when that half was blind); the numbers a reader needs sit one level down.
# A half that is None mirrors nothing — the top-level `state`/`reason`
# already says it was unreachable, and an absent key is not a zero. Each
# half's own `state` rides too, so a missing `tasks.held` reads as "that
# half was None" only when `tasks.state` is also missing; a present
# `tasks.state` with no `held` would be an upstream field rename, not a
# blind half (Loki 09922563).
_RECEIPT_NESTED_FIELDS = (
    ("tasks", "state"),
    ("tasks", "held"),
    ("tasks", "counts"),
    ("tasks", "truncated"),
    ("leases", "state"),
    ("leases", "requests"),
)


def _app_id() -> str:
    return os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"


def _receipts_path() -> Path:
    return willow_home() / "willow-bot" / "steward_heartbeat.jsonl"


def _full_receipts_path() -> Path:
    """Where a curated tool's FULL, unsummarized result lands every tick,
    regardless of size — the pattern the steward already uses for
    ``steward_ticks.jsonl`` vs the heartbeat file: a small, always-readable
    heartbeat row that names what happened, and a separate file that keeps
    everything. Gap b7a4ccdc8bbb."""
    return willow_home() / "willow-bot" / "steward_heartbeat_receipts.jsonl"


def _append_full_receipt(*, tool: str, at: str, result: dict[str, Any]) -> None:
    row = {"tool": tool, "at": at, "result": result}
    path = _full_receipts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


#: Fast lookup of each curated tool's own args (e.g. envelope_retire_sweep's
#: `dry_run: False`), keyed by name — used below so narrowing the curated
#: list via WILLOW_BOT_STEWARD_TOOLS does not silently drop a known tool's
#: non-default args (rework of Loki's LOW finding on BAA43543/9494D3AF: an
#: env-narrowed list handed every named tool bare `{app_id}`, which for
#: envelope_retire_sweep meant losing `dry_run=False` and calling it with
#: the tool's own True default instead — a dry run standing in for the live
#: tick with no error, no receipt difference an operator would notice at a
#: glance).
_DEFAULT_ARGS_BY_NAME = dict(DEFAULT_CURATED)


def curated_calls() -> list[tuple[str, dict[str, Any]]]:
    raw = os.environ.get("WILLOW_BOT_STEWARD_TOOLS", "").strip()
    app = _app_id()
    if not raw:
        return [(name, {**args, "app_id": app}) for name, args in DEFAULT_CURATED]
    # comma-separated tool names: a name this module knows keeps its own
    # curated args (so dry_run=False etc. survive narrowing); an unknown
    # name gets bare {app_id}, same as before.
    return [
        (n.strip(), {**_DEFAULT_ARGS_BY_NAME.get(n.strip(), {}), "app_id": app})
        for n in raw.split(",") if n.strip()
    ]


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
            # A gate/permission refusal comes back as an ordinary dict
            # (mcp_client.call does not raise on it), shaped {"error": ...}
            # with no three-state "state" field — the verb-absent /
            # transport-failure case (an exception) is already honest via
            # "could-not-run" below; this is the other way a call can fail
            # without raising. Rework of Loki's LOW finding on
            # BAA43543/9494D3AF: this used to fall straight through to
            # outcome="ok" with nothing but {"error"} in result_keys — a
            # denied call read identically to a successful one at a glance.
            if isinstance(result, dict) and "error" in result and "state" not in result:
                entry["outcome"] = "denied"
                entry["error"] = str(result["error"])[:300]
                entry["result_keys"] = sorted(result.keys())[:20]
                receipt["tools"].append(entry)
                continue
            entry["outcome"] = "ok"
            # Keep receipt small — summarize, but carry the three-state
            # fields through so the journal says what happened, not just
            # that something answered. The FULL result (every row, every
            # `why`) is appended whole to its own file every tick,
            # regardless of size — gap b7a4ccdc8bbb: a heartbeat row must
            # stay small enough for the tail reader, no exceptions, so
            # nothing here is a judgment call about what is "too big" per
            # tool; the split is unconditional.
            if isinstance(result, dict):
                _append_full_receipt(tool=name, at=receipt["at"], result=result)
                entry["full_receipt"] = {"file": _full_receipts_path().name, "at": receipt["at"]}
                entry["result_keys"] = sorted(result.keys())[:20]
                for field in _RECEIPT_FIELDS:
                    if field in result:
                        entry[field] = result[field]
                for src, dest in _COUNT_ONLY_FIELDS.items():
                    val = result.get(src)
                    if isinstance(val, list):
                        entry[dest] = len(val)
                # `retired` gets a bounded ID sample on top of its count
                # (rework of gap b7a4ccdc8bbb finding 1: "the first N
                # retired ids" belongs at a glance, not just the count) —
                # never the `why`/`reason` text, and never more than
                # `_RETIRED_SAMPLE_N` rows regardless of how large the
                # real list is.
                retired = result.get("retired")
                if isinstance(retired, list):
                    entry["retired_count"] = len(retired)
                    entry["retired_sample"] = [
                        row.get("id") for row in retired[:_RETIRED_SAMPLE_N]
                        if isinstance(row, dict)
                    ]
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
