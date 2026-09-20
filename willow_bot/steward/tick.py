"""One steward tick: inbox → scan open PRs → optional host merge sync → JSON events.

Emits AGENT_LOOP_TICK_PR_AUDIT when run with --loop (desk wake). Never calls Grove 3B.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from willow_bot.steward import inbox as inbox_mod
from willow_bot.steward import merge as merge_mod
from willow_bot.steward import scan as scan_mod
from willow_bot.steward.config import host_sync_enabled, state_path


_DEFAULT_PROMPT = (
    "Steward PR watch tick (Cursor seat — not Grove 3B watcher). "
    "JSON lines above are from willow-bot-steward. Treat webhook_pr as willow-bot "
    "fleet_bridge hints. new_pr rows are dispatched to Loki by the audit step "
    "(steward_audit line); steward_sweep says what came home; steward_resolve which "
    "backlog gaps those merges named (Gap-Id: trailers); steward_mirror what "
    "reached the store; steward_ci which checks went red and were filed for review. "
    "Do not post to Grove; Grove Loki is separate. Brief tick summary."
)


def _load_or_init_state(path: Path, open_keys: list[str]) -> dict:
    if path.is_file() and path.read_text().strip():
        return json.loads(path.read_text())
    return {
        "seen": list(open_keys),
        "open": list(open_keys),
        "merged_synced": [],
        "inbox_consumed": [],
        "webhook_signals": [],
        "pending_audit": [],
        "audit_dispatched": {},
    }


def run_once(*, do_host_sync: bool | None = None) -> int:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Capture scan lines
    from io import StringIO
    from contextlib import redirect_stdout

    buf = StringIO()
    with redirect_stdout(buf):
        scan_mod.main()
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]

    keys: list[str] = []
    meta: dict[str, str] = {}
    for line in lines:
        key, rest = line.split("|", 1)
        title, url = rest.rsplit("|", 1)
        keys.append(key)
        meta[key] = f"{title}|{url}"

    state = _load_or_init_state(path, keys)
    path.write_text(json.dumps(state, indent=2) + "\n")

    rc = inbox_mod.ingest(path)
    if rc != 0:
        print(json.dumps({"event": "error", "detail": "inbox ingest failed"}), flush=True)
        return rc

    sync = host_sync_enabled() if do_host_sync is None else do_host_sync
    if sync:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(keys, tmp)
            tmp_path = tmp.name
        try:
            old_argv = sys.argv
            sys.argv = ["merge", str(path), tmp_path]
            try:
                merge_mod.main()
            finally:
                sys.argv = old_argv
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    state = json.loads(path.read_text()) if path.is_file() else {}
    seen = list(state.get("seen") or [])
    new_keys = [k for k in keys if k not in seen]
    merged_seen: list[str] = []
    u: set[str] = set()
    for k in seen + keys:
        if not k or k in u:
            continue
        u.add(k)
        merged_seen.append(k)

    state["seen"] = merged_seen
    state["open"] = keys
    # A new PR is a pending audit until the audit step has dispatched it —
    # persisted, so a tick that dies between scan and dispatch loses nothing.
    pending = list(state.get("pending_audit") or [])
    dispatched = state.get("audit_dispatched") or {}
    for k in new_keys:
        if k not in pending and k not in dispatched:
            title_url = meta.get(k, "|")
            title, url = title_url.split("|", 1) if "|" in title_url else (title_url, "")
            pending.append({"repo_pr": k, "title": title, "url": url})
    state["pending_audit"] = pending
    path.write_text(json.dumps(state, indent=2) + "\n")

    for k in new_keys:
        title_url = meta.get(k, "|")
        title, url = title_url.split("|", 1) if "|" in title_url else (title_url, "")
        print(
            json.dumps(
                {"event": "new_pr", "repo_pr": k, "title": title, "url": url},
            ),
            flush=True,
        )
    return 0


# ── the audit step: a new PR becomes Loki's packet ───────────────────────────

_AUDIT_PER_TICK = 5


def _audit_brief(item: dict) -> str:
    key, title, url = item.get("repo_pr", ""), item.get("title", ""), item.get("url", "")
    repo, _, num = key.rpartition("#")
    return (
        f"# Audit {key}\n\n"
        f"**{title}**\n{url}\n\n"
        "Adversarial review of this pull request, in your register: name what "
        "the PR promises, what the diff delivers, and the distance between "
        "them. Specific findings with file and line; no summary of the diff "
        "back to its author.\n\n"
        "Read, do not build:\n"
        f"- `integration_call(name='github', method='GET', path='/repos/{repo}/pulls/{num}')` "
        "for the body and the head sha;\n"
        f"- `integration_call(name='github', method='GET', path='/repos/{repo}/pulls/{num}/files')` "
        "for the diff;\n"
        f"- `store_search(collection='willow_bot_ci_deposits', query='{repo}')` for the "
        "CI outcomes the bot deposited for its head sha.\n\n"
        "Hold the body to the org PR template (Bite / What was done / Evidence / "
        "Out of scope / Next bite) and say which sections are missing or empty. "
        "Hold Evidence to receipts: a count with no command is a claim.\n\n"
        "Close with `handoff_write_v4` to willow: findings ranked, most severe "
        "first; `no findings` is a finding only when you say what you checked."
    )


def run_audit(*, enable_mcp: bool | None = None) -> dict:
    """Dispatch every pending new PR to Loki as an audit packet.

    One `dispatch_send(to_app='loki', role='auditor')` per PR — the call args
    the verb gate cites are `{to_agents: loki, task_class: auditor}`, which
    is the bounds of the standing dispatch envelope. Idempotent per repo#pr
    through the state file: a PR moves from `pending_audit` to
    `audit_dispatched[key] = dispatch_id` only on success, and a refusal
    leaves it pending with the reason so the next tick tries again. Capped
    per tick so a cold start does not flood the desk. Honest absence when
    MCP is off.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_audit", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    pending = list(state.get("pending_audit") or [])
    receipt["pending"] = len(pending)
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no dispatch")
        return _emit(receipt)
    if not pending:
        receipt.update(status="ok", dispatched=[], refused=[])
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    dispatched = dict(state.get("audit_dispatched") or {})
    done, refused, still = [], [], []
    for item in pending[:_AUDIT_PER_TICK]:
        key = item.get("repo_pr", "")
        try:
            result = mcp_client.call("dispatch_send", {
                "app_id": app,
                "to_app": "loki",
                "role": "auditor",
                "summary": f"Audit {key}: {item.get('title', '')}"[:200],
                "assignment_md": _audit_brief(item),
                "context_refs": [item.get("url", "")],
                "reply_to": "willow",
                "phase": "operate",
                "priority": "normal",
            })
        except Exception as exc:  # noqa: BLE001 — a refused dispatch stays pending, with its reason
            item["last_error"] = str(exc)[:300]
            refused.append({"repo_pr": key, "error": item["last_error"]})
            still.append(item)
            continue
        err = _tool_error(result)
        did = None if err else (result.get("dispatch_id") if isinstance(result, dict) else None)
        if not did:
            item["last_error"] = err or f"no dispatch_id in result: {str(result)[:200]}"
            refused.append({"repo_pr": key, "error": item["last_error"]})
            still.append(item)
            continue
        dispatched[key] = did
        done.append({"repo_pr": key, "dispatch_id": did})
    still.extend(pending[_AUDIT_PER_TICK:])
    state["pending_audit"] = still
    state["audit_dispatched"] = dispatched
    path.write_text(json.dumps(state, indent=2) + "\n")
    receipt.update(status="ok", dispatched=done, refused=refused, remaining=len(still))
    return _emit(receipt)


