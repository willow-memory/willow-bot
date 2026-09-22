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

from willow_bot.steward import ci_comments
from willow_bot.steward import inbox as inbox_mod
from willow_bot.steward import merge as merge_mod
from willow_bot.steward import scan as scan_mod
from willow_bot.steward.config import app_id as _resolve_app_id, host_sync_enabled, state_path
from willow_bot.steward.config import manifest_fingerprint as _manifest_fingerprint


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


def run_once(*, do_host_sync: bool | None = None, scan_filters: list[str] | None = None) -> int:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Capture scan lines. `scan.main()` reads `sys.argv[1:]` as repo
    # filters; the unit runs `willow-bot-steward loop`, so with the
    # process argv left in place the filter was `['loop']`, every PR was
    # rejected, and `state['open']` was written EMPTY every tick — the
    # catchup step refilled three repos a tick behind it (gap
    # 1045a4056d11, Loki B7B947C4). The argv is reset around the call, the
    # way `merge` below already does, and the filters actually applied are
    # receipted so an empty open set is visible for what it is.
    from io import StringIO
    from contextlib import redirect_stdout

    filters = list(scan_filters or [])
    buf = StringIO()
    old_argv = sys.argv
    sys.argv = ["scan", *filters]
    try:
        with redirect_stdout(buf):
            scan_mod.main()
    finally:
        sys.argv = old_argv
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]

    keys: list[str] = []
    meta: dict[str, str] = {}
    for line in lines:
        key, rest = line.split("|", 1)
        title, url = rest.rsplit("|", 1)
        keys.append(key)
        meta[key] = f"{title}|{url}"

    state = _load_or_init_state(path, keys)
    # The scan receipt: how many PRs the bot sees open, under which filters,
    # at what time, and what it could NOT list. An unfiltered scan that
    # finds nothing says so rather than leaving an empty list to be
    # mistaken for "no PRs are open" — and the legacy clear and the stuck
    # backfill trust `open` only through this record. `errors` is the
    # scan's own incompleteness (fleet.last_scan_errors: a repo, org or
    # user whose listing failed and was skipped); a consumer reading "not
    # in the open set" as "closed on GitHub" must refuse for those
    # (gap 52928edb3fc7, Loki 25CBCB13).
    from willow_bot.steward import fleet as fleet_mod

    state["scan"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "open": len(keys), "filters": filters,
                     "errors": list(fleet_mod.last_scan_errors)}
    path.write_text(json.dumps(state, indent=2) + "\n")
    scan_receipt: dict = {"event": "steward_scan", **state["scan"]}
    if not keys:
        scan_receipt["detail"] = ("scan returned no open PRs" + (f" under filters {filters}" if filters
                                  else " — the fleet has none, or the listing failed silently"))
    _emit(scan_receipt)

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


#: The dispatch envelope an audit packet is sent under: the one whose bounds
#: are exactly `{to_agents: loki, task_class: auditor}`. Remembered in state
#: under this key once resolved, so the resolution costs one refused call
#: per steward lifetime, not one per PR per tick.
_AUDIT_ENVELOPE_STATE_KEY = "audit_envelope_id"
_AUDIT_TASK_CLASS = "auditor"
_AUDIT_TO_AGENT = "loki"


def _auditor_envelope_from(envelopes: object) -> str | None:
    """Pick the auditor envelope out of an EAMBIG's `envelopes` list.

    willow-mcp names every active dispatch envelope on refusal, each with
    its `envelope_id` and `bounds`. The one this step may cite has
    `task_class == "auditor"` and `to_agents` naming loki — as a bare
    string or as a member of a list, since both spellings exist in the
    live registry (measured 2026-09-20: `to_agents: "loki"` on the auditor
    row, lists elsewhere). Anything else is not ours to pick, whatever its
    id looks like: never hardcode an envelope id.
    """
    if not isinstance(envelopes, list):
        return None
    for env in envelopes:
        if not isinstance(env, dict):
            continue
        bounds = env.get("bounds") if isinstance(env.get("bounds"), dict) else {}
        if bounds.get("task_class") != _AUDIT_TASK_CLASS:
            continue
        to = bounds.get("to_agents")
        agents = [to] if isinstance(to, str) else (to if isinstance(to, list) else [])
        if _AUDIT_TO_AGENT in agents and isinstance(env.get("envelope_id"), str):
            return env["envelope_id"]
    return None


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

    The envelope is named, not assumed (gap b26c8232fa9d): with more than
    one dispatch envelope active for the steward's seat, a bare
    `dispatch_send` answers `EAMBIG` and lists them — measured 2026-09-20,
    five of twenty-seven audits refused every tick, forever. On that
    refusal the step picks the envelope whose bounds are the auditor's,
    remembers it in state, and retries the same PR under it in the same
    tick. If the list holds no auditor envelope the PR is refused with
    `no auditor envelope`, which names the grant that is missing rather
    than the ambiguity that is not the problem. A remembered id that stops
    governing (`ENOENT` on a later tick — revoked, or the registry
    re-issued) is forgotten so the next refusal re-resolves it.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_audit", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    pending = list(state.get("pending_audit") or [])
    receipt["pending"] = len(pending)
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no dispatch", **_Pacer.idle())
        return _emit(receipt)
    if not pending:
        receipt.update(status="ok", dispatched=[], refused=[], remaining=0, **_Pacer.idle())
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = _resolve_app_id()
    dispatched = dict(state.get("audit_dispatched") or {})
    envelope_id = state.get(_AUDIT_ENVELOPE_STATE_KEY) or None
    resolved_this_tick = False
    done, refused, still = [], [], []
    # Paced through this step's own budget (gap 52928edb3fc7): audit is
    # the step that reaches Loki, and it used to refuse a PR on the first
    # rate_limited the mirror's pacing left behind — three of five on the
    # 2026-09-21 01:07Z tick. A budget-spent stop leaves the rest pending
    # for the next tick, as a refusal always has.
    pace = _Pacer(_AUDIT_TIME_BUDGET_S)
    for item in pending[:_AUDIT_PER_TICK]:
        key = item.get("repo_pr", "")
        if pace.budget_spent:
            still.append(item)
            continue
        args = {
            "app_id": app,
            "to_app": _AUDIT_TO_AGENT,
            "role": _AUDIT_TASK_CLASS,
            "summary": f"Audit {key}: {item.get('title', '')}"[:200],
            "assignment_md": _audit_brief(item),
            "context_refs": [item.get("url", "")],
            "reply_to": "willow",
            "phase": "operate",
            "priority": "normal",
        }
        if envelope_id:
            args["envelope_id"] = envelope_id
        result, err = pace.call(mcp_client.call, "dispatch_send", args)
        if err and pace.budget_spent:
            # A pause, not a refusal: the PR stays pending untouched and the
            # step says where it stopped (Loki 095AF9DB).
            receipt["stopped"] = {"at": key, "reason": err}
            still.append(item)
            continue
        if err and err.startswith("EAMBIG") and not envelope_id:
            # First refusal of the lifetime: the tool named the options.
            picked = _auditor_envelope_from(result.get("envelopes") if isinstance(result, dict) else None)
            if picked is None:
                result, err = None, ("no auditor envelope: none of the active dispatch "
                                     "envelopes is bounded to task_class=auditor for loki")
            else:
                envelope_id = picked
                resolved_this_tick = True
                result, err = pace.call(mcp_client.call, "dispatch_send", {**args, "envelope_id": envelope_id})
                if err and pace.budget_spent:
                    receipt["stopped"] = {"at": key, "reason": err}
                    still.append(item)
                    continue  # the id was listed, not refused; it is kept
                if err:
                    # The id the tool itself just listed was refused when
                    # named — same rule as below: a refused assertion is
                    # not carried out of the tick, whatever the errno.
                    envelope_id = None
                    resolved_this_tick = False
                    state.pop(_AUDIT_ENVELOPE_STATE_KEY, None)
        elif err and envelope_id and not err.startswith("rate_limited"):
            # A refusal while the remembered id was named — ENOENT (it
            # no longer governs), EAMBIG (its bounds changed under us),
            # or anything else. The id was this step's own assertion
            # and the tool just refused it, so the assertion is gone
            # whatever the errno (Loki 09922563: an EAMBIG here used to
            # fall through, keep the id, and refuse every PR every tick
            # forever). Forget it; this PR stays pending with the reason
            # and the next refusal re-resolves from the tool's own list.
            # No second call this tick — one honest refusal beats a
            # guessed retry against a registry that moved. A limiter
            # answer is not a refusal of the id: the envelope stays.
            envelope_id = None
            state.pop(_AUDIT_ENVELOPE_STATE_KEY, None)
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
    if envelope_id:
        state[_AUDIT_ENVELOPE_STATE_KEY] = envelope_id
    path.write_text(json.dumps(state, indent=2) + "\n")
    # Same precedence as run_ci: a tool refusal outranks a pause (Loki FE91FF0E).
    status = "could-not-run" if refused else ("paced" if pace.budget_spent else "ok")
    receipt.update(status=status, dispatched=done, refused=refused,
                   remaining=len(still), envelope_id=envelope_id, envelope_resolved=resolved_this_tick,
                   **pace.receipt())
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
    from willow_bot.deposits import (COLLECTION, deposits_jsonl, is_annul, record_id_for,
                                     void_set_before)

    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_mirror", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    src = deposits_jsonl()
    if not src.is_file():
        receipt.update(status="ok", present=False, mirrored=0, detail=f"no deposits file at {src}",
                       **_Pacer.idle())
        return _emit(receipt)
    off_path = _mirror_offset_path()
    offset = int(off_path.read_text().strip() or 0) if off_path.is_file() else 0
    size = src.stat().st_size
    if offset > size:
        offset = 0  # the file was truncated or rotated; start over, overwrites are idempotent
    receipt.update(present=True, offset=offset, size=size)
    if not enable_mcp:
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — no store_put",
                       behind=size - offset, **_Pacer.idle())
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = _resolve_app_id()
    mirrored, failed = 0, None
    annulled_skipped, annulled_mirrors = 0, 0
    pace = _Pacer(_MIRROR_TIME_BUDGET_S, calls=_MIRROR_CALLS_PER_TICK)
    # Voided rows (gap 9) never reach the store — the void set is built over
    # the whole file first, so a row voided by an annul later in this same
    # window is skipped too. An annul row met in the window also retracts
    # any copy the mirror landed BEFORE the annul (`store_delete` by the
    # voided row's record_id — the store keys mirrored rows that way, so
    # the id is derivable from the file); `mirrored_ids` maps only rows
    # before the offset, which is exactly what has been mirrored.
    voids = void_set_before(src)
    mirrored_ids = _mirrored_ids_by_hash(src, offset)
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
            if is_annul(rec):
                voids.take(rec)
                # Only ids the mirror has landed (chained: by hash → id;
                # legacy: by id, gated the same way) — and a retraction is
                # counted only when the store says it deleted something
                # (`store_delete` answers {deleted: false} on an unknown id
                # with no error; Loki 717E236C).
                retract = [mirrored_ids[h] for h in rec.get("voids") or [] if h in mirrored_ids]
                landed_ids = set(mirrored_ids.values())
                retract += [v for v in rec.get("voids_legacy") or []
                            if isinstance(v, str) and v in landed_ids and v not in retract]
                for record_id in retract:
                    result, err = pace.call(mcp_client.call, "store_delete",
                                            {"app_id": app, "collection": COLLECTION, "record_id": record_id})
                    if err is not None:
                        failed = err
                        break
                    if isinstance(result, dict) and result.get("deleted") is True:
                        annulled_mirrors += 1
                if failed is not None:
                    break
                offset = fh.tell()
                continue
            if voids.voided(rec):
                annulled_skipped += 1
                offset = fh.tell()
                continue
            args = {"app_id": app, "collection": COLLECTION, "record": rec,
                    "record_id": record_id_for(rec), "deviation": 0}
            # willow-mcp meters every app at 60/min with a burst of 10 and
            # answers the 11th call {"error": "rate_limited", "retry_after": N}.
            # The first live mirror stopped there with 7 rows landed (2026-09-15
            # 00:28Z). Pace instead: wait what the limiter asks, retry the same
            # row, inside this step's budget — the row is never skipped.
            result, err = pace.call(mcp_client.call, "store_put", args)
            if err is not None:
                failed = err
                break
            mirrored += 1
            offset = fh.tell()
    off_path.parent.mkdir(parents=True, exist_ok=True)
    off_path.write_text(f"{offset}\n", encoding="utf-8")
    behind = size - offset
    status = "ok" if failed is None else ("paced" if pace.budget_spent else "could-not-run")
    receipt.update(status=status, mirrored=mirrored, new_offset=offset, behind=behind,
                   annulled=annulled_skipped, annulled_mirrors=annulled_mirrors, **pace.receipt())
    if failed is not None:
        receipt["detail"] = failed
    return _emit(receipt)


def _mirrored_ids_by_hash(src: Path, offset: int) -> dict[str, str]:
    """``row_hash → record_id`` for every chained row BEFORE ``offset`` —
    the rows the mirror has already landed (its offset only advances past
    rows that did). An annul met later can retract them by id. Rows after
    the offset are never mirrored once voided, so they are not needed."""
    from willow_bot.deposits import is_annul, record_id_for

    out: dict[str, str] = {}
    if not src.is_file() or offset <= 0:
        return out
    with src.open("rb") as fh:
        while fh.tell() < offset:
            line = fh.readline()
            if not line:
                break
            try:
                rec = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or is_annul(rec):
                continue
            if isinstance(rec.get("row_hash"), str):
                out[rec["row_hash"]] = record_id_for(rec)
            else:
                # A legacy row has no hash; its record_id is its only handle,
                # keyed by itself so `values()` names it as landed.
                rid = record_id_for(rec)
                out[f"legacy:{rid}"] = rid
    return out


# Pacing knobs, module-level so a test can shrink them. The store meters at
# 60 calls/min per app (burst 10). Every MCP step that writes shares that
# one limiter, and until gap 52928edb3fc7 the mirror alone paced inside a
# 120 s budget while ci and audit fell over on their FIRST rate_limited —
# on 2026-09-21 01:07Z the mirror paced 21 of 27 rows, then ci ended
# `could-not-run rate_limited` on a real Nestor red and audit refused three
# PRs. Each step now paces through its own budget (`_Pacer`), and the
# mirror is additionally CAPPED in calls per tick so it cannot drain the
# minute the later steps need: 30 of the 60 leaves ci + audit the other
# half, and a 500-row backlog still lands in ~17 ticks. Mirror stays first
# in the loop (ci reads what it mirrored); the cap is what makes it yield.
_MIRROR_TIME_BUDGET_S = 120.0
_MIRROR_CALLS_PER_TICK = 30
_MIRROR_MAX_WAIT_S = 10
_CI_TIME_BUDGET_S = 60.0
_AUDIT_TIME_BUDGET_S = 60.0
_clock = time.monotonic
_sleep = time.sleep


