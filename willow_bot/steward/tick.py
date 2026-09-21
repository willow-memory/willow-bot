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
    # at what time. An unfiltered scan that finds nothing says so rather
    # than leaving an empty list to be mistaken for "no PRs are open" —
    # and the legacy clear trusts `open` only through this record.
    state["scan"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "open": len(keys), "filters": filters}
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

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
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
    from willow_bot.deposits import COLLECTION, deposits_jsonl, record_id_for

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

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    mirrored, failed = 0, None
    pace = _Pacer(_MIRROR_TIME_BUDGET_S, calls=_MIRROR_CALLS_PER_TICK)
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
    receipt.update(status=status, mirrored=mirrored, new_offset=offset, behind=behind, **pace.receipt())
    if failed is not None:
        receipt["detail"] = failed
    return _emit(receipt)


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


def _prune_ci_state(items: dict, heads: dict, head_legs: dict, cancelled_pending: dict) -> dict:
    """Keep the four maps bounded (Loki, dispatch 82A7DB13, finding 4).

    A head is kept when it is the newest the bot has seen for its PR, the
    head of an unresolved item, or the head of a pending cancelled leg;
    every other head — and its ``head_legs`` — is dropped. Resolved items
    are dropped (``ci_filed`` still remembers their legs, so a
    re-delivered completion never re-files). The resolve loop is then
    unresolved-items × kept-heads, a handful per PR. Returns counts.
    """
    keep: dict[str, set[str]] = {}
    for item in items.values():
        if not item.get("resolved"):
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
        if items[item_key].get("resolved"):
            del items[item_key]
            dropped_items += 1
    return {"heads": dropped_heads, "items": dropped_items}


def _ci_head_key_from_pr(pr_key: str, head_sha: str) -> str:
    return f"{pr_key}@{head_sha}"


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
        pruned = _prune_ci_state(items, heads, head_legs, cancelled_pending)
        if pruned["heads"] or pruned["items"]:
            receipt["pruned"] = pruned
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
                       would_resolve=[{"where": k, "superseded_by": sha, "how": how}
                                      for k, _, sha, how in _resolve_candidates(filed_before, filed_cancelled_before)],
                       remaining=len(groups), **_Pacer.idle())
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    filed, appended, refused = [], [], []
    pace = _Pacer(_CI_TIME_BUDGET_S)
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
        result, err = pace.call(mcp_client.call, "human_required_enqueue", args)
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
        items[head_key] = {"id": item_id, "repo": first["repo"], "pr": first["pr"],
                           "head_sha": first["head_sha"], "legs": names, "filed_at": receipt["at"],
                           "stuck": all(lg["conclusion"] == _CI_CANCELLED for lg in legs),
                           "branch": first["head_branch"]}
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
        result, err = pace.call(mcp_client.call, "human_required_resolve", {
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

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
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
    scan = state.get("scan")
    open_prs = state.get("open")
    open_known = (isinstance(scan, dict) and scan.get("filters") == [] and isinstance(open_prs, list)
                  and (open_prs != [] or scan.get("open") == 0))
    open_set = set(open_prs or []) if open_known else set()
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
        to_clear.append((item, "PR merged" if where in merged else "PR closed without merge"))
    receipt.update(listed=len(rows), legacy=len(legacy), kept=kept, open_known=open_known)
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
    complete = not refused and not remaining
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
    scan = state.get("scan")
    open_prs = state.get("open")
    open_known = (isinstance(scan, dict) and scan.get("filters") == [] and isinstance(open_prs, list)
                  and (open_prs != [] or scan.get("open") == 0))
    open_set = set(open_prs or []) if open_known else set()

    candidates = [(k, it) for k, it in items.items()
                  if not it.get("resolved") and it.get("id") and _item_is_stuck_only(it, filed, filed_cancelled)]
    out["candidates"] = len(candidates)
    out["open_known"] = open_known
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
        elif open_known and where not in open_set:
            why = "PR not open per the bot's scan"
        elif open_known:
            kept.append({"item_id": item["id"], "where": where, "reason": "PR open"})
            continue
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

    complete = not refused and not remaining
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
    print(
        "usage: willow-bot-steward [tick|loop|heartbeat|sweep|resolve|install-receipts|mirror|ci|ci-legacy-clear|catchup|audit|voice|status|inbox <state>|scan]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