# ── the mirror step: local CI deposits reach the store ───────────────────────

_MIRROR_PER_TICK = 200


def _mirror_offset_path() -> Path:
    from willow_bot.deposits import deposits_dir

    return deposits_dir() / "mirror.offset"


def run_mirror(*, enable_mcp: bool | None = None) -> dict:
    """Mirror new rows of ``deposits/ci_outcomes.jsonl`` into the store.

    The webhook unit writes every check outcome locally and — because it
    runs without MCP — never mirrors: 506 local rows against 6 in
    ``willow_bot_ci_deposits`` on 2026-09-14. This step reads from a byte
    offset, ``store_put``s each new row under its ``record_id_for`` (so a
    re-run is an overwrite, not a duplicate), and advances the offset only
    past rows that landed. Capped per tick. Honest absence when MCP is off.
    """
    from willow_bot.deposits import COLLECTION, deposits_jsonl, record_id_for

    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_mirror", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    src = deposits_jsonl()
    if not src.is_file():
        receipt.update(status="ok", present=False, mirrored=0, detail=f"no deposits file at {src}")
        return _emit(receipt)
    off_path = _mirror_offset_path()
    offset = int(off_path.read_text().strip() or 0) if off_path.is_file() else 0
    size = src.stat().st_size
    if offset > size:
        offset = 0  # the file was truncated or rotated; start over, overwrites are idempotent
    receipt.update(present=True, offset=offset, size=size)
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no store_put",
                       behind=size - offset)
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    mirrored, failed, paced = 0, None, 0
    deadline = _clock() + _MIRROR_TIME_BUDGET_S
    with src.open("rb") as fh:
        fh.seek(offset)
        while mirrored < _MIRROR_PER_TICK:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break  # a row still being written; next tick
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                offset = fh.tell()
                continue
            rec = json.loads(text)
            args = {"app_id": app, "collection": COLLECTION, "record": rec,
                    "record_id": record_id_for(rec), "deviation": 0}
            # willow-mcp meters every app at 60/min with a burst of 10 and
            # answers the 11th call {"error": "rate_limited", "retry_after": N}.
            # The first live mirror stopped there with 7 rows landed (2026-09-15
            # 00:28Z). Pace instead: wait what the limiter asks, retry the same
            # row, inside a time budget per tick — the row is never skipped.
            while True:
                try:
                    result = mcp_client.call("store_put", args)
                except Exception as exc:  # noqa: BLE001 — stop at the first failure; the offset stays before it
                    failed = str(exc)[:300]
                    break
                err = _tool_error(result)
                if err is None:
                    break
                if err != "rate_limited":
                    failed = err
                    break
                wait = min(max(int(result.get("retry_after") or 1), 1), _MIRROR_MAX_WAIT_S)
                if _clock() + wait > deadline:
                    failed = f"rate_limited (paced {paced}x; time budget spent, resumes next tick)"
                    break
                paced += 1
                _sleep(wait)
            if failed is not None:
                break
            mirrored += 1
            offset = fh.tell()
    off_path.parent.mkdir(parents=True, exist_ok=True)
    off_path.write_text(f"{offset}\n", encoding="utf-8")
    behind = size - offset
    status = "ok" if failed is None else ("paced" if failed.startswith("rate_limited") else "could-not-run")
    receipt.update(status=status, mirrored=mirrored, paced=paced, new_offset=offset, behind=behind)
    if failed is not None:
        receipt["detail"] = failed
    return _emit(receipt)


# Pacing knobs, module-level so a test can shrink them. The store meters at
# 60 calls/min per app (burst 10); ~100 rows a tick is what two minutes
# buys once the burst is spent, and a 500-row backlog drains in five ticks.
_MIRROR_TIME_BUDGET_S = 120.0
_MIRROR_MAX_WAIT_S = 10
_clock = time.monotonic
_sleep = time.sleep


# ── the ci step: a red check reaches a seat ─────────────────────────────────

# Conclusions that mean "this leg failed". `cancelled` is NOT in (gap
# 25cb3c1a3489): GitHub cancels a run when the next push supersedes it
# (`concurrency: cancel-in-progress` in the fleet's tests.yml), and every
# release-please cycle produced three to four such legs — filed as reds,
# they drowned the real ones. A cancelled leg is its own state, decided
# by whether a successor head shows up (see ``_decide_cancelled``).
_CI_RED = frozenset({"failure", "timed_out", "startup_failure"})
_CI_CANCELLED = "cancelled"
# Conclusions that mean "this leg passed or did not count". A head whose
# every recorded leg is one of these, with at least one `success`, is green.
_CI_GREEN = frozenset({"success", "skipped", "neutral"})
_CI_PER_TICK = 50

# A cancelled leg with no successor head after this long is a stuck PR,
# not a superseded run, and IS worth an item. Ten minutes: release-please
# pushes its release commit within a minute or two of the merge and the
# superseding run starts at once, so ten covers a queued runner without
# leaving a genuinely stuck PR silent for long. Env-overridable.
_CI_CANCELLED_GRACE_ENV = "WILLOW_BOT_CI_CANCELLED_GRACE_MIN"
_CI_CANCELLED_GRACE_MIN_DEFAULT = 10