class _Pacer:
    """One step's slice of the tick's limiter allowance.

    ``call`` retries the SAME call on ``rate_limited`` — waiting what the
    limiter asks, capped at ``_MIRROR_MAX_WAIT_S`` — until the step's time
    budget would be overrun or its call cap is reached; then reports
    ``budget_spent`` and the step stops where a retry can resume. Every
    other error is returned as-is. ``receipt()`` is the three fields each
    step's receipt carries so starvation is legible in the heartbeat:
    ``paced`` (how many waits), ``budget_spent`` (bool), ``calls`` (made).
    """

    def __init__(self, budget_s: float, *, calls: int | None = None) -> None:
        self.deadline = _clock() + budget_s
        self.cap = calls
        self.paced = 0
        self.calls = 0
        self.budget_spent = False

    def call(self, fn, name: str, args: dict) -> tuple[object, str | None]:
        while True:
            if self.cap is not None and self.calls >= self.cap:
                self.budget_spent = True
                return None, f"rate_limited (call cap {self.cap} reached; resumes next tick)"
            try:
                self.calls += 1
                result = fn(name, args)
            except Exception as exc:  # noqa: BLE001 — the caller decides what one failure means
                return None, str(exc)[:300]
            err = _tool_error(result)
            if err is None:
                return result, None
            if err != "rate_limited":
                return result, err
            retry = result.get("retry_after") if isinstance(result, dict) else None
            wait = min(max(int(retry or 1), 1), _MIRROR_MAX_WAIT_S)
            if _clock() + wait > self.deadline:
                self.budget_spent = True
                return result, f"rate_limited (paced {self.paced}x; time budget spent, resumes next tick)"
            self.paced += 1
            _sleep(wait)

    def receipt(self) -> dict:
        return {"paced": self.paced, "budget_spent": self.budget_spent, "calls": self.calls}

    @staticmethod
    def idle() -> dict:
        """The same three fields for a step that returned before it could
        pace anything (MCP off, nothing to do): declared zeros, so a
        reader never has to treat an absent field as 'did not pace'."""
        return {"paced": 0, "budget_spent": False, "calls": 0}


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


def _ci_branch_key(repo: str, branch: str) -> str:
    """The successor group for a head with no PR: every head the bot has
    seen on ``repo``'s ``branch``. Spelled so it can never collide with a
    ``repo#pr`` key (``#`` is not legal in a branch name's position here)."""
    return f"{repo}~{branch}"


def _decide_cancelled(pending: dict, heads: dict, *, now: float, grace_s: float,
                      head_legs: dict | None = None, closed: dict | None = None) -> dict:
    """Each pending cancelled leg gets exactly one of six states.

    * ``rerun`` — the same leg name on the same head has since reported a
      conclusion other than ``cancelled`` (a re-run): the cancelled run
      is moot. Dropped; the new conclusion is judged on its own.
    * ``moot`` — the PR the leg belongs to has CLOSED (merged or not) in
      the bot's own deposits: a run cancelled on a PR that no longer
      exists is nobody's stuck PR. Dropped, never filed (gap
      52928edb3fc7 — release-please PRs whose successor is the merge
      itself filed as stuck after grace).
    * ``superseded`` — a different head for the same PR was first seen
      AFTER this one: the run was cancelled because the next push landed.
      For a head with NO PR (a release commit on master, keyed
      ``repo@sha12``) the same rule runs over the heads seen on the same
      ``repo`` + ``head_branch`` — the deposit carries the branch since
      52928edb3fc7; a row without one has no successor group and ages
      like any other. Dropped, never filed.
    * ``waiting`` — no successor yet and the leg is younger than the grace
      window. Stays pending; the next tick decides again.
    * ``stuck`` — no successor and older than the grace window: nothing
      superseded it, so the PR is sitting on a cancelled run. Filed, in
      the same per-head item a red leg would be — as a cancelled leg,
      never as a red one, and never under a "CI red" title when it is
      the only kind of leg on the head (``_filing_args``).
    * ``unreachable`` — the deposit's timestamp cannot be read, so its age
      from the deposit is unknowable. Stays pending and says so — until
      the grace window has passed since the step FIRST SAW it
      (``pending_since``, wall clock, which is known), at which point it
      is ``stuck`` with ``aged_by: pending_since``. An unreadable input
      earns a wait, not a permanent seat in the pending set.

    Successor evidence is the bot's own deposits (``heads`` maps
    `repo#pr` — or `repo~branch` for PR-less heads — → {head_sha:
    first_seen_epoch}); closure evidence is the inbox's ``pr_closed``
    record (``closed``). No GitHub API is asked, so there is no "API
    failed" state to report. Successor ORDER is the deposits'
    ``received_at`` (the webhook's arrival), not GitHub's push order —
    close enough that a superseding push is always later, and the only
    clock the bot holds.
    """
    out: dict[str, dict] = {}
    for key, leg in pending.items():
        pr_key = _ci_pr_key(leg["repo"], leg["pr"], leg["head_sha"])
        latest = ((head_legs or {}).get(_ci_head_key_from_pr(pr_key, leg["head_sha"])) or {}).get(leg["check"])
        if latest is not None and latest != _CI_CANCELLED:
            out[key] = {**leg, "state": "rerun", "latest": latest}
            continue
        if leg["pr"] and pr_key in (closed or {}):
            out[key] = {**leg, "state": "moot",
                        "closed": "merged" if (closed or {})[pr_key].get("merged") else "closed"}
            continue
        group = pr_key
        if not leg["pr"] and leg.get("head_branch"):
            group = _ci_branch_key(leg["repo"], leg["head_branch"])
        seen = heads.get(group) or {}
        mine = seen.get(leg["head_sha"])
        if mine is None:
            mine = _ci_epoch(leg.get("received_at"))
        if mine is None:
            # The deposit's own clock is unreadable, so "later than me" is
            # judged against the moment this step first saw it: a head
            # whose first deposit arrived after that is a successor; an
            # older dated head is not (Loki 18CE5C43 — any dated head used
            # to count, so a bad clock on the newest head dropped it).
            since = leg.get("pending_since")
            later = [sha for sha, at in seen.items()
                     if sha != leg["head_sha"] and at is not None
                     and isinstance(since, (int, float)) and at > since]
            if later:
                out[key] = {**leg, "state": "superseded", "successor": sorted(later, key=lambda s: seen[s])[-1],
                            "aged_by": "pending_since"}
            elif isinstance(since, (int, float)) and now - since >= grace_s:
                out[key] = {**leg, "state": "stuck", "aged_by": "pending_since"}
            else:
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


def _head_is_green(later_legs: dict, expected: set[str]) -> bool:
    """What "the head is green" can honestly mean when the bot only sees
    deposits, one per webhook: every leg the EARLIER head recorded has
    reported on this head (the earlier head's leg set is the only known
    expected set), every leg reported so far is green, and at least one
    is a real ``success``. A head with only ``lint: success`` so far is
    not green — it is early."""
    if not later_legs:
        return False
    if not expected.issubset(later_legs.keys()):
        return False
    return all(c in _CI_GREEN for c in later_legs.values()) and "success" in later_legs.values()


def _heads_fully_voided(src: Path, voids) -> set[tuple[str, str]]:
    """``(repo, head_sha)`` for every head whose EVERY deposit in the file
    is voided under ``voids``. Read once per annul met (rare). A head with
    one honest row left is not voided — it stays."""
    from willow_bot.deposits import is_annul

    live: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    with src.open("rb") as fh:
        for line in fh:
            try:
                rec = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or is_annul(rec) or not rec.get("head_sha"):
                continue
            key = (rec.get("repo", ""), rec["head_sha"])
            seen.add(key)
            if not voids.voided(rec):
                live.add(key)
    return seen - live


def _resolved_lines_owed(items: dict, state: dict) -> set[str]:
    """Item keys of resolved items on a WATCHED PR whose `resolved` line has
    not been sent yet — kept through the prune so the next tick can retry."""
    from willow_bot.steward import pr_watch

    table = pr_watch.load()
    marks = state.get("ci_notified") or {}
    owed: set[str] = set()
    for key, item in items.items():
        if not item.get("resolved") or not item.get("id"):
            continue
        if "resolved" in (marks.get(item["id"]) or {}):
            continue
        if pr_watch.watcher_for(item.get("repo", ""), item.get("pr"), table) is not None:
            owed.add(key)
    return owed


def _prune_ci_state(items: dict, heads: dict, head_legs: dict, cancelled_pending: dict,
                    *, voided_heads: set[tuple[str, str]] | None = None,
                    keep_items: set[str] | None = None) -> dict:
    """Keep the four maps bounded (Loki, dispatch 82A7DB13, finding 4).

    A head is kept when it is the newest the bot has seen for its PR, the
    head of an unresolved item, or the head of a pending cancelled leg;
    every other head — and its ``head_legs`` — is dropped. Resolved items
    are dropped (``ci_filed`` still remembers their legs, so a
    re-delivered completion never re-files). The resolve loop is then
    unresolved-items × kept-heads, a handful per PR. Returns counts.

    ``voided_heads`` (gap 9): a head whose every deposit an annul voided is
    dropped from every group it sits in — even as a PR's newest head, even
    with a pending cancelled leg — and its items are dropped too: nothing
    honest was ever filed for it. ``ci_filed`` keeps its keys (a re-read
    of the voided rows is skipped before it could file, so the marker is
    inert).
    """
    voided_heads = voided_heads or set()
    dropped_voided = 0
    if voided_heads:
        for pr_key in list(heads):
            seen = heads[pr_key]
            repo = pr_key.split("#")[0].split("@")[0].split("~")[0]
            for sha in list(seen):
                if (repo, sha) in voided_heads:
                    del seen[sha]
                    dropped_voided += 1
            if not seen:
                del heads[pr_key]
        for key in list(cancelled_pending):
            leg = cancelled_pending[key]
            if (leg.get("repo", ""), leg.get("head_sha", "")) in voided_heads:
                del cancelled_pending[key]
        for item_key in list(items):
            it = items[item_key]
            if (it.get("repo", ""), it.get("head_sha", "")) in voided_heads:
                del items[item_key]
    keep: dict[str, set[str]] = {}
    keep_items = keep_items or set()
    for item_key, item in items.items():
        if not item.get("resolved") or item_key in keep_items:
            keep.setdefault(_ci_pr_key(item["repo"], item["pr"], item["head_sha"]), set()).add(item["head_sha"])
            if not item["pr"] and item.get("branch"):
                # A stuck PR-less item resolves when a later head lands on
                # its branch — which needs its own head kept in the group.
                keep.setdefault(_ci_branch_key(item["repo"], item["branch"]), set()).add(item["head_sha"])
    for leg in cancelled_pending.values():
        keep.setdefault(_ci_pr_key(leg["repo"], leg["pr"], leg["head_sha"]), set()).add(leg["head_sha"])
        if not leg["pr"] and leg.get("head_branch"):
            keep.setdefault(_ci_branch_key(leg["repo"], leg["head_branch"]), set()).add(leg["head_sha"])
    dropped_heads = dropped_items = 0
    for pr_key in list(heads):
        seen = heads[pr_key]
        dated = [s for s, at in seen.items() if at is not None]
        newest = max(dated, key=lambda s: seen[s]) if dated else (next(iter(seen)) if seen else None)
        wanted = set(keep.get(pr_key, set()))
        if newest:
            wanted.add(newest)
        for sha in list(seen):
            if sha not in wanted:
                del seen[sha]
                dropped_heads += 1
        if not seen:
            del heads[pr_key]
    live_heads = {_ci_head_key_from_pr(pr_key, sha) for pr_key, seen in heads.items() for sha in seen}
    for head_key in list(head_legs):
        if head_key not in live_heads:
            del head_legs[head_key]
    for item_key in list(items):
        if items[item_key].get("resolved") and item_key not in keep_items:
            del items[item_key]
            dropped_items += 1
    out = {"heads": dropped_heads, "items": dropped_items}
    if dropped_voided:
        out["voided_heads"] = dropped_voided
    return out


def _ci_head_key_from_pr(pr_key: str, head_sha: str) -> str:
    return f"{pr_key}@{head_sha}"


def _scan_open_set(state: dict) -> tuple[bool, set[str], list[str]]:
    """``(open_known, open_set, scan_errors)`` — the bot's own view of which
    PRs are open, trusted only through the scan record ``run_once`` writes:
    an UNFILTERED scan (before the argv fix the unit's scan ran under the
    filter ``['loop']`` and wrote ``open = []`` every tick — gap
    1045a4056d11 — and an empty list from that path is "the scan saw
    nothing", not "no PRs are open"). ``scan_errors`` is what that scan
    could not list (``repo:``/``org:``/``user:`` keys from
    ``fleet.last_scan_errors``); ``open_known`` says the scan ran unfiltered,
    NOT that it completed — a consumer must ask ``_scan_incomplete_for``
    before reading absence as closure (Loki 25CBCB13). A record from a build
    that did not write ``errors`` reads as no errors, as it always has."""
    scan = state.get("scan")
    open_prs = state.get("open")
    open_known = (isinstance(scan, dict) and scan.get("filters") == [] and isinstance(open_prs, list)
                  and (open_prs != [] or scan.get("open") == 0))
    open_set = set(open_prs or []) if open_known else set()
    errors = [e for e in ((scan or {}).get("errors") or []) if isinstance(e, str)] if isinstance(scan, dict) else []
    return open_known, open_set, errors


def _scan_incomplete_for(where: str, scan_errors: list[str]) -> str | None:
    """The scan-error key that covers ``where`` (``owner/repo#num`` or
    ``owner/repo@sha``), or None when the scan listed that repo: a
    ``repo:owner/repo`` failure, or an ``org:owner`` / ``user:owner`` page
    failure that may have dropped the repo from the fleet list entirely."""
    repo = where.split("#", 1)[0].split("@", 1)[0]
    owner = repo.split("/", 1)[0]
    for key in scan_errors:
        kind, _, name = key.partition(":")
        if kind == "repo" and name == repo:
            return key
        if kind in ("org", "user") and name == owner:
            return key
    return None


