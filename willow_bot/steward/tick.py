"""One steward tick: inbox → scan open PRs → optional host merge sync → JSON events.

Emits AGENT_LOOP_TICK_PR_AUDIT when run with --loop (desk wake). Never calls Grove 3B.
"""
from __future__ import annotations

import json
import os
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
    "fleet_bridge hints. For each new_pr: audit; for merged_synced: confirm local "
    "pull. Do not post to Grove; Grove Loki is separate. Brief tick summary."
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
        "usage: willow-bot-steward [tick|loop|heartbeat|inbox <state>|scan]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