# The aggregate job in the fleet's tests.yml (`test`, `needs: test-matrix,
# if: always()`) fails BECAUSE a leg failed. It is folded into that head's
# item as an aggregate leg, never counted as a cause of its own.
_CI_AGGREGATE_CHECKS = frozenset({"test"})

_ci_clock = time.time


def _ci_offset_path() -> Path:
    from willow_bot.deposits import deposits_dir

    return deposits_dir() / "ci.offset"


def _ci_key(rec: dict) -> str:
    return f"{rec.get('head_sha', '')}:{rec.get('check_run_id', '')}"


def _ci_pr_key(repo: str, pr: object, head_sha: str) -> str:
    """`repo#pr` when the row names a PR, else `repo@sha12` — the same
    spelling the filing's title carries."""
    return f"{repo}#{pr}" if pr else f"{repo}@{head_sha[:12]}"


def _ci_head_key(repo: str, pr: object, head_sha: str) -> str:
    """The dedupe key for one filing: one review item per (repo, pr, head_sha)."""
    return f"{_ci_pr_key(repo, pr, head_sha)}@{head_sha}"


def _ci_grace_s() -> float:
    raw = os.environ.get(_CI_CANCELLED_GRACE_ENV, "").strip()
    try:
        minutes = float(raw) if raw else float(_CI_CANCELLED_GRACE_MIN_DEFAULT)
    except ValueError:
        minutes = float(_CI_CANCELLED_GRACE_MIN_DEFAULT)
    return max(minutes, 0.0) * 60.0


def _ci_epoch(value: object) -> float | None:
    """The deposit's `received_at` (ISO-8601, UTC) as epoch seconds, or
    None when it cannot be read — the third state, not zero."""
    if not isinstance(value, str) or not value.strip():
        return None
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _decide_cancelled(pending: dict, heads: dict, *, now: float, grace_s: float) -> dict:
    """Each pending cancelled leg gets exactly one of four states.

    * ``superseded`` — a different head for the same PR was first seen
      AFTER this one: the run was cancelled because the next push landed.
      Dropped, never filed.
    * ``waiting`` — no successor yet and the leg is younger than the grace
      window. Stays pending; the next tick decides again.
    * ``stuck`` — no successor and older than the grace window: nothing
      superseded it, so the PR is sitting on a cancelled run. Filed, in
      the same per-head item a red leg would be.
    * ``unreachable`` — the deposit's timestamp cannot be read, so its age
      is unknowable. Stays pending and says so; it neither files nor
      clears, which is what an unreadable input earns.

    Successor evidence is the bot's own deposits (``heads`` maps
    `repo#pr` → {head_sha: first_seen_epoch}); no GitHub API is asked, so
    there is no fifth "API failed" state to report.
    """
    out: dict[str, dict] = {}
    for key, leg in pending.items():
        pr_key = _ci_pr_key(leg["repo"], leg["pr"], leg["head_sha"])
        seen = heads.get(pr_key) or {}
        mine = seen.get(leg["head_sha"])
        if mine is None:
            mine = _ci_epoch(leg.get("received_at"))
        if mine is None:
            out[key] = {**leg, "state": "unreachable"}
            continue
        later = [sha for sha, at in seen.items() if sha != leg["head_sha"] and at is not None and at > mine]
        if later:
            out[key] = {**leg, "state": "superseded", "successor": sorted(later, key=lambda s: seen[s])[-1]}
        elif now - mine >= grace_s:
            out[key] = {**leg, "state": "stuck"}
        else:
            out[key] = {**leg, "state": "waiting"}
    return out


def _leg_of(rec: dict) -> dict:
    return {
        "repo": rec.get("repo", ""), "pr": rec.get("pr_number"),
        "head_sha": rec.get("head_sha", ""), "check": rec.get("check_name", ""),
        "conclusion": rec.get("conclusion"), "url": rec.get("html_url", ""),
        "received_at": rec.get("received_at", ""), "key": _ci_key(rec),
    }


def _public(leg: dict) -> dict:
    return {k: v for k, v in leg.items() if k not in ("key", "received_at")}


def _filing_args(app: str, where: str, head_sha: str, legs: list[dict]) -> dict:
    causes = [lg for lg in legs if lg["check"] not in _CI_AGGREGATE_CHECKS] or legs
    names = ", ".join(lg["check"] for lg in causes)
    lines = [f"{lg['check']} concluded {lg['conclusion']}"
             + (" (aggregate)" if lg["check"] in _CI_AGGREGATE_CHECKS and lg not in causes else "")
             + f". {lg['url']}" for lg in legs]
    return {
        "app_id": app, "kind": "review", "priority": "normal",
        "title": f"CI red: {where} — {len(causes)} leg(s): {names}"[:200],
        "summary": f"head {head_sha}\n" + "\n".join(lines),
        "source_ref": causes[0]["url"] or legs[0]["url"],
    }