def _item_is_stuck_only(item: dict, filed: dict, filed_cancelled: dict) -> bool:
    """True when every leg this item ever filed was a cancellation. Items
    this build files say so (``stuck: True``); items from the build before
    carry no flag, so the answer is read from the two filed maps the way
    voice reads them — the id appears under ``ci_filed_cancelled`` and
    never under ``ci_filed``."""
    if "stuck" in item:
        return bool(item["stuck"])
    item_id = item.get("id")
    if not item_id:
        return False
    return item_id in filed_cancelled.values() and item_id not in filed.values()


def _leg_of(rec: dict) -> dict:
    return {
        "repo": rec.get("repo", ""), "pr": rec.get("pr_number"),
        "head_sha": rec.get("head_sha", ""), "check": rec.get("check_name", ""),
        "conclusion": rec.get("conclusion"), "url": rec.get("html_url", ""),
        "received_at": rec.get("received_at", ""), "key": _ci_key(rec),
        "head_branch": rec.get("head_branch") or "",
    }


def _public(leg: dict) -> dict:
    return {k: v for k, v in leg.items() if k not in ("key", "received_at", "head_branch")}


def _filing_args(app: str, where: str, head_sha: str, legs: list[dict], *, grace_s: float = 0.0) -> dict:
    causes = [lg for lg in legs if lg["check"] not in _CI_AGGREGATE_CHECKS] or legs
    names = ", ".join(lg["check"] for lg in causes)
    lines = [f"{lg['check']} concluded {lg['conclusion']}"
             + (" (aggregate)" if lg["check"] in _CI_AGGREGATE_CHECKS and lg not in causes else "")
             + f". {lg['url']}" for lg in legs]
    # A head whose every leg is a stuck cancellation is not red — nothing
    # failed; something never finished and nothing superseded it. The
    # title says that (gap 52928edb3fc7: three such items filed as "CI
    # red: … concluded cancelled" on 2026-09-20). A head with any real red
    # is red, and its cancelled legs ride in the summary as before.
    if legs and all(lg["conclusion"] == _CI_CANCELLED for lg in legs):
        minutes = int(round(grace_s / 60.0)) if grace_s else _CI_CANCELLED_GRACE_MIN_DEFAULT
        title = (f"CI stuck: {where} — {len(causes)} leg(s) cancelled, "
                 f"no successor after {minutes} min: {names}")
    else:
        title = f"CI red: {where} — {len(causes)} leg(s): {names}"
    return {
        "app_id": app, "kind": "review", "priority": "normal",
        "title": title[:200],
        "summary": f"head {head_sha}\n" + "\n".join(lines),
        "source_ref": causes[0]["url"] or legs[0]["url"],
    }


_GROVE_SENDER_ENV = "WILLOW_BOT_GROVE_SENDER"


def _grove_sender() -> str:
    """The Grove ``sender`` the steward posts as — always its own resolved
    ``app_id`` (sealed 163b9a70: one source of truth). Never call this to
    decide WHETHER to send when ``WILLOW_BOT_GROVE_SENDER`` disagrees with
    the app_id — see ``_grove_sender_mismatch`` for that; sending under a
    disagreeing name is refused entirely, not silently corrected, so this
    function never has to lie about who it posted as."""
    return _resolve_app_id()


def _grove_sender_mismatch() -> str | None:
    """``None`` when there is no ``WILLOW_BOT_GROVE_SENDER`` override, or
    it already agrees with the resolved ``app_id``. Otherwise the
    disagreeing override value itself — the caller MUST refuse to send at
    all (Loki 738DB24E F2, second half of 9778E096 F2): the first cut of
    this fix substituted the app_id for a disagreeing override and sent
    anyway, which under the interim box config the deploy template itself
    prescribes (app_id=willow, WILLOW_BOT_GROVE_SENDER=willow-bot left over
    from before this fix) posted every CI-red line to #willow AS the
    identity ``willow`` — the human trust-root seat, not the bot — because
    willow-mcp's own gate accepts a caller posting as its own resolved
    identity for free. A mismatch is a configuration problem to surface
    and fix, never a reason to speak as someone else."""
    app = _resolve_app_id()
    override = os.environ.get(_GROVE_SENDER_ENV, "").strip()
    if override and override != app:
        return override
    return None


# ── the CI-red comment: the failure block itself, and an unconditional word
# to Grove #willow ────────────────────────────────────────────────────────────
#
# Gap 1d28527ad324 / 2c2ab8bd9209 (2026-09-21): the steward filed a review
# item on a red check_run and tried to notify a "watcher" from
# `pr_watch.json` — a table only a post-#589 broker's `pr_open_execute`
# writes. #594 went red twice and nobody heard, because that table had no
# row for it. The comment and Grove line below never ask pr_watch whether
# to speak; a row there is read only as EXTRA context (never built here —
# `_notify_watchers` above already carries that use of it), never as the
# gate.
#
# Loki's audit of the first cut (dispatch E026CFE7) found the comment and
# the Grove line fired exactly ONCE, from inside the same loop that filed
# the review item through `human_required_enqueue`, AFTER the legs were
# already marked `ci_filed` — so a 502, a refused broker, or the broker
# being off made the silence permanent for that head, and a second red
# leg on a later tick rewrote the comment from only the NEW leg (the
# earlier job's block vanished). The fix lives in `ci_comments.py`: a
# small, separately and atomically written table, keyed per
# (repo, pr, head_sha), owed until it lands — independent of `ci_filed`,
# `ci_items`, and whatever `human_required_enqueue` answers. Every leg
# this head has ever shown a log for stays in that table, so the body is
# always rebuilt from the FULL set, not just this tick's delta.

_GROVE_CI_RED_REPO_PREFIX = "willow-memory/"
BODY_CHAR_CAP = 60_000
# A Grove `#willow` line, not another 60k-char PR comment — capped by
# CHARACTERS (Loki's re-audit: a 60-line head/tail cut on a real body of
# 4 KB-wide lines let a 59,864-char message straight through, because the
# cut counted LINES, and a wrapped pytest failure line is one very long
# line).
GROVE_SUMMARY_CHAR_CAP = 4_000


