"""One steward tick: inbox → scan open PRs → optional host merge sync → JSON events.

Emits AGENT_LOOP_TICK_PR_AUDIT when run with --loop (desk wake). Never calls Grove 3B.
"""
from __future__ import annotations

import json
import os
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
    "(steward_audit line); steward_sweep says what came home; steward_mirror what "
    "reached the store. Do not post to Grove; Grove Loki is separate. Brief tick summary."
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
    )
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
        for name, step in (("sweep", run_sweep), ("mirror", run_mirror), ("audit", run_audit)):
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
    if args[0] == "mirror":
        run_mirror()
        return 0
    if args[0] == "audit":
        run_audit()
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
        "usage: willow-bot-steward [tick|loop|heartbeat|sweep|mirror|audit|inbox <state>|scan]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