def run_ci(*, enable_mcp: bool | None = None) -> dict:
    """File every red head the bot deposited since the last tick — one
    review item per (repo, pr, head_sha) — hold cancelled legs until a
    successor shows or the grace window passes, and resolve a PR's older
    items when a later head goes green.

    Gap 8d1bcb2b7c02: the bot records a red check faithfully and reports
    it to nobody. This step reads ``deposits/ci_outcomes.jsonl`` from its
    own byte offset (the mirror's shape, its own offset file so the two
    never race) — the bot's own deposits as the only source, no lease, no
    ``gh``.

    Gap 25cb3c1a3489, the three asks: (1) ``cancelled`` is its own state
    (``_decide_cancelled``), reported under ``cancelled`` in the receipt
    and filed only when stuck; (2) legs collapse into one item per head,
    keyed ``repo#pr@head_sha`` in ``ci_items``, the aggregate ``test``
    job folded into its cause's item; (3) when a later head for the same
    PR is fully green (every recorded leg in ``_CI_GREEN``, at least one
    ``success``), the PR's older items are resolved through
    ``human_required_resolve`` with ``superseded by <sha>, green at <ts>``.

    Idempotent per leg through ``ci_filed`` (``head_sha:check_run_id`` →
    item id, the shape ``voice`` reads by sha prefix) and per head through
    ``ci_items``: a re-delivered completion, a rotated file, or a second
    leg on an already-filed head files nothing new — the extra leg is
    remembered against the same item and reported as ``appended``. A
    refused enqueue leaves the offset before that row and reports the
    reason; the next tick retries. Reds are reported in the receipt even
    when MCP is off — the seat reading the receipt file still learns what
    went red, it just is not filed.
    """
    from willow_bot.deposits import deposits_jsonl

    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_ci", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    src = deposits_jsonl()
    if not src.is_file():
        receipt.update(status="ok", present=False, red=[], cancelled=[], filed=[], resolved=[],
                       detail=f"no deposits file at {src}")
        return _emit(receipt)
    off_path = _ci_offset_path()
    size = src.stat().st_size
    if off_path.is_file():
        offset = int(off_path.read_text().strip() or 0)
        if offset > size:
            offset = 0  # truncated or rotated; ci_filed keeps a re-read from filing twice
    else:
        # First run starts at EOF, as the seal watcher does. The live file
        # held 500+ historical rows on 2026-09-15; walking it from 0 filed
        # three stale reds (one a test fixture) before the limiter stopped it,
        # and would have kept filing old failures for ten ticks. A red that
        # happened before this step existed is not this step's to raise.
        offset = size
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
        receipt["first_run_skipped_bytes"] = size
    receipt.update(present=True, offset=offset, size=size)

    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    filed_before = dict(state.get("ci_filed") or {})
    items = {k: dict(v) for k, v in (state.get("ci_items") or {}).items()}
    heads = {k: dict(v) for k, v in (state.get("ci_heads") or {}).items()}
    head_legs = {k: dict(v) for k, v in (state.get("ci_head_legs") or {}).items()}
    cancelled_pending = {k: dict(v) for k, v in (state.get("ci_cancelled_pending") or {}).items()}

    # Read first: every row is in the receipt whether or not it can be filed.
    red: list[dict] = []
    scanned = 0
    with src.open("rb") as fh:
        fh.seek(offset)
        while scanned < _CI_PER_TICK:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break  # a row still being written; next tick
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                try:
                    rec = json.loads(text)
                except json.JSONDecodeError:
                    rec = None
                if isinstance(rec, dict) and rec.get("head_sha"):
                    leg = _leg_of(rec)
                    pr_key = _ci_pr_key(leg["repo"], leg["pr"], leg["head_sha"])
                    head_key = _ci_head_key(leg["repo"], leg["pr"], leg["head_sha"])
                    at = _ci_epoch(leg["received_at"])
                    # Every head the bot has seen for this PR, oldest first —
                    # the successor evidence for a cancelled leg.
                    seen = heads.setdefault(pr_key, {})
                    if leg["head_sha"] not in seen or (at is not None and seen[leg["head_sha"]] is None):
                        seen[leg["head_sha"]] = at
                    # Latest conclusion per leg name per head — the green test.
                    head_legs.setdefault(head_key, {})[leg["check"]] = leg["conclusion"]
                    if leg["conclusion"] in _CI_RED:
                        red.append(leg)
                    elif leg["conclusion"] == _CI_CANCELLED and leg["key"] not in filed_before:
                        cancelled_pending.setdefault(leg["key"], leg)
                scanned += 1
            offset = fh.tell()
    receipt["red"] = [_public(r) for r in red]

    decided = _decide_cancelled(cancelled_pending, heads, now=_ci_clock(), grace_s=_ci_grace_s())
    receipt["cancelled"] = [
        {"repo": d["repo"], "pr": d["pr"], "head_sha": d["head_sha"], "leg": d["check"], "state": d["state"],
         **({"successor": d["successor"]} if "successor" in d else {})}
        for d in decided.values()
    ]
    stuck = [d for d in decided.values() if d["state"] == "stuck"]
    # Superseded legs leave the pending set for good; waiting/unreachable stay.
    cancelled_pending = {k: {kk: vv for kk, vv in d.items() if kk not in ("state", "successor")}
                         for k, d in decided.items() if d["state"] in ("waiting", "unreachable")}

    # One item per head: group the legs to file (reds + stuck cancelled) by head key.
    groups: dict[str, list[dict]] = {}
    for leg in red + stuck:
        if leg["key"] in filed_before:
            continue
        groups.setdefault(_ci_head_key(leg["repo"], leg["pr"], leg["head_sha"]), []).append(leg)
    skipped = sum(1 for leg in red + stuck if leg["key"] in filed_before)

    # Resolve-on-green candidates: a head whose recorded legs are all green,
    # against older filed items for the same PR that are not yet resolved.
    to_resolve: list[tuple[str, dict, str]] = []
    for item_key, item in items.items():
        if item.get("resolved"):
            continue
        pr_key = _ci_pr_key(item["repo"], item["pr"], item["head_sha"])
        mine = (heads.get(pr_key) or {}).get(item["head_sha"])
        for sha, at in (heads.get(pr_key) or {}).items():
            if sha == item["head_sha"] or at is None or mine is None or at <= mine:
                continue
            legs = head_legs.get(_ci_head_key(item["repo"], item["pr"], sha)) or {}
            if legs and all(c in _CI_GREEN for c in legs.values()) and "success" in legs.values():
                to_resolve.append((item_key, item, sha))
                break

    def _persist() -> None:
        state["ci_filed"] = filed_now
        state["ci_items"] = items
        state["ci_heads"] = heads
        state["ci_head_legs"] = head_legs
        state["ci_cancelled_pending"] = cancelled_pending
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")

    filed_now = dict(filed_before)
    if not enable_mcp:
        _persist()
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — reds reported, not filed",
                       filed=[], appended=[], resolved=[], refused=[], skipped=skipped, new_offset=offset,
                       would_resolve=[{"where": k, "superseded_by": sha} for k, _, sha in to_resolve])
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    filed, appended, refused = [], [], []
    for head_key, legs in groups.items():
        first = legs[0]
        where = _ci_pr_key(first["repo"], first["pr"], first["head_sha"])
        existing = items.get(head_key)
        if existing and existing.get("id"):
            # The head already has its item; a new leg joins it, no second filing.
            for lg in legs:
                filed_now[lg["key"]] = existing["id"]
                existing.setdefault("legs", []).append(lg["check"])
                appended.append({"where": where, "check": lg["check"], "id": existing["id"]})
            continue
        args = _filing_args(app, where, first["head_sha"], legs)
        try:
            result = mcp_client.call("human_required_enqueue", args)
        except Exception as exc:  # noqa: BLE001 — a refused filing is a line with its reason
            refused.append({"where": where, "legs": [lg["check"] for lg in legs], "error": str(exc)[:300]})
            break
        err = _tool_error(result)
        if err:
            refused.append({"where": where, "legs": [lg["check"] for lg in legs], "error": err})
            break
        item_id = (result.get("id") if isinstance(result, dict) else None) or "filed"
        for lg in legs:
            filed_now[lg["key"]] = item_id
        items[head_key] = {"id": item_id, "repo": first["repo"], "pr": first["pr"],
                           "head_sha": first["head_sha"], "legs": [lg["check"] for lg in legs],
                           "filed_at": receipt["at"]}
        filed.append({"where": where, "head_sha": first["head_sha"], "legs": [lg["check"] for lg in legs],
                      "conclusions": [lg["conclusion"] for lg in legs], "id": item_id})

    resolved, resolve_refused = [], []
    for item_key, item, sha in to_resolve:
        note = f"superseded by {sha}, green at {receipt['at']}"
        try:
            result = mcp_client.call("human_required_resolve", {
                "app_id": app, "item_id": item["id"], "status": "resolved", "note": note,
            })
        except Exception as exc:  # noqa: BLE001 — a refused resolve is a line; the item stays open and is retried
            resolve_refused.append({"where": item_key, "item_id": item["id"], "error": str(exc)[:300]})
            continue
        err = _tool_error(result)
        if err:
            resolve_refused.append({"where": item_key, "item_id": item["id"], "error": err})
            continue
        item["resolved"] = {"by": sha, "at": receipt["at"]}
        resolved.append({"where": item_key, "item_id": item["id"], "superseded_by": sha})

    # On a refusal the offset stays where a retry can find the row; what was
    # filed before it is remembered in ci_filed so the retry skips it.
    _persist()
    if not refused:
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
    if resolve_refused:
        receipt["resolve_refused"] = resolve_refused
    receipt.update(status="ok" if not refused else "could-not-run", filed=filed, appended=appended,
                   resolved=resolved, refused=refused, skipped=skipped,
                   new_offset=offset if not refused else receipt["offset"])
    return _emit(receipt)