def _head_tail_chars(text: str, budget: int) -> str:
    """`text` unchanged when short enough; otherwise the first and last
    `budget // 2` characters, with a marker naming how many were dropped
    between them — never a bare tail-only cut. Used both to fit ONE leg's
    failure block into its own share of the body cap, and to cap the
    Grove summary by characters rather than lines."""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    half = max(budget // 2, 1)
    head, tail = text[:half], text[-half:]
    dropped = len(text) - len(head) - len(tail)
    if dropped <= 0:
        return text[:budget]
    return f"{head}\n... [{dropped} chars trimmed] ...\n{tail}"


def _ci_red_leg_lines(lg: dict, *, char_budget: int | None = None) -> str:
    from willow_bot.steward import ci_log

    block = lg.get("block")
    if block:
        notes = []
        if lg.get("trimmed"):
            notes.append(f"trimmed to the last {ci_log.TRIM_LINES} lines")
        if lg.get("log_truncated"):
            mb = ci_log.MAX_LOG_BYTES // (1024 * 1024)
            notes.append(f"log truncated to the last {mb} MB")
        if lg.get("refetch_failed_detail"):
            # Loki's re-audit, LIMIT 1: a leg that already had a real
            # block keeps it when a LATER re-fetch attempt fails — the
            # note says the re-fetch failed, never that the log is gone.
            notes.append(f"re-fetch failed: {lg['refetch_failed_detail']}; showing earlier log")
        if char_budget is not None and len(block) > char_budget:
            block = _head_tail_chars(block, char_budget)
            notes.append(f"block capped to {char_budget} chars")
        note = f" ({', '.join(notes)})" if notes else ""
        return f"<details><summary>{lg['check']}{note}</summary>\n\n```\n{block}\n```\n</details>\n"
    if lg.get("missing_permission"):
        return f"_{lg['check']}: log unavailable — missing `{lg['missing_permission']}`_\n"
    if lg.get("rate_limited"):
        return f"_{lg['check']}: log unavailable — rate limited, will retry_\n"
    if lg.get("refetch_failed_detail"):
        # No prior block to fall back on: honest this tick, but NOT stored
        # under `detail` — see `_ci_owed_refetch_stripped` — so the next
        # tick tries the fetch again instead of never again.
        return f"_{lg['check']}: log unavailable ({lg['refetch_failed_detail']})_\n"
    return f"_{lg['check']}: log unavailable ({lg.get('detail', 'unknown error')})_\n"


def _leg_char_budget(header: str, n_legs: int, *, cap: int = BODY_CHAR_CAP) -> int:
    """`cap` split evenly across legs, after the header's own space —
    never below a 4,000-char floor even when a head has many legs, so a
    leg with a lot of jobs still shows SOMETHING useful per job rather
    than a sliver."""
    n_legs = max(n_legs, 1)
    return max((cap - len(header)) // n_legs, 4_000)


def _ci_red_body(where: str, head_sha: str, legs: list[dict], *, cap: int = BODY_CHAR_CAP) -> str:
    """One PR comment body for a head's failing legs: which jobs are red,
    then each job's failure block (or why it has none) in a collapsible
    fenced section. `legs` are the per-leg dicts `_ci_red_leg_info` built,
    accumulated across every tick this head has been red — a log-fetch
    failure of ANY kind (missing permission, rate limited, network) is
    still one line in the body, never a reason to skip posting the whole
    comment.

    Hard-capped at `cap` chars against GitHub's 65,536-char comment limit
    (a 480,974-char body from two legs' untrimmed blocks — Loki's audit,
    P3 — 422'd and, before this build, was never retried).

    Loki's re-audit (P3 cap-shape finding): `ci_log._trim` already caps
    every REAL block at 120 lines, so a stage that only shrinks blocks
    over 120 lines never fires on real traffic, and the fallback raw
    character cut left an open ``` fence and an open `<details>` with the
    cap note rendered inside them. So the only shrink stage now is a
    PER-LEG character budget (`_leg_char_budget`) applied before
    rendering — every block is capped whether or not it needed the ci_log
    trim — and `_close_open_blocks` runs on the assembled body (not just
    the Grove summary) so a hard cut, if the per-leg budgets still don't
    fit, never leaves a dangling fence or an unclosed section.
    """
    header_lines = [f"CI red: {where} @ {head_sha[:7]} — {len(legs)} job(s):"]
    header_lines += [f"- **{lg['check']}** ({lg['conclusion']}): {lg['url']}" for lg in legs]
    header = "\n".join(header_lines) + "\n\n"

    budget = _leg_char_budget(header, len(legs), cap=cap)
    blocks = "\n".join(_ci_red_leg_lines(lg, char_budget=budget) for lg in legs).rstrip() + "\n"

    body = (header + blocks).strip() + "\n"
    if len(body) <= cap:
        return _close_open_blocks(body.rstrip("\n")) + "\n"

    # Per-leg budgets alone did not fit (e.g. many legs, or wrapper
    # overhead) — a last, raw character cut of the rendered blocks,
    # closed before the cap note is appended so the note never lands
    # inside a re-opened fence or <details>. Shrinks the cut window until
    # the closed body actually fits, rather than trusting one guess.
    note = f"\n\n_body capped at {cap} chars; remaining failure detail omitted_\n"
    trim_budget = max(cap - len(header) - len(note), 0)
    while True:
        closed_blocks = _close_open_blocks(blocks[:trim_budget].rstrip("\n"))
        body = (header + closed_blocks).strip() + note
        if len(body) <= cap or trim_budget <= 0:
            return body
        trim_budget = max(trim_budget - 200, 0)


def _ci_red_green_body(where: str, head_sha: str, *, by: str, at: str) -> str:
    """The same comment, edited on resolve — kept, not deleted (the
    assignment's own words: "green at <sha>")."""
    return f"CI red: {where} @ {head_sha[:7]} — green at {by[:7]}\nresolved at {at}\n"


def _close_open_blocks(text: str) -> str:
    """Close a trailing open ``` fence or `<details>` block left by a
    truncating cut — the Grove summary used to slice the comment body at
    its first 10 lines with no regard for what those lines were IN THE
    MIDDLE of (Loki's audit, finding 8: it routinely ended inside an open
    fence)."""
    if text.count("```") % 2 == 1:
        text = text + "\n```"
    opens = text.count("<details>") - text.count("</details>")
    if opens > 0:
        text = text + ("\n</details>" * opens)
    return text


def _grove_summary(body: str, pr_url: str) -> str:
    """The Grove `#willow` line: the comment body capped by CHARACTERS
    (head+tail, never a naive first-N cut) at `GROVE_SUMMARY_CHAR_CAP` —
    never a line-based cut. Loki's re-audit: a 60-line head/tail cut let a
    real 59,815-char body straight through as a 59,864-char Grove message,
    because every line in a wrapped pytest failure block can run to 4 KB;
    "60 lines" was no cap at all on traffic shaped like that. Any fence or
    `<details>` left open by the cut is closed, then the PR link."""
    text = _head_tail_chars(body.rstrip("\n"), GROVE_SUMMARY_CHAR_CAP)
    text = _close_open_blocks(text.rstrip())
    return f"{text}\n{pr_url}"


def _ci_red_leg_info(repo: str, leg: dict) -> dict:
    """`{check, conclusion, url, block?, trimmed?, source?, log_truncated?}
    | {..., missing_permission} | {..., rate_limited} | {..., detail}` —
    the job's log, extracted, or the honest reason it has none. Never
    raises, and never a reason by itself to skip the comment — the caller
    always has a line to show for this leg. `leg["key"]` is
    `head_sha:check_run_id` and GitHub Actions job ids and check-run ids
    share one id space, so the check_run_id is a valid job id for the
    logs endpoint."""
    from willow_bot.steward import ci_log

    job_id = leg["key"].rsplit(":", 1)[-1]
    info: dict = {"check": leg["check"], "conclusion": leg["conclusion"], "url": leg["url"]}
    text, log_receipt = ci_log.fetch_job_log(repo, job_id)
    if log_receipt.get("missing_permission"):
        info["missing_permission"] = log_receipt["missing_permission"]
        return info
    if log_receipt.get("status") == "rate_limited":
        info["rate_limited"] = True
        info["detail"] = log_receipt.get("detail", "rate limited")
        return info
    if text is None:
        info["detail"] = log_receipt.get("detail", "unknown error")
        return info
    extracted = ci_log.extract_failure_block(text)
    info.update(block=extracted["block"], trimmed=extracted["trimmed"], source=extracted["source"],
                log_truncated=bool(log_receipt.get("truncated")))
    return info


def _ci_leg_needs_refetch(info: object) -> bool:
    """True for a leg whose stored info carries no failure block AND no
    recorded reason it has none — the exact shape ``strip_blocks`` (or an
    entry surviving to be rebuilt after a prune) leaves behind. Rendering
    a leg like this as-is prints a bare ``log unavailable (unknown
    error)`` for a job whose log the bot already had and threw away on
    purpose (Loki's re-audit, HIGH finding 1)."""
    return (isinstance(info, dict) and "_leg_id" in info and not info.get("superseded")
            and not info.get("block") and not info.get("missing_permission")
            and not info.get("rate_limited") and "detail" not in info)


def _ci_active_legs(entry: dict) -> list[dict]:
    """Every leg in `entry["legs"]` still worth rendering — excludes any
    leg a newer job id for the same check name has superseded (Loki's
    re-audit, HIGH finding 2). Superseded legs are kept in the table
    (never re-fetched, never rendered again) rather than deleted, so the
    table itself still shows what happened."""
    return [v for v in (entry.get("legs") or {}).values() if isinstance(v, dict) and not v.get("superseded")]


def _ci_owed_refetch_stripped(entry: dict, *, errors: list | None = None) -> None:
    """Re-fetch any leg in `entry["legs"]` whose block was stripped (or
    lost across a prune-and-rebuild) BEFORE the body is rendered from it.

    Loki's re-audit, HIGH finding 1: `strip_blocks` drops every leg's
    block once both channels land, to keep the owed table small; a LATER
    leg on the same head used to rebuild the body straight from that
    table without re-fetching the stripped one first — `tick.py`'s own
    comment claimed `_ci_owed_merge_new_legs` "re-fetches anyway", but
    that function only looks at legs newly reported THIS tick, and a
    stripped leg with nothing new to report is never among them, so the
    PR comment was edited to `_test: log unavailable (unknown error)_`
    for a job whose real block the bot had thrown away minutes earlier.
    This runs on every leg in the table, not just this tick's delta, so a
    stripped leg is always given the chance to be whole again before the
    comment is rebuilt.

    A re-fetch that itself fails is recorded as an honest
    `log unavailable: <reason>`, never a silent falsehood — and, per
    Loki's re-audit (LIMIT 1; probe H4), it must not become a PERMANENT
    falsehood either. If the leg still carries a real `block` from an
    earlier successful fetch (kept only when a leg that once had a block
    is asked to refetch again and fails), that block is kept and the
    rendered note says `(re-fetch failed: <reason>; showing earlier
    log)`, not "log unavailable". If there is no prior block to fall back
    on — the ordinary stripped-leg case — the failure reason is recorded
    under `refetch_failed_detail`, NEVER under `detail`: a leg carrying
    `detail` is `_ci_leg_needs_refetch`-ineligible forever, so storing a
    transient 502 there would make it sticky for the life of the entry
    (exactly what probe H4 caught). `refetch_failed_detail` renders the
    same honest line THIS tick and leaves the leg eligible for another
    real attempt next tick."""
    repo = entry.get("repo", "")
    legs_table = entry.get("legs") or {}
    for job_id, info in list(legs_table.items()):
        if not _ci_leg_needs_refetch(info):
            continue
        leg_id = info.get("_leg_id", "")
        key = leg_id.rsplit("::", 1)[0] if "::" in leg_id else leg_id
        try:
            fake_leg = {"key": key, "check": info.get("check", ""), "conclusion": info.get("conclusion"),
                        "url": info.get("url", "")}
            refreshed = _ci_red_leg_info(repo, fake_leg)
        except Exception as exc:  # noqa: BLE001 — a schema surprise on one leg must not kill the tick
            if errors is not None:
                errors.append({"key": entry.get("where", job_id), "step": "refetch_stripped",
                               "error": str(exc)[:300]})
            continue
        if refreshed.get("block") or refreshed.get("missing_permission") or refreshed.get("rate_limited"):
            refreshed["_leg_id"] = leg_id
            legs_table[job_id] = refreshed
            continue
        reason = refreshed.get("detail", "unknown error")
        if info.get("block"):
            info["refetch_failed_detail"] = reason
            continue
        refreshed.pop("detail", None)
        refreshed["_leg_id"] = leg_id
        refreshed["refetch_failed_detail"] = reason
        legs_table[job_id] = refreshed


def _ci_job_id_newer(a: str, b: str) -> bool:
    """True when GitHub Actions job/check-run id `a` is a NEWER run than
    `b`. Ids are assigned in increasing order by GitHub, so a numeric
    comparison — not the order two webhook deposits happened to land in
    the same tick — is what decides which of two legs sharing a check
    name is superseded (Loki's re-audit, LIMIT 3; probe C3 deposits job 9
    then job 1 in one tick, and job 1, the numerically older id, must
    still be the one marked superseded regardless of iteration order).
    Falls back to a plain string comparison if either id is not a plain
    integer, rather than raising."""
    try:
        return int(a) > int(b)
    except (TypeError, ValueError):
        return a > b


def _ci_owed_merge_new_legs(owed: dict, groups: dict, *, commented: list, errors: list | None = None) -> None:
    """Fold this tick's newly-seen legs into the durable owed table
    (`ci_comments.py`), independent of whatever the filing loop's
    `human_required_enqueue` call answers. A PR-less head or a stuck-only
    (every leg cancelled) head has nothing to show a log for and never
    gets an owed entry — reported directly here so the receipt still
    carries a line for it, matching what the filing loop used to say.

    Loki's re-audit (broker-refused-every-tick finding): a leg the filing
    loop never got to mark `ci_filed` (a refused `human_required_enqueue`)
    is regrouped every tick, so this used to re-fetch the job log and
    reset a `posted` comment to `pending` (attempts back to 0, defeating
    the backoff) EVERY tick for a leg that had not actually changed. A
    leg is now identified by `(job_id, conclusion)`; only a leg that is
    new to the entry or whose id changed (a re-run, a new conclusion) is
    re-fetched, and the comment is reset to `pending` only when at least
    one leg actually changed."""
    for head_key, legs in groups.items():
        first = legs[0]
        where = _ci_pr_key(first["repo"], first["pr"], first["head_sha"])
        if not first["pr"]:
            commented.append({"repo_pr": where, "head_sha": first["head_sha"], "comment_id": None,
                              "action": "skipped", "reason": "no pr"})
            continue
        real = [lg for lg in legs if lg["conclusion"] != _CI_CANCELLED]
        if not real:
            commented.append({"repo_pr": where, "head_sha": first["head_sha"], "comment_id": None,
                              "action": "skipped", "reason": "stuck, no failing leg to show a log for"})
            continue
        try:
            entry = ci_comments.entry_for(owed, head_key, repo=first["repo"], pr=first["pr"],
                                          head_sha=first["head_sha"], where=where)
            legs_table = entry.setdefault("legs", {})
            changed = False
            for lg in real:
                # Keyed by the check_run/job id, not the check NAME: a
                # re-run of the same check is a NEW leg id sharing the
                # old one's name, and two ids sharing one name-keyed slot
                # used to overwrite each other every tick for as long as
                # both stayed unfiled — one 5 MB-capable log fetch and one
                # comment edit per tick, forever (Loki's re-audit, HIGH
                # finding 2, probe C2: a refused broker plus a re-run).
                # A plain string, not a tuple: `_leg_id` round-trips through
                # `ci_comments.save`/`load` (JSON has no tuple type — a
                # tuple would silently come back as a list and never equal
                # itself again, re-fetching and resetting EVERY tick).
                job_id = str(lg.get("key", "")).rsplit(":", 1)[-1]
                leg_id = f"{lg.get('key')}::{lg.get('conclusion')}"
                check_name = lg["check"]
                prior = legs_table.get(job_id)
                if isinstance(prior, dict) and prior.get("superseded"):
                    continue  # a job id a newer run of this check replaced; never fetched again
                this_leg_superseded = False
                for other_id, other_info in legs_table.items():
                    if (other_id != job_id and isinstance(other_info, dict)
                            and other_info.get("check") == check_name and not other_info.get("superseded")):
                        # This check has run again under a new job id.
                        # Supersession is decided by JOB ID ORDER, not by
                        # which of the two happened to be iterated (or
                        # deposited) first this tick (Loki's re-audit,
                        # LIMIT 3; probe C3 deposits the newer id BEFORE
                        # the older one in the same tick, and the older id
                        # must still be the one superseded regardless).
                        # The numerically OLDER id is superseded: kept in
                        # the table (for the record), never fetched or
                        # rendered again.
                        if _ci_job_id_newer(job_id, other_id):
                            other_info["superseded"] = True
                            other_info.pop("block", None)
                            changed = True
                        else:
                            this_leg_superseded = True
                if this_leg_superseded:
                    if not (isinstance(prior, dict) and prior.get("_leg_id") == leg_id):
                        legs_table[job_id] = {"check": check_name, "conclusion": lg.get("conclusion"),
                                              "url": lg.get("url", ""), "_leg_id": leg_id, "superseded": True}
                        changed = True
                    continue
                if isinstance(prior, dict) and prior.get("_leg_id") == leg_id:
                    # This exact leg was already fetched; nothing new to
                    # show from THIS tick's deposit. A block stripped
                    # since then is rebuilt at render time
                    # (`_ci_owed_refetch_stripped`), not here — a leg with
                    # an unfiled broker replaying the SAME webhook event
                    # every tick (Loki's re-audit, HIGH finding 2) must
                    # never re-fetch just because its block is gone.
                    continue
                info = _ci_red_leg_info(first["repo"], lg)
                info["_leg_id"] = leg_id
                legs_table[job_id] = info
                changed = True
            entry["pending_kind"] = "red"
            cs = entry["comment"]
            if changed and cs["status"] not in ("pending", "stalled"):
                cs["status"] = "pending"
                cs["attempts"] = 0
                cs["last_error"] = None
                cs.pop("paused_until", None)
        except Exception as exc:  # noqa: BLE001 — a schema surprise on one head must not kill the tick
            if errors is not None:
                errors.append({"key": head_key, "step": "merge_new_legs", "error": str(exc)[:300]})


def _ci_owed_detect_green(owed: dict, heads: dict, head_legs: dict, *, at: str, errors: list | None = None) -> None:
    """A currently-`posted` (red) owed entry whose head has since gone
    green — by re-run, or by a later head on the same PR — is queued for
    a green edit. Reads only `heads`/`head_legs`, built from the bot's own
    deposits regardless of whether the broker is up, so this never depends
    on `ci_items` or a successful `human_required_resolve`.

    Gated on `cs.get("status") != "posted"`, on purpose: a comment that is
    still `pending` through a GitHub/Grove outage has never been posted at
    all, so there is no comment here to edit green yet. Loki's re-audit
    (LIMIT 4): if the head is already green by the time the outage clears,
    the FIRST post for it is still the red body (the pending entry's
    `pending_kind` stays `"red"`); this function flips it to green on the
    very next tick once the comment is `posted`, and it is retired the
    tick after that. Not a regression — pre-existing at 69068a6 — and
    self-correcting within one extra tick; the PR comment is the durable
    state carrier and never lands wrong for long. Grove only ever hears
    the red block for this head (it is not told about the green edit),
    which is a late alert, not a false state claim."""
    for head_key, entry in list(owed.items()):
        if not isinstance(entry, dict) or not entry.get("pr"):
            continue
        try:
            cs = entry.get("comment") or {}
            if cs.get("status") != "posted":
                continue
            repo, pr, head_sha = entry["repo"], entry["pr"], entry["head_sha"]
            # `entry["legs"]` is keyed by job id, not check name, since the
            # re-run flap fix — the expected set for green detection is
            # still names (matching `head_legs`), one per still-active leg.
            expected = {v.get("check") for v in _ci_active_legs(entry) if v.get("check")}
            if not expected:
                continue
            pr_key = _ci_pr_key(repo, pr, head_sha)
            own_key = _ci_head_key(repo, pr, head_sha)
            own = head_legs.get(own_key) or {}
            went_green_by = None
            if _head_is_green(own, expected):
                went_green_by = head_sha
            else:
                mine = (heads.get(pr_key) or {}).get(head_sha)
                for sha, seen_at in (heads.get(pr_key) or {}).items():
                    if sha == head_sha or seen_at is None or mine is None or seen_at <= mine:
                        continue
                    if _head_is_green(head_legs.get(_ci_head_key(repo, pr, sha)) or {}, expected):
                        went_green_by = sha
                        break
            if went_green_by is None:
                continue
            entry["pending_kind"] = "green"
            entry["green_by"] = went_green_by
            entry["green_at"] = at
            cs["status"] = "pending"
            cs["attempts"] = 0
            cs["last_error"] = None
            cs.pop("paused_until", None)
        except Exception as exc:  # noqa: BLE001 — a schema surprise on one head must not kill the tick
            if errors is not None:
                errors.append({"key": head_key, "step": "detect_green", "error": str(exc)[:300]})


def _grove_human_required_dedup_key(app: str, channel: str) -> str:
    """One key per (identity, channel) — sender always equals app_id in
    this model (``_grove_sender``/F2), so the identity half is the app_id
    itself, never a separately-tracked sender name."""
    return f"{app}::{channel}"


def _file_or_retry_human_required(owed: dict, *, app: str, channel: str, error: str,
                                  where: str, call) -> None:
    """File one ``human_required_enqueue`` item naming the exact fix for a
    permission-class (or sender-mismatch) Grove refusal — identity,
    channel, and the grant it lacks — the first time this (identity,
    channel) pair is seen blocked. Deduped in the SAME ci_comments table
    the blocked sub-state itself lives in, so ten red heads from one
    blocked identity file one item, not ten, and the dedup survives a
    restart the same way the blocked state does.

    Loki 738DB24E F3: the earlier cut discarded the enqueue call's own
    result and marked the key filed regardless — including on a
    ``{"error": "gate denied", ...}`` reply, which this very code path can
    itself provoke (a pre-manifest willow-bot has neither grove_write NOR
    human_required_enqueue). That permanently hid the desk item behind a
    dedupe key nothing had actually filed. Now: the key is marked
    ``filed`` (with the returned item id, for F4's resolve step) ONLY on a
    clean, non-error result; any other outcome — a raised exception or an
    ``{"error": ...}`` reply — is recorded ``could_not_run`` and is NOT
    treated as filed, so THIS SAME function retries it on a later call
    (the caller invokes it every tick for a still-blocked entry,
    independent of the Grove resend's own slower probe cadence — a broker
    hiccup should not cost ``BLOCKED_PROBE_TICKS``)."""
    dedup_key = _grove_human_required_dedup_key(app, channel)
    if ci_comments.human_required_is_filed(owed, dedup_key):
        return
    title = f"Grove refuses '{app}' on #{channel} ({error})"
    summary = (
        f"grove_send_message refused app_id '{app}' (posting as itself, the "
        f"only shape that ever sends) on channel '{channel}' with '{error}'. "
        f"First seen on {where}. Fix: grant this identity grove_write for "
        f"this channel."
    )
    try:
        result = call("human_required_enqueue", {
            "app_id": app, "kind": "review", "title": title[:200], "summary": summary,
            "priority": "normal", "source_ref": where,
        })
    except Exception as exc:  # noqa: BLE001 — not filed; retried next tick
        ci_comments.mark_human_required_could_not_run(owed, dedup_key, error=str(exc)[:400])
        return
    if isinstance(result, dict) and result.get("error"):
        ci_comments.mark_human_required_could_not_run(owed, dedup_key, error=str(result["error"])[:400])
        return
    item_id = result.get("id") if isinstance(result, dict) else None
    ci_comments.mark_human_required_filed(owed, dedup_key, item_id=item_id)


def _release_and_resolve_human_required(owed: dict, *, app: str, channel: str, call) -> str | None:
    """A block just cleared (a successful send landed): release the
    dedupe key so a LATER new refusal on this (identity, channel) files a
    fresh item, and resolve the queue item this block filed, if any (Loki
    738DB24E F4). Best-effort: if the steward's manifest does not (yet)
    hold ``human_required_resolve`` — plausible under a fresh willow-bot
    principal pre-4326FDFE — the item is left open in the queue; the
    dedupe key is released regardless, which is the part that actually
    matters for not staying silently stuck.

    Returns ``None`` when there was nothing to resolve or the resolve
    succeeded; otherwise the refusal reason (Loki 67536D9A C5: a refused
    ``human_required_resolve`` used to be dropped with no receipt line at
    all — the desk item stays open with an already-released dedupe key,
    invisible). The caller folds a non-``None`` return into the spoke
    receipt so the interim (pre-4326FDFE) case is at least visible."""
    dedup_key = _grove_human_required_dedup_key(app, channel)
    item_id = ci_comments.release_human_required(owed, dedup_key)
    if not item_id:
        return None
    try:
        result = call("human_required_resolve", {
            "app_id": app, "item_id": item_id, "status": "resolved",
            "note": "Grove send succeeded — the grant this item asked for is confirmed.",
        })
    except Exception as exc:  # noqa: BLE001 — best-effort; the dedupe key is already released
        return str(exc)[:300]
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])[:300]
    return None


