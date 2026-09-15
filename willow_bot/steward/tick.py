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

# Conclusions that mean "this leg did not pass". `cancelled` is in: a leg
# that never reached a verdict is not green, and the aggregate `test` gate
# in the fleet's tests.yml treats it as a failure too.
_CI_RED = frozenset({"failure", "timed_out", "cancelled", "startup_failure"})
_CI_PER_TICK = 50


def _ci_offset_path() -> Path:
    from willow_bot.deposits import deposits_dir

    return deposits_dir() / "ci.offset"


def _ci_key(rec: dict) -> str:
    return f"{rec.get('head_sha', '')}:{rec.get('check_run_id', '')}"


def run_ci(*, enable_mcp: bool | None = None) -> dict:
    """File every red check the bot deposited since the last tick.

    Gap 8d1bcb2b7c02: the bot records a red check faithfully and reports
    it to nobody — a `ci_fail` quip in the webhook log, a `check_run` item
    the inbox step skips, a deposit row nothing reads back. On 2026-09-14
    the operator told the seat a PR was red, twice. This step reads
    ``deposits/ci_outcomes.jsonl`` from its own byte offset (the mirror's
    shape, its own offset file so the two never race), and for each new
    row whose conclusion is in ``_CI_RED`` files one ``human_required``
    item of kind ``review`` naming the PR, the leg and the job URL — the
    bot's own deposits as the only source, no lease, no ``gh``.

    Idempotent per (head_sha, check_run_id) through ``ci_filed`` in the
    state file: a re-delivered completion or a rotated deposits file does
    not file twice. A refused enqueue leaves the offset before that row
    and reports the reason; the next tick retries the same row. Reds are
    reported in the receipt even when MCP is off — the seat reading the
    receipt file still learns what went red, it just is not filed.
    """
    from willow_bot.deposits import deposits_jsonl

    if enable_mcp is None:
        enable_mcp = mcp_enabled()
    receipt: dict = {"event": "steward_ci", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    src = deposits_jsonl()
    if not src.is_file():
        receipt.update(status="ok", present=False, red=[], filed=[], detail=f"no deposits file at {src}")
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

    # Read first: every red row is in the receipt whether or not it can be filed.
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
                if isinstance(rec, dict) and rec.get("conclusion") in _CI_RED:
                    red.append({
                        "repo": rec.get("repo", ""), "pr": rec.get("pr_number"),
                        "head_sha": rec.get("head_sha", ""), "check": rec.get("check_name", ""),
                        "conclusion": rec.get("conclusion"), "url": rec.get("html_url", ""),
                        "key": _ci_key(rec),
                    })
                scanned += 1
            offset = fh.tell()
    receipt["red"] = [{k: v for k, v in r.items() if k != "key"} for r in red]

    to_file = [r for r in red if r["key"] not in filed_before]
    if not enable_mcp:
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
        receipt.update(status="absent", detail="WILLOW_BOT_MCP not enabled — reds reported, not filed",
                       filed=[], refused=[], new_offset=offset)
        return _emit(receipt)

    from willow_bot.steward import mcp_client

    app = os.environ.get("WILLOW_BOT_MCP_APP_ID", "willow").strip() or "willow"
    filed, refused = [], []
    filed_now = dict(filed_before)
    for r in to_file:
        where = f"{r['repo']}#{r['pr']}" if r["pr"] else f"{r['repo']}@{r['head_sha'][:12]}"
        args = {
            "app_id": app, "kind": "review", "priority": "normal",
            "title": f"CI red: {where} — {r['check']} {r['conclusion']}",
            "summary": f"{r['check']} concluded {r['conclusion']} on {r['head_sha']}. {r['url']}",
            "source_ref": r["url"],
        }
        try:
            result = mcp_client.call("human_required_enqueue", args)
        except Exception as exc:  # noqa: BLE001 — a refused filing is a line with its reason
            refused.append({"where": where, "check": r["check"], "error": str(exc)[:300]})
            break
        err = _tool_error(result)
        if err:
            refused.append({"where": where, "check": r["check"], "error": err})
            break
        item_id = result.get("id") if isinstance(result, dict) else None
        filed_now[r["key"]] = item_id or "filed"
        filed.append({"where": where, "check": r["check"], "conclusion": r["conclusion"], "id": item_id})
    # On a refusal the offset stays where a retry can find the row; what was
    # filed before it is remembered in ci_filed so the retry skips it.
    state["ci_filed"] = filed_now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    if not refused:
        off_path.parent.mkdir(parents=True, exist_ok=True)
        off_path.write_text(f"{offset}\n", encoding="utf-8")
    receipt.update(status="ok" if not refused else "could-not-run", filed=filed, refused=refused,
                   skipped=len(red) - len(to_file), new_offset=offset if not refused else receipt["offset"])
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
        for name, step in (
            ("resolve", lambda: run_resolve(sweep)),
            ("mirror", run_mirror),
            ("ci", run_ci),
            ("audit", run_audit),
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
        "usage: willow-bot-steward [tick|loop|heartbeat|sweep|resolve|mirror|ci|audit|status|inbox <state>|scan]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