# ── the catch-up step: repair state after a missed webhook ───────────────────

_CATCHUP_PER_TICK = 3


def _catchup_repos(state: dict) -> list[str]:
    """Every ``owner/repo`` that has ever appeared in state, plus any that
    ``WILLOW_BOT_CATCHUP_EXTRA_REPOS`` names (comma-separated). The catchup
    step polls each in turn, rotating across ticks. A fleet with zero PRs
    on record and no extras env returns an empty list — the receipt then
    says so honestly rather than fabricating a target."""
    repos: set[str] = set()

    def _add_key(key: object) -> None:
        if not isinstance(key, str) or "#" not in key:
            return
        repo = key.rsplit("#", 1)[0]
        if "/" in repo:
            repos.add(repo)

    for src in ("open", "seen", "merged_synced"):
        for key in state.get(src) or []:
            _add_key(key)
    for item in state.get("pending_audit") or []:
        if isinstance(item, dict):
            _add_key(item.get("repo_pr"))
    for key in (state.get("audit_dispatched") or {}).keys():
        _add_key(key)
    extras = os.environ.get("WILLOW_BOT_CATCHUP_EXTRA_REPOS", "").strip()
    if extras:
        for r in extras.split(","):
            r = r.strip()
            if r and "/" in r:
                repos.add(r)
    return sorted(repos)


