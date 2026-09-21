"""Propose-only CI / PR deposits (§12 middle row).

Always append a local draft JSONL under ``$WILLOW_HOME/willow-bot/deposits/``.
Optionally mirror into willow-mcp ``store_put`` when MCP is configured — never seals.

Gap ``a6c0926d7e83`` (hash chain): every row appended by ``append_local``
carries ``prev_hash`` (the previous chained row's ``row_hash``, or the
genesis constant when this row starts the chain) and ``row_hash``
(``sha256`` over the row's other fields in canonical form, plus the
prev). ``verify_chain(path)`` walks the file and confirms each chained
row's prev_hash equals the previous chained row's row_hash, so an edit
to a historical row breaks the chain at the tampered row's successor.
Legacy rows without hashes (rows written before this step existed) are
tolerated: the verifier reports ``chained_from`` and counts what it
verified from there. A sidecar file ``ci_outcomes.chain.tip`` holds the
current tip hash so an append is O(1); a missing tip file is rebuilt on
the next append by scanning the tail of the deposits file.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot.paths import deposits_dir as _paths_deposits_dir

log = logging.getLogger("willow-bot.deposits")

COLLECTION = "willow_bot_ci_deposits"

# All-zero hash starts the chain — a legacy tail with no hashes leaves the
# tip file absent and the first chained row uses GENESIS_HASH as prev.
GENESIS_HASH = "0" * 64

# Reserved keys: never in the canonical form the row_hash is computed over.
_CHAIN_KEYS = frozenset({"prev_hash", "row_hash"})


def deposits_dir() -> Path:
    return _paths_deposits_dir()


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
    head_branch: str | None = None,
) -> dict[str, Any]:
    """Draft claim: how CI went for repo@sha (pass and fail both recorded).

    ``head_branch`` is the check suite's branch (gap 52928edb3fc7): for a
    head with no PR (a release commit pushed to master) it is the only key
    under which a *successor* head can be found, so the ci step can tell a
    cancelled-because-superseded run from a stuck one. Empty when the
    payload did not carry it — a row written before this field existed
    reads the same as one GitHub sent without a suite.
    """
    return {
        "kind": "ci_outcome",
        "lane": "draft",
        "deposited_by": "willows-bot",
        "actor_type": sender_type or "unknown",
        "repo": repo,
        "head_sha": head_sha or "",
        "head_branch": head_branch or "",
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


def _tip_path() -> Path:
    return deposits_dir() / "ci_outcomes.chain.tip"


def _canonical(rec: dict[str, Any]) -> bytes:
    """Deterministic bytes for the row_hash. Sort keys so a re-ordering of
    the input dict does not change the hash; separators dropped so
    whitespace does not either. Chain keys are excluded — the row_hash
    covers the row's data, not itself."""
    body = {k: v for k, v in rec.items() if k not in _CHAIN_KEYS}
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def compute_row_hash(rec: dict[str, Any], prev_hash: str) -> str:
    """The row_hash covers the canonical body plus the prev_hash, so a
    tampered historical row breaks the chain at its successor (the
    successor's stored prev_hash no longer matches the tampered row's
    recomputed row_hash)."""
    h = hashlib.sha256()
    h.update(_canonical(rec))
    h.update(b"\x00")  # a byte the JSON body can never contain (json escapes it)
    h.update(prev_hash.encode("ascii"))
    return h.hexdigest()


def _last_chained_hash_from_tail(path: Path, *, tail_bytes: int = 65536) -> str | None:
    """Scan the tail of the deposits file for the last row carrying
    ``row_hash``. Bounded read: a very old file with a legacy head can
    still have its recent chained tail read quickly. Returns None when
    no chained row is found in the tail — the caller starts the chain
    at GENESIS."""
    if not path.is_file():
        return None
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - tail_bytes))
        lines = fh.read().splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("row_hash"), str):
            return rec["row_hash"]
    return None


def _read_tip(path: Path) -> str:
    """Return the current chain tip: the tip file's contents when present,
    otherwise the last row_hash in the deposits file tail, otherwise
    GENESIS_HASH. A tip file that disagrees with the deposits tail (rare:
    a partial write, an operator hand-edit) is IGNORED in favour of the
    deposits file, which is the source of truth."""
    tail = _last_chained_hash_from_tail(deposits_jsonl())
    if tail is not None:
        return tail
    if path.is_file():
        try:
            v = path.read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            pass
    return GENESIS_HASH