def _ci_owed_drain(owed: dict, *, tick: int, app: str, grove_pace: "_Pacer", call,
                   enable_mcp: bool, commented: list, spoke: list, errors: list | None = None) -> None:
    """Retry every owed comment and Grove line that is due — new this
    tick or carried over from any earlier one. The GitHub half never
    needs `call`/`enable_mcp`; only the Grove half does, and when the
    broker is down it is reported `unreachable` rather than silently
    skipped.

    Loki's re-audit, HIGH finding: the Grove line used to be gated on the
    comment's own status (`posted`/`green`) — a GitHub outage silenced
    Grove too, even though the Grove line needs nothing from GitHub: it
    is built straight from the entry's own legs and the PR URL. That gate
    is gone; the two channels are now fully independent, each with its
    own `due()`/backoff/stall state.

    `grove_pace` is this drain's OWN `_Pacer`, never the filing loop's
    (Loki's re-audit, MEDIUM finding 3): sharing one pacer let a hot
    limiter's Grove waits burn the same wall-clock deadline the filing
    loop needed, starving `human_required_enqueue` for a tick by chat
    lines (probe I). A `rate_limited` result — from GitHub, or from
    `grove_pace` itself giving up on its own budget — is not a failure:
    neither calls `record_failure` (no attempt burned) —
    `record_rate_limited` honours `Retry-After` (or, for the pacer's own
    give-up, one tick) instead. Every per-entry step is guarded so a
    schema surprise on one head cannot kill the whole drain for every
    other head this tick."""
    from willow_bot import pr_voice

    # Loki 67536D9A C4: the enqueue retry loop below runs once per BLOCKED
    # head — paced to one attempt per (identity, channel) PER TICK here, so
    # N blocked heads sharing one dedup key (the pre-manifest willow-bot
    # case) cost one refused enqueue call this tick, not N.
    _enqueue_retried_this_tick: set[str] = set()

    for head_key in ci_comments.head_keys(owed):
        entry = owed[head_key]
        try:
            repo, pr, head_sha, where = entry["repo"], entry["pr"], entry["head_sha"], entry["where"]
            cs = entry.get("comment") or {}
            if ci_comments.due(cs, tick=tick):
                if entry.get("pending_kind") == "green":
                    body = _ci_red_green_body(where, head_sha, by=entry.get("green_by", head_sha),
                                              at=entry.get("green_at", ""))
                else:
                    _ci_owed_refetch_stripped(entry, errors=errors)
                    body = _ci_red_body(where, head_sha, _ci_active_legs(entry))
                result = pr_voice.upsert_ci_red_comment(repo, int(pr), head_sha, body)
                if result.get("status") == "ok":
                    new_status = "green" if entry.get("pending_kind") == "green" else "posted"
                    ci_comments.record_success(cs, status=new_status, tick=tick)
                    cs["comment_id"] = result.get("comment_id")
                    entry.pop("pending_kind", None)
                    commented.append({"repo_pr": where, "head_sha": head_sha,
                                      "comment_id": result.get("comment_id"), "action": result.get("action"),
                                      "reason": None})
                elif result.get("status") == "rate_limited":
                    ci_comments.record_rate_limited(cs, retry_after=result.get("retry_after"), tick=tick)
                    commented.append({"repo_pr": where, "head_sha": head_sha, "comment_id": None,
                                      "action": "skipped",
                                      "reason": f"rate limited, paused until tick {cs.get('paused_until')}"})
                else:
                    reason = (f"missing permission: {result['missing_permission']}"
                             if result.get("missing_permission")
                             else result.get("detail", "unknown error"))
                    ci_comments.record_failure(cs, error=reason, tick=tick)
                    commented.append({"repo_pr": where, "head_sha": head_sha, "comment_id": None,
                                      "action": "skipped", "reason": reason})

            sp = entry.get("spoke") or {}
            if (sp.get("status") not in ("pending", "stalled", "blocked")
                    or not repo.startswith(_GROVE_CI_RED_REPO_PREFIX)):
                continue
            if not enable_mcp or call is None:
                spoke.append({"channel": "willow", "state": "unreachable"})
                continue

            # A not-yet-filed human_required item is retried EVERY tick
            # this entry is blocked — independent of the Grove resend's
            # own slower due()/BLOCKED_PROBE_TICKS cadence below (Loki
            # 738DB24E F3): filing is cheap, and a broker hiccup on the
            # enqueue call itself must not cost 48 ticks on top of
            # whatever already blocked the send.
            if sp.get("status") == "blocked":
                dedup_key = _grove_human_required_dedup_key(app, "willow")
                if dedup_key not in _enqueue_retried_this_tick:
                    _enqueue_retried_this_tick.add(dedup_key)
                    _file_or_retry_human_required(
                        owed, app=app, channel="willow", error=sp.get("last_error") or "blocked",
                        where=where, call=call,
                    )

            fingerprint = _manifest_fingerprint(app)
            if not ci_comments.due(sp, tick=tick, manifest_fingerprint=fingerprint):
                continue

            sender = _grove_sender()
            spoke_receipt = {"channel": "willow", "grove_sender": sender}
            mismatch = _grove_sender_mismatch()
            if mismatch is not None:
                # Refuse outright — never substitute the app_id and send
                # under its name as though the override had been honored
                # (Loki 738DB24E F2): no willow-mcp call is made at all.
                reason = f"sender_mismatch: WILLOW_BOT_GROVE_SENDER={mismatch!r} != app_id={sender!r}"
                ci_comments.record_blocked(sp, error=reason, tick=tick, manifest_fingerprint=fingerprint)
                dedup_key = _grove_human_required_dedup_key(app, "willow")
                if dedup_key not in _enqueue_retried_this_tick:
                    _enqueue_retried_this_tick.add(dedup_key)
                    _file_or_retry_human_required(
                        owed, app=app, channel="willow", error=reason, where=where, call=call,
                    )
                spoke.append({**spoke_receipt, "ok": False, "reason": reason, "blocked": True})
                continue

            pr_url = f"https://github.com/{repo}/pull/{pr}"
            body_for_summary = _ci_red_body(where, head_sha, _ci_active_legs(entry))
            summary = _grove_summary(body_for_summary, pr_url)
            was_blocked = sp.get("status") == "blocked"
            result, err = grove_pace.call(call, "grove_send_message", {
                "app_id": app, "channel_name": "willow", "content": summary, "sender": sender,
            })
            # Every attempt's receipt line names the identity and channel it
            # used, win or lose (assignment step 5) — the pair a grant gets
            # written for comes straight from this line, not a guess.
            if err is None:
                ci_comments.record_success(sp, status="posted", tick=tick)
                unresolved = (_release_and_resolve_human_required(owed, app=app, channel="willow", call=call)
                             if was_blocked else None)
                if cs.get("status") in ("posted", "green"):
                    # Both channels have now landed at least once for this
                    # head — the failure blocks are only needed to build a
                    # comment/Grove body, and this entry is done doing that
                    # until something ABOUT it changes (a new leg, a green
                    # edit, a prune-and-rebuild), which re-fetches anyway
                    # (`_ci_owed_refetch_stripped`, called above).
                    ci_comments.strip_blocks(entry)
                ok_receipt = {**spoke_receipt, "ok": True}
                if unresolved:
                    ok_receipt["human_required_unresolved"] = unresolved
                spoke.append(ok_receipt)
            elif err.startswith("rate_limited"):
                # The pacer's own give-up (budget/cap spent) reads exactly
                # like GitHub's rate limit to this entry: a pause, never a
                # failure (Loki's re-audit, MEDIUM finding 3, probe F2 —
                # the 403-burns-an-attempt defect, transposed to Grove).
                ci_comments.record_rate_limited(sp, retry_after=None, tick=tick)
                spoke.append({**spoke_receipt, "ok": False, "reason": err, "rate_limited": True})
            elif ci_comments.is_permission_error(err):
                # A permission-class refusal is terminal for THIS
                # (head, channel) until the reason changes — one attempt,
                # then quiet (`due()` re-probes on BLOCKED_PROBE_TICKS or a
                # manifest fingerprint change, never every tick). File it
                # where the desk reads: one human_required item per
                # (identity, channel), deduped so every red head from the
                # same blocked identity is not its own ticket.
                ci_comments.record_blocked(sp, error=err, tick=tick, manifest_fingerprint=fingerprint)
                dedup_key = _grove_human_required_dedup_key(app, "willow")
                if dedup_key not in _enqueue_retried_this_tick:
                    _enqueue_retried_this_tick.add(dedup_key)
                    _file_or_retry_human_required(
                        owed, app=app, channel="willow", error=err, where=where, call=call,
                    )
                spoke.append({**spoke_receipt, "ok": False, "reason": err, "blocked": True})
            else:
                ci_comments.record_failure(sp, error=err, tick=tick)
                spoke.append({**spoke_receipt, "ok": False, "reason": err})
        except Exception as exc:  # noqa: BLE001 — one head's surprise must not stop the whole drain
            if errors is not None:
                errors.append({"key": head_key, "step": "drain", "error": str(exc)[:300]})


def _ci_where(item: dict) -> str:
    return _ci_pr_key(item.get("repo", ""), item.get("pr"), item.get("head_sha", ""))


def _ci_line_filed(item: dict) -> str:
    """The one line the seat reads. `CI red: repo#pr @ sha7 — leg, leg —
    url`; a stuck-only item says `CI stuck:` and why (nothing failed,
    nothing superseded it)."""
    where = _ci_where(item)
    sha7 = (item.get("head_sha") or "")[:7]
    legs = ", ".join(item.get("legs") or [])
    url = item.get("url") or ""
    tail = f" — {url}" if url else ""
    if item.get("stuck"):
        minutes = item.get("grace_min") or _CI_CANCELLED_GRACE_MIN_DEFAULT
        return f"CI stuck: {where} @ {sha7} — {legs} cancelled, no successor after {minutes} min{tail}"
    return f"CI red: {where} @ {sha7} — {legs}{tail}"


def _ci_line_resolved(item: dict) -> str:
    where = _ci_where(item)
    sha7 = (item.get("head_sha") or "")[:7]
    done = item.get("resolved") or {}
    how = done.get("how") or "resolved"
    by = (done.get("by") or "")[:7]
    why = {
        "re-run": "re-run green",
        "superseded": f"superseded by {by}, green",
        "closed-merged": "PR merged",
        "closed": "PR closed",
        "superseded-on-branch": f"superseded by {by} on the same branch",
    }.get(how, how)
    return f"CI resolved: {where} @ {sha7} — {why}"