def run_catchup(*, enable_mcp: bool | None = None) -> dict:
    """Repair a missed webhook by polling ``/repos/{repo}/pulls`` under the
    App's install token.

    Gap: a lost webhook (a Pangolin restart mid-delivery, a systemd roll
    while a POST was in flight, a proxy dropping the body) is invisible
    to the bot — the tick's ``open`` set stays stale, the audit step
    never sees the PR, the seat learns of it from a human. This step is
    the reader: for a bounded batch of repos each tick, query the App's
    live view of open PRs and reconcile it with ``state["open"]``. Any
    PR the API sees that state does not gets promoted to
    ``pending_audit`` under the same shape as ``run_once`` writes,
    so the same-tick audit step picks it up.

    Idempotent per (repo, PR) through the same ``pending_audit`` +
    ``audit_dispatched`` guards as ``run_once``: a repair for an
    already-known PR is a no-op. The catch-up cursor
    (``state["catchup_cursor"] = {next_index, last_polled_at, batch}``)
    rotates the polling across ticks so a fleet of 20 repos does not
    hammer 20 requests every 5 min — 3 × 12 = 36 requests/hour under
    the default cap, well inside the App installation's 5000/hr.

    Honest absence when MCP is off — this is an act-half read (a live
    GitHub API poll under the App's credential); the same posture as
    ``sweep``/``mirror``/``audit``.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {
        "event": "steward_catchup",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not enable_mcp:
        receipt.update(status="absent",
                       detail="WILLOW_BOT_MCP not enabled — no list_pulls")
        return _emit(receipt)

    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    repos = _catchup_repos(state)
    receipt["repos_known"] = len(repos)
    if not repos:
        receipt.update(status="ok", polled=[], repaired=[], errors=[],
                       detail="no repos to poll (state carries none, no extras env)")
        return _emit(receipt)

    cursor = state.get("catchup_cursor") or {}
    start = int(cursor.get("next_index") or 0) % len(repos)
    batch = repos[start:start + _CATCHUP_PER_TICK]
    if len(batch) < _CATCHUP_PER_TICK and len(repos) > _CATCHUP_PER_TICK:
        batch.extend(repos[:_CATCHUP_PER_TICK - len(batch)])
    receipt["batch"] = batch

    import github_app  # local import so a test can monkeypatch the module

    now_open = set(state.get("open") or [])
    pending = list(state.get("pending_audit") or [])
    dispatched = state.get("audit_dispatched") or {}
    already_pending = {p.get("repo_pr") for p in pending if isinstance(p, dict)}
    polled: list[dict] = []
    repaired: list[dict] = []
    errors: list[dict] = []
    for repo in batch:
        try:
            pulls = github_app.list_open_pulls(repo)
        except Exception as exc:  # noqa: BLE001 — one bad repo is a line, not a dead step
            errors.append({"repo": repo, "error": str(exc)[:300]})
            continue
        polled.append({"repo": repo, "count": len(pulls)})
        for pr in pulls:
            num = pr.get("number") if isinstance(pr, dict) else None
            if not num:
                continue
            key = f"{repo}#{num}"
            if key in now_open:
                continue
            title = pr.get("title", "") if isinstance(pr, dict) else ""
            url = pr.get("html_url", "") if isinstance(pr, dict) else ""
            if key not in already_pending and key not in dispatched:
                pending.append({"repo_pr": key, "title": title, "url": url})
                already_pending.add(key)
            repaired.append({"repo_pr": key, "title": title, "url": url})
            now_open.add(key)

    state["open"] = sorted(now_open)
    state["pending_audit"] = pending
    state["catchup_cursor"] = {
        "next_index": (start + _CATCHUP_PER_TICK) % max(len(repos), 1),
        "last_polled_at": receipt["at"],
        "batch": batch,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    receipt.update(status="ok", polled=polled, repaired=repaired, errors=errors,
                   cursor=state["catchup_cursor"])
    return _emit(receipt)


# ── the install step: refresh editable installs after a sweep ────────────────


def _default_branch_for(checkout: str) -> str | None:
    """Read the checkout's remote HEAD symref (``refs/remotes/origin/HEAD``)
    and return the branch name it points at. Git sets this when a repo is
    cloned (``origin/HEAD -> origin/main``); a checkout that was ``git
    init``-ed and later added a remote will not have it and returns None.
    The sweep just fast-forwarded whatever this points to, so it is the
    right branch to pass to ``refresh_editable``."""
    if not checkout:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", checkout, "symbolic-ref", "--short",
             "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    v = proc.stdout.strip()
    if v.startswith("origin/"):
        v = v[len("origin/"):]
    return v or None


def run_install_receipts(sweep: dict | None = None) -> dict:
    """Refresh the editable install of each checkout the sweep brought home.

    Gap ``1f6b033ffca7`` (bot half). The old path was ``merge.py``'s
    ``sync_checkout``: for every merged PR the bot checked out
    ``default_branch`` (yanking an agent off their feature branch),
    ``git pull``-ed (a diverged local turned into a merge commit),
    and ran ``pip install -e .`` (returning ``skip`` or ``ok``, no
    distinct name for the tree it should not have touched).

    ``willow_bot.install_receipt.refresh_editable`` replaces the yanks
    with distinct states — ``missing_checkout``, ``dirty``,
    ``on_feature_branch``, ``fetch_failed``, ``diverged``, ``ahead``,
    ``install_failed``, ``ok`` — and never switches branches or
    resolves a merge conflict. This step is the wiring: for each range
    the sweep pulled (``ok=True and pulled=True`` in the swept list),
    read the checkout's ``origin/HEAD`` for the default branch, then
    call ``refresh_editable``. Every receipt lands in the tick log.

    Never runs on a range the sweep did not pull — a flag that landed
    on a fresh branch (no ``before``) has no commits to refresh
    against, and the state file's ``merged_synced`` already remembers
    what came home. Honest absence when the sweep brought nothing
    home; the seat reading a receipt file with an empty ``receipts``
    array knows there was nothing to install.
    """
    from willow_bot import install_receipt as install_mod

    receipt: dict = {
        "event": "steward_install",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    ranges = list((sweep or {}).get("ranges") or [])
    receipt["ranges"] = len(ranges)
    if not ranges:
        receipt.update(status="ok", receipts=[], detail="nothing came home")
        return _emit(receipt)

    receipts_out: list[dict] = []
    for rng in ranges:
        checkout = rng.get("checkout") or ""
        repo = rng.get("repo") or ""
        entry: dict = {"repo": repo, "checkout": checkout}
        branch = rng.get("default_branch") or _default_branch_for(checkout)
        if not branch:
            entry.update(state="unknown_default_branch",
                         detail="could not read origin/HEAD; install refused")
            receipts_out.append(entry)
            continue
        try:
            r = install_mod.refresh_editable(Path(checkout), branch)
        except Exception as exc:  # noqa: BLE001 — one bad checkout is a line, not a dead step
            entry.update(state="error", detail=str(exc)[:300])
            receipts_out.append(entry)
            continue
        entry.update(r)
        receipts_out.append(entry)
    receipt.update(status="ok", receipts=receipts_out)
    return _emit(receipt)


def run_sweep(*, enable_mcp: bool | None = None) -> dict:
    """The act half of a tick: ask the broker to bring merges home.

    ``gitsync_sweep`` (willow-mcp #524) consumes the flags fleet_bridge
    writes on every push to a default branch — it fetches with the App's
    token, fast-forwards, prunes nothing it was not told to, and leaves a
    FRANK receipt. This replaces merge.py's host ``gh`` + ``pip -e`` path,
    which needed a human's credential on the box. Honest absence when MCP
    is off: the receipt says so, nothing is pulled, and nothing pretends.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_sweep", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no sweep")
        return _emit(receipt)
    try:
        from willow_bot.steward import mcp_client

        app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
        result = mcp_client.call("gitsync_sweep", {"app_id": app, "project": "fleet"})
    except Exception as exc:  # noqa: BLE001 — a failed sweep is a line, not a dead loop
        receipt.update(status="could-not-run", detail=str(exc)[:400])
        return _emit(receipt)
    err = _tool_error(result)
    if err:
        receipt.update(status="could-not-run", detail=err)
        return _emit(receipt)
    swept = result.get("swept", []) if isinstance(result, dict) else []
    receipt.update(
        status="ok" if isinstance(result, dict) and result.get("ok") else "could-not-run",
        present=bool(isinstance(result, dict) and result.get("present")),
        pulled=[s.get("repo") for s in swept if s.get("ok") and s.get("pulled")],
        refused=[{"flag": s.get("flag"), "error": s.get("error")} for s in swept if not s.get("ok")],
        # What came home, as ranges — the resolve step reads trailers off them.
        ranges=[
            {"repo": s.get("repo"), "checkout": s.get("checkout"),
             "before": s.get("before"), "after": s.get("after")}
            for s in swept if s.get("ok") and s.get("pulled")
        ],
    )
    return _emit(receipt)


