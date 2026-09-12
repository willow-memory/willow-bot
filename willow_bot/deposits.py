"""Propose-only CI / PR deposits (§12 middle row).

Always append a local draft JSONL under ``$WILLOW_HOME/willow-bot/deposits/``.
Optionally mirror into willow-mcp ``store_put`` when MCP is configured — never seals.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("willow-bot.deposits")

COLLECTION = "willow_bot_ci_deposits"


def _willow_home() -> Path:
    default = Path.home() / "sean-data-vault" / "willow-operator-box"
    return Path(os.environ.get("WILLOW_HOME", default))


def deposits_dir() -> Path:
    return _willow_home() / "willow-bot" / "deposits"


def deposits_jsonl() -> Path:
    return deposits_dir() / "ci_outcomes.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ci_outcome_record(
    *,
    repo: str,
    head_sha: str | None,
    check_run_id: Any,
    check_name: str | None,
    conclusion: str | None,
    status: str | None = None,
    pr_number: int | None = None,
    html_url: str | None = None,
    sender_type: str = "",
    received_at: str | None = None,
) -> dict[str, Any]:
    """Draft claim: how CI went for repo@sha (pass and fail both recorded)."""
    return {
        "kind": "ci_outcome",
        "lane": "draft",
        "deposited_by": "willows-bot",
        "actor_type": sender_type or "unknown",
        "repo": repo,
        "head_sha": head_sha or "",
        "check_run_id": check_run_id,
        "check_name": check_name or "",
        "conclusion": conclusion or "",
        "status": status or "",
        "pr_number": pr_number,
        "html_url": html_url or "",
        "received_at": received_at or _now(),
        "deposited_at": _now(),
    }


def record_id_for(rec: dict[str, Any]) -> str:
    repo = (rec.get("repo") or "unknown").replace("/", "__")
    sha = (rec.get("head_sha") or "nosha")[:12]
    cid = rec.get("check_run_id") or "0"
    return f"ci-{repo}-{sha}-{cid}"


def append_local(rec: dict[str, Any]) -> Path:
    path = deposits_jsonl()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return path


def deposit_ci_outcome(rec: dict[str, Any]) -> dict[str, Any]:
    """Write local draft; optionally MCP store_put. Returns status receipt."""
    path = append_local(rec)
    out: dict[str, Any] = {
        "local": str(path),
        "record_id": record_id_for(rec),
        "mcp": {"status": "skipped", "detail": "WILLOW_BOT_MCP not enabled"},
    }
    if os.environ.get("WILLOW_BOT_MCP", "").strip().lower() not in ("1", "true", "yes"):
        return out
    try:
        from willow_bot.steward import mcp_client

        app_id = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
        result = mcp_client.call(
            "store_put",
            {
                "app_id": app_id,
                "collection": COLLECTION,
                "record": rec,
                "record_id": record_id_for(rec),
                "deviation": 0,
            },
        )
        out["mcp"] = {"status": "ok", "result": result}
    except Exception as exc:  # noqa: BLE001 — honest absence, never silent success
        log.warning("mcp store_put failed: %s", exc)
        out["mcp"] = {"status": "could-not-run", "detail": str(exc)}
    return out


def deposit_from_check_run_payload(payload: dict) -> dict[str, Any] | None:
    """Build + deposit from a completed check_run webhook payload."""
    if payload.get("action") != "completed":
        return None
    check = payload.get("check_run") or {}
    repo = (payload.get("repository") or {}).get("full_name") or ""
    if not repo:
        return None
    prs = check.get("pull_requests") or []
    pr_number = prs[0].get("number") if prs else None
    sender_type = str((payload.get("sender") or {}).get("type") or "")
    rec = ci_outcome_record(
        repo=repo,
        head_sha=check.get("head_sha"),
        check_run_id=check.get("id"),
        check_name=check.get("name"),
        conclusion=check.get("conclusion"),
        status=check.get("status"),
        pr_number=pr_number,
        html_url=check.get("html_url"),
        sender_type=sender_type,
    )
    return deposit_ci_outcome(rec)