def _notify_watchers(items: dict, state: dict, receipt: dict, *, pace: "_Pacer", app: str, call,
                     just_filed: set[str], just_resolved: set[str], state_view: dict) -> dict:
    """One Grove message per (item, filed|resolved) to the seat that opened
    the PR, plus the bot's per-head PR comment kept current — for WATCHED
    PRs only. Returns the new `ci_notified` table; fills `receipt["notified"]`
    and `receipt["notified_counts"]`.

    Three-state per attempt: `sent` (Grove took it; marker written),
    `refused` (the tool refused or the PR comment could not land; no
    marker, retried next tick), `skipped` (no watch row / no PR / the
    steward's own CI-red Grove block already covers this channel for a
    "filed" item — said only for items filed or resolved THIS tick, so an
    unwatched fleet does not fill every receipt), `stopped` (this step's
    budget ran out first; no marker, retried next tick).

    Loki's re-audit, MEDIUM finding, probe J: for a "filed" (red/stuck)
    item whose repo the steward's own `ci_comments` drain speaks for
    unconditionally, into this SAME channel, a watched PR used to hear
    BOTH this one-liner AND that multi-line failure-summary block — two
    Grove messages for one red head. The block carries the actual failure
    detail; this one-liner steps aside for it rather than doubling the
    voice. A "resolved" item is unaffected — the block never speaks again
    once posted (Loki's re-audit, finding K), so the follow-up here is
    still the only word of it.
    """
    from willow_bot.steward import pr_watch

    notified: dict = {k: dict(v) for k, v in (state.get("ci_notified") or {}).items()
                      if isinstance(v, dict)}
    table = pr_watch.load()
    sender = _grove_sender()
    lines: list[dict] = []
    counts = {"sent": 0, "refused": 0, "skipped": 0, "stopped": 0}

    def _say(kind: str, item: dict, line: str) -> None:
        item_id = item.get("id") or ""
        watcher = pr_watch.watcher_for(item.get("repo", ""), item.get("pr"), table)
        entry: dict = {"item_id": item_id, "where": _ci_where(item), "kind": kind}
        if watcher is None:
            if item_id in just_filed or item_id in just_resolved:
                entry.update(state="skipped", reason="no watch row" if item.get("pr") else "no pr")
                lines.append(entry)
                counts["skipped"] += 1
            return
        channel = str(watcher.get("channel") or "")
        entry["channel"] = channel
        if (kind == "filed" and not item.get("stuck")
                and str(item.get("repo") or "").startswith(_GROVE_CI_RED_REPO_PREFIX)
                and channel.lstrip("#") == "willow"):
            entry.update(state="skipped", reason="ci-red block covers this channel")
            entry["comment"] = _comment_on_pr(item, state_view, at=receipt["at"])
            notified.setdefault(item_id, {})[kind] = receipt["at"]
            lines.append(entry)
            counts["skipped"] += 1
            return
        if pace.budget_spent:
            entry.update(state="stopped", reason="ci budget spent")
            lines.append(entry)
            counts["stopped"] += 1
            return
        result, err = pace.call(call, "grove_send_message", {
            "app_id": app, "channel_name": channel.lstrip("#"), "content": line, "sender": sender,
        })
        if err:
            entry.update(state="stopped" if pace.budget_spent else "refused", reason=err)
            lines.append(entry)
            counts["stopped" if pace.budget_spent else "refused"] += 1
            return
        entry["state"] = "sent"
        entry["comment"] = _comment_on_pr(item, state_view, at=receipt["at"])
        notified.setdefault(item_id, {})[kind] = receipt["at"]
        lines.append(entry)
        counts["sent"] += 1

    for item in items.values():
        item_id = item.get("id")
        if not item_id or item_id == "filed":
            continue
        marks = notified.get(item_id) or {}
        if item.get("resolved"):
            if "resolved" not in marks:
                _say("resolved", item, _ci_line_resolved(item))
            continue
        if "filed" not in marks:
            _say("filed", item, _ci_line_filed(item))

    receipt["notified"] = lines
    receipt["notified_counts"] = counts
    return notified


def _comment_on_pr(item: dict, state_view: dict, *, at: str) -> dict:
    """Keep the bot's ONE per-head status comment current with the CI line
    (acfd27ae3259 item 1). Same marker, same body builder as the voice
    step, so the two writers converge on one comment per head sha — never
    a second comment. Direct GitHub call (App token), not the limiter.
    Three-state receipt, never raises."""
    if not item.get("pr"):
        return {"state": "skipped", "reason": "no pr"}
    try:
        from willow_bot import pr_voice
        from willow_bot.steward import voice

        key = f"{item['repo']}#{item['pr']}"
        body = voice._status_comment_body(key, item["head_sha"], state_view, at=at)
        result = pr_voice.upsert_status_comment(item["repo"], int(item["pr"]), item["head_sha"], body)
    except Exception as exc:  # noqa: BLE001 — a comment that cannot land is a line, not a raise
        return {"state": "refused", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    if result.get("status") == "ok":
        return {"state": "sent", "action": result.get("action"), "url": result.get("url", "")}
    return {"state": "refused", "reason": str(result.get("detail", ""))[:300]}


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
                       commented=[], spoke=[],
                       detail=f"no deposits file at {src}", remaining=0, **_Pacer.idle())
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
    # `ci_filed` holds RED legs only (head_sha:check_run_id → item id) — voice
    # derives the `ci-red` label from its prefixes, so a cancelled leg must
    # never land there. Stuck cancelled legs are remembered in
    # `ci_filed_cancelled` under the same key shape for the same idempotence.
    filed_before = dict(state.get("ci_filed") or {})
    filed_cancelled_before = dict(state.get("ci_filed_cancelled") or {})
    already = set(filed_before) | set(filed_cancelled_before)
    items = {k: dict(v) for k, v in (state.get("ci_items") or {}).items()}
    heads = {k: dict(v) for k, v in (state.get("ci_heads") or {}).items()}
    head_legs = {k: dict(v) for k, v in (state.get("ci_head_legs") or {}).items()}
    cancelled_pending = {k: dict(v) for k, v in (state.get("ci_cancelled_pending") or {}).items()}
    now = _ci_clock()

    # Read first: every row is in the receipt whether or not it can be filed.
    # Voided rows (an `annul` row names them; gap 9) are skipped and
    # counted. The void set is built over the WHOLE file before the pass:
    # an annul follows what it voids, and a row read before its annul in
    # the same pass would already have been filed.
    from willow_bot.deposits import is_annul, void_set_before

    voids = void_set_before(src)
    annulled_skipped = 0
    voided_heads: set[tuple[str, str]] = set()
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
                if isinstance(rec, dict) and is_annul(rec):
                    # The heads this annul empties: every head whose every
                    # deposit is now voided drops out of `ci_heads` on this
                    # tick's prune (Loki 346276F9: PR-less groups never
                    # pruned — a fixture head is exactly that case).
                    voided_heads.update(_heads_fully_voided(src, voids))
                    rec = None
                elif isinstance(rec, dict) and voids.voided(rec):
                    annulled_skipped += 1
                    rec = None
                if isinstance(rec, dict) and rec.get("head_sha"):
                    leg = _leg_of(rec)
                    pr_key = _ci_pr_key(leg["repo"], leg["pr"], leg["head_sha"])
                    head_key = _ci_head_key(leg["repo"], leg["pr"], leg["head_sha"])
                    at = _ci_epoch(leg["received_at"])
                    # Every head the bot has seen for this PR, with the
                    # received_at of its first deposit — the successor
                    # evidence for a cancelled leg.
                    seen = heads.setdefault(pr_key, {})
                    if leg["head_sha"] not in seen or (at is not None and seen[leg["head_sha"]] is None):
                        seen[leg["head_sha"]] = at
                    # A PR-less head is ALSO remembered under its branch:
                    # that is the only group in which its successor (the
                    # next push to the same branch) can ever be found.
                    if not leg["pr"] and leg["head_branch"]:
                        on_branch = heads.setdefault(_ci_branch_key(leg["repo"], leg["head_branch"]), {})
                        if leg["head_sha"] not in on_branch or (at is not None and on_branch[leg["head_sha"]] is None):
                            on_branch[leg["head_sha"]] = at
                    # Latest conclusion per leg NAME per head — a re-run of
                    # a leg (new check_run_id) overwrites, so a head that
                    # went green by re-running reads green here.
                    head_legs.setdefault(head_key, {})[leg["check"]] = leg["conclusion"]
                    if leg["conclusion"] in _CI_RED:
                        red.append(leg)
                    elif leg["conclusion"] == _CI_CANCELLED and leg["key"] not in already:
                        cancelled_pending.setdefault(leg["key"], {**leg, "pending_since": now})
                scanned += 1
            offset = fh.tell()
    receipt["red"] = [_public(r) for r in red]
    receipt["annulled"] = annulled_skipped

    grace_s = _ci_grace_s()
    # Closure evidence: the inbox's durable `pr_closed` record (which knows
    # merged from closed-without-merge), plus what the host sync brought
    # home — `merged_synced` holds closed-not-merged PRs too (merge.py's
    # pr_closed_not_merged), so a key known only from there is `closed`,
    # not `merged` (Loki 095AF9DB).
    closed = {k: v for k, v in (state.get("pr_closed") or {}).items() if isinstance(v, dict)}
    for key in state.get("merged_synced") or []:
        closed.setdefault(key, {"merged": None})
    decided = _decide_cancelled(cancelled_pending, heads, now=now, grace_s=grace_s, head_legs=head_legs,
                                closed=closed)
    receipt["cancelled"] = [
        {"repo": d["repo"], "pr": d["pr"], "head_sha": d["head_sha"], "leg": d["check"], "state": d["state"],
         **({k: d[k] for k in ("successor", "aged_by", "latest", "closed") if k in d})}
        for d in decided.values()
    ]
    stuck = [d for d in decided.values() if d["state"] == "stuck"]
    # Superseded, rerun, moot and stuck legs leave the pending set; waiting/unreachable stay.
    cancelled_pending = {k: {kk: vv for kk, vv in d.items()
                             if kk not in ("state", "successor", "aged_by", "latest", "closed")}
                         for k, d in decided.items() if d["state"] in ("waiting", "unreachable")}

    # One item per head: group the legs to file (reds + stuck cancelled) by head key.
    groups: dict[str, list[dict]] = {}
    for leg in red + stuck:
        if leg["key"] in already:
            continue
        groups.setdefault(_ci_head_key(leg["repo"], leg["pr"], leg["head_sha"]), []).append(leg)
    skipped = sum(1 for leg in red + stuck if leg["key"] in already)

    # The CI-red comment and the Grove line: owed per (repo, pr, head_sha)
    # until they land, in their OWN small atomically-written file
    # (`ci_comments.py`), independent of `human_required_enqueue` and of
    # whether the broker (WILLOW_BOT_MCP) is even enabled — a refused or
    # absent broker never prevents the GitHub comment. Run before the
    # filing loop below, whose own success or failure this no longer
    # depends on.
    app = _resolve_app_id()
    grove_pace = _Pacer(_CI_TIME_BUDGET_S)
    call = None
    if enable_mcp:
        from willow_bot.steward import mcp_client

        call = mcp_client.call
    owed, corrupt_reason = ci_comments.load()
    quarantined = None
    commented: list = []
    spoke: list = []
    drain_errors: list = []
    retired: list = []
    unreachable = bool(corrupt_reason) and corrupt_reason.startswith("unreachable")
    if unreachable:
        # Loki's re-audit, MEDIUM finding 4: an unreadable-but-PRESENT
        # table (EACCES, EIO, ...) is not a missing one. Merging,
        # draining, retiring, pruning, and above all `save()`'s own
        # `os.replace` over a table this process never actually read
        # would each silently forget every owed head — the exact silent
        # `{}` this module's docstring claims to have removed, reachable
        # by a permissions error rather than a test's corrupt-json fixture.
        # Skip the whole drain this tick; the file is left exactly as it
        # was for the next tick (once whatever broke the read is fixed).
        #
        # Loki's re-audit, LIMIT 2: a head first SEEN this tick (`groups`)
        # is still filed into `ci_filed`/`ci_items` below — the filing
        # loop does not know or care whether `ci_comments.json` is
        # readable — so once this tick's webhook data is gone, that head
        # never appears in `groups` again and, before this fix, was never
        # owed a comment or Grove line: silent forever. The head's legs
        # are recorded here in the tick's OWN state file
        # (`ci_owed_deferred`, in `state`, not `ci_comments.json` — that
        # file is precisely what is unreachable right now) so the next
        # tick that CAN read the table folds them in as if newly seen.
        deferred = state.setdefault("ci_owed_deferred", {})
        for head_key, legs in groups.items():
            deferred[head_key] = legs
        receipt["ci_comments_table"] = {"status": "unreachable", "reason": corrupt_reason,
                                        "drain_skipped": True,
                                        "deferred": sorted(deferred.keys())}
    else:
        if corrupt_reason:
            # Never silently read a corrupt table as {} and forget every owed
            # head with no trace (Loki's re-audit, drain-guard finding): move
            # the unreadable file aside and start a fresh table, with a
            # receipt line naming exactly what happened.
            quarantined = ci_comments.quarantine_corrupt()
        receipt["ci_comments_table"] = (
            {"status": "unreadable", "reason": corrupt_reason, "quarantined": quarantined}
            if corrupt_reason else {"status": "ok"}
        )
        # A head first seen while the table was unreachable (LIMIT 2,
        # above) is owed now that it can be read again — folded into this
        # tick's groups (a COPY, so the filing loop's own `groups` below
        # is untouched: those legs were already filed, and re-adding them
        # there would re-file, not just re-comment).
        owed_deferred = state.pop("ci_owed_deferred", None) or {}
        owed_groups = dict(groups)
        for head_key, legs in owed_deferred.items():
            bucket = list(owed_groups.get(head_key, []))
            seen_keys = {lg.get("key") for lg in bucket}
            bucket.extend(lg for lg in legs if lg.get("key") not in seen_keys)
            owed_groups[head_key] = bucket
        if owed_deferred:
            receipt["ci_owed_deferred_recovered"] = sorted(owed_deferred.keys())
        tick_n = ci_comments.next_tick(owed)
        _ci_owed_merge_new_legs(owed, owed_groups, commented=commented, errors=drain_errors)
        _ci_owed_detect_green(owed, heads, head_legs, at=receipt["at"], errors=drain_errors)
        _ci_owed_drain(owed, tick=tick_n, app=app, grove_pace=grove_pace, call=call, enable_mcp=enable_mcp,
                      commented=commented, spoke=spoke, errors=drain_errors)
        if drain_errors:
            receipt["ci_comments_errors"] = drain_errors
        retired = ci_comments.retire_check(owed, closed=closed, tick=tick_n)
        retired += ci_comments.prune_old(owed, now_epoch=now)
        if retired:
            receipt["retired"] = retired
        ci_comments.save(owed)
    receipt["stalled"] = ci_comments.stalled_report(owed)
    receipt["blocked"] = ci_comments.blocked_report(owed)

    # Resolve-on-green candidates. Two honest routes to "this item is done":
    # a LATER head for the same PR on which every leg the item's head
    # recorded has reported green (the item's leg set is the only expected
    # set the bot knows), or the SAME head re-run to green (every leg name
    # it ever recorded now reads green). Never on a single early green leg.
    #
    # A STUCK-ONLY item (every leg it filed was a cancellation — nothing
    # failed, nothing finished) has two more honest routes, the same two
    # that make a pending cancelled leg moot or superseded (Loki 095AF9DB:
    # an item filed as stuck at tick N stayed open when the PR merged at
    # tick N+1, and no green head ever comes for a cancelled run): the PR
    # closed, or — for a PR-less head — a later head landed on its branch.
    #
    # Computed AFTER this tick's legs have joined their items (Loki
    # FE91FF0E): a red and a close arriving in the same tick used to
    # resolve the item moot with the red live, because the candidates were
    # read before the filing loop set `stuck = False`. The MCP-off path
    # calls it early only to report what it WOULD resolve.
    def _resolve_candidates(filed_map: dict, filed_cancelled_map: dict) -> list[tuple[str, dict, str, str]]:
        out: list[tuple[str, dict, str, str]] = []
        for item_key, item in items.items():
            if item.get("resolved"):
                continue
            pr_key = _ci_pr_key(item["repo"], item["pr"], item["head_sha"])
            own_key = _ci_head_key(item["repo"], item["pr"], item["head_sha"])
            expected = set((head_legs.get(own_key) or {}).keys())
            own = head_legs.get(own_key) or {}
            if _head_is_green(own, expected):
                out.append((item_key, item, item["head_sha"], "re-run"))
                continue
            mine = (heads.get(pr_key) or {}).get(item["head_sha"])
            found = False
            for sha, at in (heads.get(pr_key) or {}).items():
                if sha == item["head_sha"] or at is None or mine is None or at <= mine:
                    continue
                if _head_is_green(head_legs.get(_ci_head_key(item["repo"], item["pr"], sha)) or {}, expected):
                    out.append((item_key, item, sha, "superseded"))
                    found = True
                    break
            if found or not _item_is_stuck_only(item, filed_map, filed_cancelled_map):
                continue
            if item["pr"] and pr_key in closed:
                how = "closed-merged" if closed[pr_key].get("merged") else "closed"
                out.append((item_key, item, item["head_sha"], how))
                continue
            if not item["pr"] and item.get("branch"):
                on_branch = heads.get(_ci_branch_key(item["repo"], item["branch"])) or {}
                mine_b = on_branch.get(item["head_sha"])
                later = [s for s, at in on_branch.items()
                         if s != item["head_sha"] and at is not None and mine_b is not None and at > mine_b]
                if later:
                    out.append((item_key, item, sorted(later, key=lambda s: on_branch[s])[-1],
                                "superseded-on-branch"))
        return out

    def _persist() -> None:
        # A resolved item whose `resolved` line is still owed to a watching
        # seat stays one more tick (Loki 717E236C: a refused send at tick N
        # was pruned at N+1 and never delivered); `ci_notified` is pruned
        # with the items so it does not grow for the life of the file.
        owed = _resolved_lines_owed(items, state)
        pruned = _prune_ci_state(items, heads, head_legs, cancelled_pending,
                                 voided_heads=voided_heads, keep_items=owed)
        if pruned["heads"] or pruned["items"] or pruned.get("voided_heads"):
            receipt["pruned"] = pruned
        live_ids = {it.get("id") for it in items.values() if it.get("id")}
        state["ci_notified"] = {k: v for k, v in (state.get("ci_notified") or {}).items() if k in live_ids}
        state.pop("ci_red_comment", None)  # superseded by ci_comments.json (owed per head, not per item)
        state.pop("ci_red_spoken", None)
        state["ci_filed"] = filed_now
        state["ci_filed_cancelled"] = filed_cancelled_now
        state["ci_items"] = items
        state["ci_heads"] = heads
        state["ci_head_legs"] = head_legs
        state["ci_cancelled_pending"] = cancelled_pending
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")

    def _remember(leg: dict, item_id: str) -> None:
        if leg["conclusion"] == _CI_CANCELLED:
            filed_cancelled_now[leg["key"]] = item_id
        else:
            filed_now[leg["key"]] = item_id

    filed_now = dict(filed_before)
    filed_cancelled_now = dict(filed_cancelled_before)
    if not enable_mcp:
        _persist()
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — reds reported, not filed",
                       filed=[], appended=[], resolved=[], refused=[], skipped=skipped, new_offset=offset,
                       commented=commented, spoke=spoke,
                       would_resolve=[{"where": k, "superseded_by": sha, "how": how}
                                      for k, _, sha, how in _resolve_candidates(filed_before, filed_cancelled_before)],
                       remaining=len(groups), **_Pacer.idle())
        return _emit(receipt)

    # The filing loop's OWN pacer — constructed here, not reused from the
    # Grove drain above, so its wall-clock deadline starts fresh from NOW
    # rather than from whatever the drain's own pacing already spent
    # (Loki's re-audit, MEDIUM finding 3).
    pace = _Pacer(_CI_TIME_BUDGET_S)
    filed, appended, refused = [], [], []
    to_file = sum(1 for hk in groups if not (items.get(hk) or {}).get("id"))
    for head_key, legs in groups.items():
        first = legs[0]
        where = _ci_pr_key(first["repo"], first["pr"], first["head_sha"])
        existing = items.get(head_key)
        if existing and existing.get("id"):
            # The head already has its item; a new leg joins it, no second filing.
            for lg in legs:
                _remember(lg, existing["id"])
                names = existing.setdefault("legs", [])
                if lg["check"] not in names:  # a re-run under a new check_run_id is the same leg
                    names.append(lg["check"])
                if lg["conclusion"] != _CI_CANCELLED:
                    existing["stuck"] = False  # a red joined: no longer a stuck-only item
                appended.append({"where": where, "check": lg["check"], "conclusion": lg["conclusion"],
                                 "id": existing["id"]})
            continue
        args = _filing_args(app, where, first["head_sha"], legs, grace_s=grace_s)
        # Paced through this step's own budget (gap 52928edb3fc7): a
        # rate_limited answer is waited out, not treated as the filing's
        # refusal — the first live tick after the mirror's pacing landed
        # ended here `could-not-run` on a real red.
        result, err = pace.call(call, "human_required_enqueue", args)
        if err:
            # A budget stop is a pause, not a refusal (Loki 095AF9DB): the
            # step stops here and says so under `stopped`; `refused` holds
            # only what the tool actually refused.
            if pace.budget_spent:
                receipt["stopped"] = {"at": where, "reason": err}
            else:
                refused.append({"where": where, "legs": [lg["check"] for lg in legs], "error": err})
            break
        item_id = (result.get("id") if isinstance(result, dict) else None) or "filed"
        names: list[str] = []
        for lg in legs:
            _remember(lg, item_id)
            if lg["check"] not in names:
                names.append(lg["check"])
        causes = [lg for lg in legs if lg["check"] not in _CI_AGGREGATE_CHECKS] or legs
        items[head_key] = {"id": item_id, "repo": first["repo"], "pr": first["pr"],
                           "head_sha": first["head_sha"], "legs": names, "filed_at": receipt["at"],
                           "stuck": all(lg["conclusion"] == _CI_CANCELLED for lg in legs),
                           "branch": first["head_branch"],
                           # The line the seat and the PR comment read (pair 11ccb0f7).
                           "url": causes[0]["url"] or legs[0]["url"],
                           "grace_min": int(round(grace_s / 60.0)) if grace_s else _CI_CANCELLED_GRACE_MIN_DEFAULT}
        filed.append({"where": where, "head_sha": first["head_sha"], "legs": [lg["check"] for lg in legs],
                      "conclusions": [lg["conclusion"] for lg in legs], "id": item_id})

    resolved, resolve_refused = [], []
    # After the filing loop: this tick's reds have joined their items and
    # cleared `stuck` where they landed, so a closed PR with a live red on
    # it is not a candidate.
    for item_key, item, sha, how in _resolve_candidates(filed_now, filed_cancelled_now):
        if pace.budget_spent:
            break  # the rest resolve next tick; the candidates are recomputed from state
        note = {
            "re-run": f"re-run green at {receipt['at']}",
            "superseded": f"superseded by {sha}, green at {receipt['at']}",
            "closed-merged": f"moot: PR merged; stuck cancelled run at {receipt['at']}",
            "closed": f"moot: PR closed; stuck cancelled run at {receipt['at']}",
            "superseded-on-branch": f"superseded by {sha} on the same branch; stuck cancelled run at {receipt['at']}",
        }[how]
        result, err = pace.call(call, "human_required_resolve", {
            "app_id": app, "item_id": item["id"], "status": "resolved", "note": note,
        })
        if err:
            if pace.budget_spent:
                receipt.setdefault("stopped", {"at": item_key, "reason": err})
                break
            # A refused resolve is a line; the item stays open and is retried.
            resolve_refused.append({"where": item_key, "item_id": item["id"], "error": err})
            continue
        item["resolved"] = {"by": sha, "how": how, "at": receipt["at"]}
        resolved.append({"where": item_key, "item_id": item["id"], "superseded_by": sha, "how": how})
        # The head's own ci-red comment (if any) is edited to green by the
        # owed table independently (`_ci_owed_detect_green`, driven off
        # `heads`/`head_legs` — not off this review item), so nothing more
        # is done here for it.

    # Tell the seat that opened the PR (sealed pair 11ccb0f7, part 1). The
    # steward's outbound half — file, label, human_required — reached
    # nobody live: ratatosk #48 went red two minutes after the desk opened
    # it (2026-09-21) and the desk heard from the operator. For every item
    # on a WATCHED PR (a row in pr_watch.json, written by pr_open_execute)
    # post one Grove message to the watcher's channel when the item is
    # filed and one when it resolves, and keep the bot's per-head PR
    # comment saying the same. Markers in `ci_notified` are written only
    # on `sent`, so a refusal or a budget stop is retried next tick and a
    # re-tick never repeats a delivered line. Unwatched PRs: nothing.
    notified_now = _notify_watchers(
        items, state, receipt, pace=pace, app=app, call=call,
        just_filed={f["id"] for f in filed}, just_resolved={r["item_id"] for r in resolved},
        state_view={**state, "ci_items": items, "ci_filed": filed_now,
                    "ci_filed_cancelled": filed_cancelled_now},
    )
    state["ci_notified"] = notified_now

    # On a refusal the offset stays where a retry can find the row; what was
    # filed before it is remembered in ci_filed so the retry skips it. (The
    # enqueue-then-persist window — a crash between the two double-files
    # one head — is the same window the leg-per-item build had; not widened.)
    _persist()
    held = bool(refused) or pace.budget_spent
    if not held:
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
    if resolve_refused:
        receipt["resolve_refused"] = resolve_refused
    status = "could-not-run" if refused else ("paced" if pace.budget_spent else "ok")
    receipt.update(status=status, filed=filed, appended=appended,
                   resolved=resolved, refused=refused, skipped=skipped,
                   commented=commented, spoke=spoke,
                   new_offset=receipt["offset"] if held else offset,
                   remaining=to_file - len(filed), **pace.receipt())
    return _emit(receipt)