# ── the resolve step: a merged commit names the gap it closed ────────────────

# `Gap-Id: <12 hex>` / `Idea-Id: willow-ideas-NNN` — the join keys
# willows-grove INVARIANTS §11 defines. The CI checker there refuses a
# malformed value; this side matches the well-formed shape only and ignores
# anything else, so a trailer that slipped past a repo without the checker
# still cannot resolve the wrong gap. The Idea-Id shape is the reconciler's
# (willow-reconciler/reconciler/ids.py: `willow-ideas-<3 digits>`), and
# `Idea-Status` is commit-level there too — a commit landing several ideas
# partially says so about all of them.
_GAP_ID_RE = re.compile(r"^\s*Gap-Id\s*:\s*([0-9a-f]{12})\s*$", re.MULTILINE | re.IGNORECASE)
_IDEA_ID_RE = re.compile(r"^\s*Idea-Id\s*:\s*(willow-ideas-\d{3})\s*$", re.MULTILINE | re.IGNORECASE)
_IDEA_STATUS_RE = re.compile(r"^\s*Idea-Status\s*:\s*(landed|partial)\s*$", re.MULTILINE | re.IGNORECASE)
_TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]")

IDEA_LANDINGS = "idea_landings"


def _trailer_block(message: str) -> str:
    """The trailer block only: the run of paragraphs at the END of the
    message whose every line is `Key: value` shaped. Same rule as the
    reconciler's gitevidence.trailer_block — a commit that DISCUSSES the
    convention in its body must not read as one that CARRIES it (a body
    explaining `Gap-Id:` would otherwise resolve a gap). Walks back over
    consecutive all-trailer paragraphs so a `Gap-Id` above the
    `Co-Authored-By` block still counts; stops at the first prose line."""
    paragraphs = re.split(r"\n[ \t]*\n", message)
    kept: list[str] = []
    for para in reversed(paragraphs):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue
        if not all(_TRAILER_LINE_RE.match(ln) for ln in lines):
            break
        kept.insert(0, "\n".join(lines))
    return "\n".join(kept)