def _write_tip(path: Path, tip_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tip_hash + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_local(rec: dict[str, Any]) -> Path:
    """Append a row to ``ci_outcomes.jsonl`` with a chained hash.

    The row is mutated in place with ``prev_hash`` and ``row_hash`` set,
    so a caller inspecting the returned rec sees what was written. The
    tip sidecar is updated after the row lands; a crash between the two
    writes leaves the deposits file as truth and the next append recovers
    the tip from the tail scan.
    """
    path = deposits_jsonl()
    path.parent.mkdir(parents=True, exist_ok=True)
    tip_path = _tip_path()
    prev_hash = _read_tip(tip_path)
    row_hash = compute_row_hash(rec, prev_hash)
    rec["prev_hash"] = prev_hash
    rec["row_hash"] = row_hash
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    _write_tip(tip_path, row_hash)
    return path


def verify_chain(path: Path) -> dict[str, Any]:
    """Walk ``ci_outcomes.jsonl`` and check the hash chain from the first
    chained row to the last.

    Returns a receipt: ``{present, lines_total, legacy_head, chained_from,
    verified, broken_at, detail}``. ``broken_at`` is None on a good
    chain; when non-None it names the 1-indexed line where the chain
    breaks (a row's prev_hash did not match the previous row's row_hash,
    or a row's row_hash did not match the recomputed value). A file with
    no chained rows returns ``chained_from=None`` and ``verified=0`` —
    honest absence, not a broken chain.
    """
    receipt: dict[str, Any] = {
        "present": path.is_file(),
        "lines_total": 0,
        "legacy_head": 0,
        "chained_from": None,
        "verified": 0,
        "broken_at": None,
        "tip": None,
    }
    if not path.is_file():
        return receipt

    prev_hash: str | None = None  # None until we see the first chained row
    lineno = 0
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            lineno += 1
            receipt["lines_total"] = lineno
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                receipt["broken_at"] = lineno
                receipt["detail"] = f"line {lineno} is not JSON"
                return receipt
            if not isinstance(rec, dict):
                receipt["broken_at"] = lineno
                receipt["detail"] = f"line {lineno} is not an object"
                return receipt
            stored_row = rec.get("row_hash")
            stored_prev = rec.get("prev_hash")
            if not (isinstance(stored_row, str) and isinstance(stored_prev, str)):
                if prev_hash is None:
                    receipt["legacy_head"] = lineno
                    continue
                # A legacy row appearing INSIDE the chain (after chained
                # rows started) is a break — a chain cannot resume from a
                # row without a hash.
                receipt["broken_at"] = lineno
                receipt["detail"] = f"line {lineno} has no row_hash but chain started at {receipt['chained_from']}"
                return receipt
            # First chained row: prev_hash must be GENESIS or match the
            # last legacy row's implicit continuation — we accept GENESIS
            # or any specific prev the row claims for a fresh chain,
            # since the legacy head has no hash to compare against.
            if prev_hash is None:
                receipt["chained_from"] = lineno
                prev_hash = stored_prev  # accept whatever the first row claims
            else:
                if stored_prev != prev_hash:
                    receipt["broken_at"] = lineno
                    receipt["detail"] = (f"line {lineno} prev_hash={stored_prev[:12]}… "
                                          f"does not match previous row_hash={prev_hash[:12]}…")
                    return receipt
            recomputed = compute_row_hash(rec, stored_prev)
            if recomputed != stored_row:
                receipt["broken_at"] = lineno
                receipt["detail"] = (f"line {lineno} row_hash={stored_row[:12]}… "
                                      f"does not match recomputed={recomputed[:12]}…")
                return receipt
            prev_hash = stored_row
            receipt["verified"] += 1

    receipt["tip"] = prev_hash
    return receipt


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
    suite = check.get("check_suite") if isinstance(check.get("check_suite"), dict) else {}
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
        head_branch=suite.get("head_branch"),
    )
    return deposit_ci_outcome(rec)