# ── the legacy-clear step: items filed before the collapse build ─────────────

# Items the leg-per-item run_ci filed carry this title shape; the collapse
# build's titles carry " leg(s): " instead. Neither shape is produced by
# anything but run_ci.
_CI_LEGACY_TITLE_RE = re.compile(r"^CI red: \S+ — (?!\d+ leg\(s\): ).+ (failure|timed_out|cancelled|startup_failure)$")
_CI_LEGACY_LIST_LIMIT = 500


def _is_legacy_ci_item(item: dict, *, own_ids: set[str]) -> bool:
    """A review item run_ci filed BEFORE the collapse build: the old title
    shape, not one of this build's own item ids, and a GitHub job URL as
    its source_ref (what run_ci always passed). Anything else — a real
    review ask, a new-shape item — is not this step's to touch."""
    if not isinstance(item, dict) or item.get("kind", "review") != "review":
        return False
    if str(item.get("id", "")) in own_ids:
        return False
    title = str(item.get("title", ""))
    if not _CI_LEGACY_TITLE_RE.match(title):
        return False
    ref = str(item.get("source_ref", ""))
    return ref == "" or ref.startswith("https://github.com/")


def _own_sha() -> str:
    """The bot's own checkout HEAD (editable install), for the clear note;
    empty when the tree is not a checkout or git is unavailable."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def run_ci_legacy_clear(*, enable_mcp: bool | None = None, build_sha: str | None = None,
                        force: bool = False) -> dict:
    """One-time, receipted, idempotent: resolve every open review item the
    leg-per-item ``run_ci`` filed before the collapse build. Those items
    have no head history in state, so resolve-on-green can never reach
    them (operator, 2026-09-20: "lets tie that in for hanuman").

    A tick step rather than a subcommand — the point of the bot is that
    the operator does not type — that runs once and records
    ``ci_legacy_cleared`` in the state file; ``force`` (the subcommand)
    re-runs it. Per item three-state: ``resolved`` / ``refused`` (the
    verb answered with an error) / and the whole step ``unreachable`` when
    the queue cannot be listed. Never touches an item it did not file:
    the old title shape, not one of this build's own ids, a GitHub
    source_ref. Honest absence when MCP is off.
    """
    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    if build_sha is None:
        build_sha = _own_sha()
    receipt: dict = {"event": "steward_ci_legacy_clear",
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = state_path()
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}
    done = state.get("ci_legacy_cleared")
    backfilled = state.get("ci_stuck_backfilled")
    if done and backfilled and not force:
        receipt.update(status="ok", ran=False, detail=f"already cleared at {done.get('at')}", cleared_at=done.get("at"),
                       backfill={"ran": False, "detail": f"already backfilled at {backfilled.get('at')}"})
        return _emit(receipt)
    if not enable_mcp:
        receipt.update(status="absent", ran=False, detail="WILLOW_BOT_MCP not enabled — no list, no resolve")
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = _resolve_app_id()
    if done and not force:
        # The legacy pass is on record; only the stuck backfill is owed.
        receipt.update(status="ok", ran=False, detail=f"already cleared at {done.get('at')}", cleared_at=done.get("at"))
        receipt["backfill"] = _backfill_stuck_items(state, path, app, mcp_client.call, at=receipt["at"],
                                                    build_sha=build_sha, force=force)
        return _emit(receipt)
    try:
        listing = mcp_client.call("human_required_list", {
            "app_id": app, "kind": "review", "status": "open", "limit": _CI_LEGACY_LIST_LIMIT,
        })
    except Exception as exc:  # noqa: BLE001 — cannot see the queue: say so, touch nothing
        receipt.update(status="unreachable", ran=False, detail=str(exc)[:300])
        return _emit(receipt)
    err = _tool_error(listing)
    if err:
        receipt.update(status="unreachable", ran=False, detail=err)
        return _emit(receipt)
    rows = listing.get("items") if isinstance(listing, dict) else None
    if rows is None and isinstance(listing, list):
        rows = listing
    if not isinstance(rows, list):
        receipt.update(status="unreachable", ran=False, detail=f"unrecognised listing shape: {str(listing)[:120]}")
        return _emit(receipt)

    # This build's own items are the ones in `ci_items` — every item it files
    # lands there. NOT `ci_filed`: the leg-per-item build wrote its item ids
    # into `ci_filed` too, so on the live box (~169 of them) that union read
    # every legacy item as "own" and the pass cleared nothing, then recorded
    # itself done (Loki 18CE5C43). The title shape is the real discriminator;
    # the id check only guards a same-shaped title this build produced.
    own_ids = {str(v.get("id")) for v in (state.get("ci_items") or {}).values() if isinstance(v, dict)}
    legacy = [r for r in rows if _is_legacy_ci_item(r, own_ids=own_ids)]
    # A real red (failure / timed_out / startup_failure) on a PR the bot
    # still sees open is exactly what the noise was drowning — the operator
    # asked for the noise cleared, not for a live failure to be called
    # superseded. Cancelled legacy items are the noise; reds on PRs no
    # longer open are moot — MERGED (in `merged_synced`) or CLOSED without
    # merge — and each gets the words that are true of it.
    #
    # `open` is trusted only through the scan record run_once writes: an
    # UNFILTERED scan, this build's shape. Before the argv fix the unit's
    # scan ran under the filter `['loop']` and wrote `open = []` every tick
    # (gap 1045a4056d11); an empty list from that path is not "no PRs are
    # open", it is "the scan saw nothing", and every real red is kept.
    open_known, open_set, scan_errors = _scan_open_set(state)
    merged = set(state.get("merged_synced") or [])
    kept: list[dict] = []
    to_clear: list[tuple[dict, str]] = []
    for item in legacy:
        title = str(item.get("title", ""))
        m = _CI_LEGACY_TITLE_RE.match(title)
        conclusion = m.group(1) if m else ""
        where = title[len("CI red: "):].split(" — ", 1)[0]
        if conclusion == _CI_CANCELLED:
            to_clear.append((item, "cancelled run"))
            continue
        if not open_known:
            kept.append({"item_id": str(item.get("id")), "title": title,
                         "reason": "open set unknown (no unfiltered scan on record)"})
            continue
        if where in open_set:
            kept.append({"item_id": str(item.get("id")), "title": title, "reason": "real red on an open PR"})
            continue
        incomplete = _scan_incomplete_for(where, scan_errors)
        if incomplete:
            # The scan could not list this repo, so "not in the open set" is
            # not "closed" (Loki 25CBCB13). Kept; re-examined next tick.
            kept.append({"item_id": str(item.get("id")), "title": title, "reason": f"scan incomplete for {incomplete}"})
            continue
        to_clear.append((item, "PR merged" if where in merged else "PR closed without merge"))
    receipt.update(listed=len(rows), legacy=len(legacy), kept=kept, open_known=open_known,
                   scan_errors=scan_errors)
    build = build_sha or "unknown sha"
    notes = {
        "cancelled run": f"superseded by the run_ci collapse build ({build}): cancelled run, not a failure",
        "PR merged": f"moot: PR merged; cleared by the run_ci collapse build ({build})",
        "PR closed without merge": f"moot: PR closed without merge; cleared by the run_ci collapse build ({build})",
    }
    # Paced like the mirror step: the store meters 60/min with a burst of 10
    # and answers the 11th call rate_limited. Wait what the limiter asks,
    # retry the same item, inside a time budget; the remainder is next tick's.
    resolved, refused, remaining, paced = [], [], 0, 0
    deadline = _clock() + _MIRROR_TIME_BUDGET_S
    budget_spent = False
    for index, (item, why) in enumerate(to_clear):
        item_id = str(item.get("id"))
        note = notes[why]
        while True:
            try:
                result = mcp_client.call("human_required_resolve", {
                    "app_id": app, "item_id": item_id, "status": "resolved", "note": note,
                })
            except Exception as exc:  # noqa: BLE001 — one refused item is a line, not a dead step
                refused.append({"item_id": item_id, "title": item.get("title", ""), "error": str(exc)[:300]})
                break
            err = _tool_error(result)
            if err is None:
                resolved.append({"item_id": item_id, "title": item.get("title", ""), "why": why})
                break
            if err != "rate_limited":
                refused.append({"item_id": item_id, "title": item.get("title", ""), "error": err})
                break
            wait = min(max(int((result.get("retry_after") if isinstance(result, dict) else 1) or 1), 1),
                       _MIRROR_MAX_WAIT_S)
            if _clock() + wait > deadline:
                budget_spent = True
                break
            paced += 1
            _sleep(wait)
        if budget_spent:
            remaining = len(to_clear) - index
            break
    # Recorded as cleared only when the pass was clean AND complete, so a
    # partial pass runs again next tick for the remainder — never a hand pass.
    # An item kept because the scan could not list its repo is not decided
    # either way; the pass re-examines it next tick, so it is not recorded.
    scan_held = any(k["reason"].startswith("scan incomplete for ") for k in kept)
    complete = not refused and not remaining and not scan_held
    if complete:
        state["ci_legacy_cleared"] = {"at": receipt["at"], "resolved": len(resolved), "kept": len(kept),
                                      "build_sha": build_sha}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")
    status = "ok" if complete else ("paced" if remaining and not refused else "partial")
    receipt.update(status=status, ran=True, resolved=resolved, refused=refused, remaining=remaining,
                   paced=paced, budget_spent=budget_spent, recorded=complete)
    # State was re-read by the legacy pass above only to write its marker;
    # the backfill reads and writes its own keys through the same file.
    state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else state
    receipt["backfill"] = _backfill_stuck_items(state, path, app, mcp_client.call, at=receipt["at"],
                                                build_sha=build_sha, force=force)
    return _emit(receipt)


def _backfill_stuck_items(state: dict, path: Path, app: str, call, *, at: str, build_sha: str | None,
                          force: bool) -> dict:
    """One-time, receipted, idempotent: resolve stuck-only items filed by
    the build BEFORE this one whose PR has since closed (Loki FE91FF0E).

    The tick's own route (``run_ci`` resolve-on-close) reads ``pr_closed``,
    which the previous build never wrote — so #583's close webhook has
    already passed and no future event will populate it, and
    ``merged_synced`` holds none of the four stuck-only items open on the
    live box. This pass reads what the box DOES know: ``pr_closed`` and
    ``merged_synced`` (the deposit stream), then the bot's own unfiltered
    scan (``state['open']``, trusted only through the scan record — the
    same rule the legacy pass uses): a PR the scan does not list is closed
    on GitHub. A PR-less item is not this pass's to judge — with a branch
    on record the tick's successor route retires it; without one nothing
    can, and the receipt says so (``kept: no branch on record``) rather
    than leaving it silent. Recorded as ``ci_stuck_backfilled`` only when
    the pass was clean and complete; ``force`` re-runs it.
    """
    out: dict = {"ran": True}
    already = state.get("ci_stuck_backfilled")
    if already and not force:
        return {"ran": False, "detail": f"already backfilled at {already.get('at')}"}
    items = {k: dict(v) for k, v in (state.get("ci_items") or {}).items() if isinstance(v, dict)}
    filed = state.get("ci_filed") or {}
    filed_cancelled = state.get("ci_filed_cancelled") or {}
    closed = {k: v for k, v in (state.get("pr_closed") or {}).items() if isinstance(v, dict)}
    merged_synced = set(state.get("merged_synced") or [])
    open_known, open_set, scan_errors = _scan_open_set(state)

    candidates = [(k, it) for k, it in items.items()
                  if not it.get("resolved") and it.get("id") and _item_is_stuck_only(it, filed, filed_cancelled)]
    out["candidates"] = len(candidates)
    out["open_known"] = open_known
    out["scan_errors"] = scan_errors
    build = build_sha or "unknown sha"
    resolved, kept, refused = [], [], []
    to_resolve: list[tuple[str, dict, str, str]] = []
    for key, item in candidates:
        where = _ci_pr_key(item["repo"], item["pr"], item["head_sha"])
        if not item["pr"]:
            reason = (f"awaiting a successor on {item['branch']}" if item.get("branch")
                      else "no branch on record")
            kept.append({"item_id": item["id"], "where": where, "reason": reason})
            continue
        if where in closed:
            why = "PR merged per deposit stream" if closed[where].get("merged") else "PR closed per deposit stream"
        elif where in merged_synced:
            why = "PR closed per host sync"
        elif open_known and where in open_set:
            kept.append({"item_id": item["id"], "where": where, "reason": "PR open"})
            continue
        elif open_known and _scan_incomplete_for(where, scan_errors):
            # Absent from a scan that could not list this repo is not
            # "closed" (Loki 25CBCB13). Kept, and the pass is not recorded
            # while any item is held this way, so it re-examines next tick.
            kept.append({"item_id": item["id"], "where": where,
                         "reason": f"scan incomplete for {_scan_incomplete_for(where, scan_errors)}"})
            continue
        elif open_known:
            why = "PR not open per the bot's scan"
        else:
            kept.append({"item_id": item["id"], "where": where,
                         "reason": "open set unknown (no unfiltered scan on record)"})
            continue
        to_resolve.append((key, item, where, why))

    pace = _Pacer(_MIRROR_TIME_BUDGET_S)
    remaining = 0
    for index, (key, item, where, why) in enumerate(to_resolve):
        note = f"moot: {why}; stuck cancelled run, backfilled by the run_ci residue build ({build})"
        result, err = pace.call(call, "human_required_resolve", {
            "app_id": app, "item_id": item["id"], "status": "resolved", "note": note,
        })
        if err and pace.budget_spent:
            remaining = len(to_resolve) - index
            out["stopped"] = {"at": where, "reason": err}
            break
        if err:
            refused.append({"item_id": item["id"], "where": where, "error": err})
            continue
        items[key]["resolved"] = {"by": item["head_sha"], "how": "backfill", "why": why, "at": at}
        resolved.append({"item_id": item["id"], "where": where, "why": why})

    scan_held = any(k["reason"].startswith("scan incomplete for ") for k in kept)
    complete = not refused and not remaining and not scan_held
    state["ci_items"] = items
    if complete:
        state["ci_stuck_backfilled"] = {"at": at, "resolved": len(resolved), "kept": len(kept), "build_sha": build_sha}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    out.update(resolved=resolved, kept=kept, refused=refused, remaining=remaining, recorded=complete,
               **pace.receipt())
    return out


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

        app = _resolve_app_id()
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

    app = _resolve_app_id()
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
            ("ci-legacy-clear", run_ci_legacy_clear),
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
    if args[0] == "ci-legacy-clear":
        # The subcommand re-runs the one-time clear (force); the loop runs it once.
        run_ci_legacy_clear(force=True)
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
    if args[0] == "annul":
        return run_annul(args[1:])
    print(
        "usage: willow-bot-steward [tick|loop|heartbeat|sweep|resolve|install-receipts|mirror|ci|ci-legacy-clear|catchup|audit|voice|status|inbox <state>|scan|annul --match … --reason … --authorization … [--apply]]",
        file=sys.stderr,
    )
    return 2


def run_annul(argv: list[str]) -> int:
    """``willow-bot-steward annul [--url S] [--sha PREFIX] [--repo org/name] [--match S]
    --reason … --authorization <frank id> [--apply] [--allow-multi] [--file F]``

    The honest correction of the chained deposits file (gap 9): rows are
    never rewritten; ONE ``annul`` row is appended naming what it voids.
    Matchers are scoped and AND-ed (``--url``/``--match`` on ``html_url``
    only, ``--sha`` a head prefix, ``--repo`` exact); at least one is
    required. Dry-run by default — prints the rows it would void (by hash,
    and by record_id for legacy rows), every distinct (repo, head) among
    them, and what is already voided; writes nothing. ``--apply`` appends
    the row and refuses when the plan spans more than one repo or head
    unless ``--allow-multi`` says the operator read the list (Loki
    717E236C: ``runs/1`` reached 395 real rows across eight repos). Also
    refuses with nothing to void, without a reason, or without an
    authorization id.
    """
    import argparse

    from willow_bot import deposits as dep

    parser = argparse.ArgumentParser(prog="willow-bot-steward annul", add_help=True)
    parser.add_argument("--url", default="", help="substring of html_url")
    parser.add_argument("--match", default="", help="substring of html_url (alias of --url)")
    parser.add_argument("--sha", default="", help="head_sha prefix")
    parser.add_argument("--repo", default="", help="exact org/name")
    parser.add_argument("--reason", default="")
    parser.add_argument("--authorization", default="", help="the FRANK id that authorizes the correction")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-multi", action="store_true",
                        help="apply even when the plan spans more than one (repo, head)")
    parser.add_argument("--file", default="", help="deposits file (default: the steward's own)")
    ns = parser.parse_args(argv)

    path = Path(ns.file) if ns.file else dep.deposits_jsonl()
    plan = dep.annul_matches(path, match=ns.match, url=ns.url, sha=ns.sha, repo=ns.repo)
    out = {"event": "steward_annul", "file": str(path), "apply": bool(ns.apply), **plan}
    if plan.get("error"):
        out.update(status="refused", detail="give at least one of --url/--match, --sha, --repo")
        print(json.dumps(out, indent=2))
        return 2
    if not ns.apply:
        out["status"] = "dry-run"
        print(json.dumps(out, indent=2))
        return 0
    if not plan["count"]:
        out.update(status="refused", detail="nothing to void (already voided or no match)")
        print(json.dumps(out, indent=2))
        return 1
    if not ns.reason.strip() or not ns.authorization.strip():
        out.update(status="refused", detail="--reason and --authorization are required to apply")
        print(json.dumps(out, indent=2))
        return 2
    if len(plan["heads"]) > 1 and not ns.allow_multi:
        out.update(status="refused",
                   detail=f"plan spans {len(plan['heads'])} distinct (repo, head) pairs — read `heads` and "
                          "pass --allow-multi to void them all, or narrow with --repo/--sha")
        print(json.dumps(out, indent=2))
        return 3
    rec = dep.annul_record(voids=plan["voids"], voids_legacy=plan["voids_legacy"],
                           reason=ns.reason.strip(), authorization=ns.authorization.strip())
    written = dep.append_local(rec) if not ns.file else _append_to(path, rec)
    out.update(status="applied", row_hash=rec["row_hash"], written=str(written))
    print(json.dumps(out, indent=2))
    return 0


def _append_to(path: Path, rec: dict) -> Path:
    """append_local against an explicit file (tests / an operator copy):
    same chaining, computed from that file's own tail."""
    from willow_bot import deposits as dep

    prev = dep._last_chained_hash_from_tail(path) or dep.GENESIS_HASH
    rec["prev_hash"] = prev
    rec["row_hash"] = dep.compute_row_hash(rec, prev)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