def _commits_in_range(checkout: str, before: str, after: str) -> list[tuple[str, str]]:
    """``[(sha, full message)]`` for ``before..after`` in ``checkout``, newest
    first, read from the host tree the sweep just fast-forwarded. A fresh
    branch (no ``before``) yields nothing: there is no range to read."""
    if not checkout or not before or not after or before == after:
        return []
    # NUL-separated records so a message body with blank lines parses.
    proc = subprocess.run(
        ["git", "-C", checkout, "log", f"{before}..{after}", "--format=%H%x00%B%x00"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[-300:] or f"git log exited {proc.returncode}")
    fields = proc.stdout.split("\x00")
    out: list[tuple[str, str]] = []
    for i in range(0, len(fields) - 1, 2):
        sha = fields[i].strip()
        if sha:
            out.append((sha, fields[i + 1]))
    return out


def run_resolve(sweep: dict | None = None, *, enable_mcp: bool | None = None) -> dict:
    """Resolve every backlog gap a merged commit named in a ``Gap-Id:`` trailer.

    Gap e278ec952b9c: the backlog has no mechanism linking a landed fix
    back to the gap that motivated it, so a seat orienting on ``gap_list``
    re-derives fixed problems and warns operators off working mechanisms.
    This is the reader for the join key: for each range the sweep brought
    home, read the merged commits' trailer blocks and call ``gap_resolve``
    with ``merged <repo>@<sha>`` as the note.

    ``Idea-Id:`` trailers (the reconciler's ``willow-ideas-NNN``) land as
    one ``idea_landings`` record each — ``{idea_id, status, repo, sha,
    merged_at}`` under record id ``<idea_id>:<sha12>`` so a re-run
    overwrites rather than duplicates. The reconciler itself keeps reading
    git (stdlib-only, by design); this record is the desk's timestamped
    view of the same landing, the moment it merges, without a git walk.

    Idempotent per (gap, sha) through the state file: a gap moves into
    ``gaps_resolved[gap_id] = repo@sha`` only when the tool answered without
    an error dict; a refusal is reported and retried next time that range
    is seen — which it will not be, so ``refused`` is the line to read.
    Honest absence when MCP is off or when the sweep pulled nothing.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_resolve", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ranges = list((sweep or {}).get("ranges") or [])
    receipt["ranges"] = len(ranges)
    if not ranges:
        receipt.update(status="ok", resolved=[], ideas=[], refused=[], detail="nothing came home")
        return _emit(receipt)

    # Read first, so a missing MCP still reports what it WOULD have resolved.
    found: list[dict] = []
    ideas: list[dict] = []
    unreadable: list[dict] = []
    for rng in ranges:
        repo = rng.get("repo", "")
        try:
            commits = _commits_in_range(rng.get("checkout", ""), rng.get("before", ""), rng.get("after", ""))
        except Exception as exc:  # noqa: BLE001 — one unreadable range is a line, not a dead step
            unreadable.append({"repo": repo, "error": str(exc)[:300]})
            continue
        for sha, message in commits:
            trailers = _trailer_block(message)
            for gap_id in _GAP_ID_RE.findall(trailers):
                found.append({"gap_id": gap_id.lower(), "repo": repo, "sha": sha})
            status_m = _IDEA_STATUS_RE.search(trailers)
            status = status_m.group(1).lower() if status_m else "landed"
            seen: set[str] = set()
            for idea_id in _IDEA_ID_RE.findall(trailers):
                idea_id = idea_id.lower()
                if idea_id in seen:
                    continue  # one commit repeating an id is one claim
                seen.add(idea_id)
                ideas.append({"idea_id": idea_id, "status": status, "repo": repo, "sha": sha})
    receipt["ideas"] = ideas
    if unreadable:
        receipt["unreadable"] = unreadable
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no gap_resolve",
                       found=found, resolved=[], refused=[], ideas_stored=[])
        return _emit(receipt)
    if not found and not ideas:
        receipt.update(status="ok", resolved=[], refused=[], ideas_stored=[])
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    already = dict(state.get("gaps_resolved") or {})
    resolved, refused, skipped = [], [], []

    # Ideas first: a landing record is a write with no side effect beyond
    # itself, and it must not be lost behind a refused gap_resolve.
    ideas_stored, ideas_refused = [], []
    for idea in ideas:
        record_id = f"{idea['idea_id']}:{idea['sha'][:12]}"
        args = {
            "app_id": app, "collection": IDEA_LANDINGS, "record_id": record_id, "deviation": 0,
            "record": {**idea, "merged_at": receipt["at"], "source": "willow-bot-steward"},
        }
        try:
            result = mcp_client.call("store_put", args)
        except Exception as exc:  # noqa: BLE001 — a refused landing is a line with its reason
            ideas_refused.append({"record_id": record_id, "error": str(exc)[:300]})
            continue
        err = _tool_error(result)
        if err:
            ideas_refused.append({"record_id": record_id, "error": err})
            continue
        ideas_stored.append(record_id)
    receipt["ideas_stored"] = ideas_stored
    if ideas_refused:
        receipt["ideas_refused"] = ideas_refused
    for item in found:
        gap_id, where = item["gap_id"], f"{item['repo']}@{item['sha']}"
        if already.get(gap_id) == where:
            skipped.append({"gap_id": gap_id, "where": where})
            continue
        try:
            result = mcp_client.call("gap_resolve", {
                "app_id": app, "gap_id": gap_id, "note": f"merged {where}",
            })
        except Exception as exc:  # noqa: BLE001 — a refused resolve is a line with its reason
            refused.append({"gap_id": gap_id, "where": where, "error": str(exc)[:300]})
            continue
        err = _tool_error(result)
        if err:
            refused.append({"gap_id": gap_id, "where": where, "error": err})
            continue
        already[gap_id] = where
        resolved.append({"gap_id": gap_id, "where": where})
    state["gaps_resolved"] = already
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    receipt.update(status="ok", resolved=resolved, refused=refused, skipped=skipped)
    return _emit(receipt)


def mcp_enabled() -> bool:
    return os.environ.get("WILLOW_BOT_MCP", "").strip().lower() in ("1", "true", "yes")


def _tool_error(result: object) -> str | None:
    """willow-mcp tools report a refusal as a dict with an `error` key, and
    the MCP client returns that dict — it does not raise. A step that treats
    any returned dict as success counts refusals as done: the first live
    mirror advanced its offset past 200 rows that never landed. Every step
    asks this first."""
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])[:300]
    return None


def _receipts_path() -> Path:
    from willow_bot.steward.config import willow_home

    return willow_home() / "willow-bot" / "steward_ticks.jsonl"


def _emit(receipt: dict) -> dict:
    """Print the receipt (the journal) AND append it to steward_ticks.jsonl
    (a file the seat can read — the journal it cannot)."""
    try:
        path = _receipts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    except OSError:
        pass
    print(json.dumps(receipt), flush=True)
    return receipt


def run_loop(interval_s: float = 300.0) -> int:
    prompt = os.environ.get("WILLOW_BOT_STEWARD_AGENT_PROMPT") or os.environ.get(
        "LOKI_PR_WATCH_AGENT_PROMPT", _DEFAULT_PROMPT
    )
    while True:
        time.sleep(interval_s)
        print(f"=== willow-bot-steward {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 — tick must not die silently without a line
            print(json.dumps({"event": "error", "detail": str(exc)}), flush=True)
        try:
            from willow_bot.steward.heartbeat import run_heartbeat

            run_heartbeat()
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"event": "error", "detail": f"heartbeat: {exc}"}), flush=True)
        # Sweep first; resolve reads the ranges the sweep brought home.
        sweep: dict | None = None
        try:
            sweep = run_sweep()
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"event": "error", "detail": f"sweep: {exc}"}), flush=True)
        # voice runs LAST — after ci and audit have written the state the
        # label reconciler reads. If both ran clean, voice sees fresh
        # audit_dispatched; if either raised, voice reconciles what state
        # it can see and the next tick picks up what changed.
        from willow_bot.steward.voice import run_voice

        for name, step in (
            ("resolve", lambda: run_resolve(sweep)),
            ("install", lambda: run_install_receipts(sweep)),
            ("mirror", run_mirror),
            ("ci", run_ci),
            ("catchup", run_catchup),
            ("audit", run_audit),
            ("voice", run_voice),
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"event": "error", "detail": f"{name}: {exc}"}), flush=True)
        print(
            "AGENT_LOOP_TICK_PR_AUDIT "
            + json.dumps({"prompt": prompt}, separators=(",", ":")),
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("tick", "once"):
        return run_once()
    if args[0] == "heartbeat":
        from willow_bot.steward.heartbeat import run_heartbeat

        run_heartbeat()
        return 0
    if args[0] == "sweep":
        run_sweep()
        return 0
    if args[0] == "resolve":
        # Sweep then resolve, as the loop does — resolve alone has no ranges.
        run_resolve(run_sweep())
        return 0
    if args[0] == "mirror":
        run_mirror()
        return 0
    if args[0] == "ci":
        run_ci()
        return 0
    if args[0] == "audit":
        run_audit()
        return 0
    if args[0] == "catchup":
        run_catchup()
        return 0
    if args[0] == "install-receipts":
        # Sweep then install, as the loop does — install alone has no ranges.
        run_install_receipts(run_sweep())
        return 0
    if args[0] == "voice":
        from willow_bot.steward.voice import run_voice

        run_voice()
        return 0
    if args[0] == "status":
        from willow_bot import status

        print(json.dumps(status.report(), separators=(",", ":"), default=str))
        return 0
    if args[0] == "loop":
        interval = float(
            os.environ.get(
                "WILLOW_BOT_STEWARD_INTERVAL",
                os.environ.get("LOKI_PR_WATCH_INTERVAL", "300"),
            )
        )
        return run_loop(interval)
    if args[0] == "inbox":
        if len(args) != 2:
            print("usage: willow-bot-steward inbox <state.json>", file=sys.stderr)
            return 2
        return inbox_mod.ingest(Path(args[1]))
    if args[0] == "scan":
        return scan_mod.main()
    print(
        "usage: willow-bot-steward [tick|loop|heartbeat|sweep|resolve|install-receipts|mirror|ci|catchup|audit|voice|status|inbox <state>|scan]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
