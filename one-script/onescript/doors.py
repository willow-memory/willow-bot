"""doors — the seat doesn't start, and check-in doesn't open, without its doors.

The model's seat (seat/, a container) has two doors, and each covers the
other's gap: the hooks (hook.py denies every tool but Write, prompt.py puts the
served tables in the prompt) and the managed settings' deny list, which holds
when a hook can't start (Claude Code hooks fail open on anything but exit 2).

    pins     seat/doors.json: the sha256 of each door, as committed
    seat     inside the container, before Claude Code starts: every installed
             door matches its pin, is root-owned, and isn't writable by the
             seat's user; the settings still say what the seat needs. Any
             failure and the seat refuses to start (exit 2).
    repo     at check-in: every door in the repo still matches its pin, so a
             hook changed without a new pin is a hard close, not a quiet drift.

Stdlib only, Python 3.9+: the container copies this one file and runs it.

    python3 doors.py pin    --repo ONE_SCRIPT_DIR          # rewrite seat/doors.json
    python3 doors.py check  --pins doors.json [--root /]   # the installed doors
    python3 doors.py exec   --pins doors.json -- claude …  # check, then exec
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

# Each door: its source in the repo (relative to one-script/) and where the
# seat installs it.
DOORS = {
    "hook.py": "/opt/onescript/hook.py",
    "prompt.py": "/opt/onescript/prompt.py",
    "seat/managed-settings.json": "/etc/claude-code/managed-settings.json",
}
SETTINGS = "seat/managed-settings.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin(repo: Path) -> dict:
    return {"doors": {src: sha256(repo / src) for src in sorted(DOORS)}}


def _row(where: str, verdict: str, why: str = "") -> dict:
    return {"gate": "doors", "where": where, "verdict": verdict, "why": why}


def settings_rows(settings: dict, where: str) -> list[dict]:
    """The settings say what the seat needs, or the seat doesn't start."""
    out = []
    hooks = settings.get("hooks", {})

    def commands(event: str) -> list[str]:
        return [
            h.get("command", "")
            for m in hooks.get(event, [])
            for h in m.get("hooks", [])
            if event != "PreToolUse" or m.get("matcher") == "*"
        ]

    needs = [
        (
            any(DOORS["hook.py"] in c for c in commands("PreToolUse")),
            "no PreToolUse hook on every tool",
        ),
        (
            any(DOORS["prompt.py"] in c for c in commands("UserPromptSubmit")),
            "no UserPromptSubmit hook serving the prompt",
        ),
        (settings.get("allowManagedHooksOnly") is True, "other hooks could run"),
        (
            settings.get("allowManagedPermissionRulesOnly") is True,
            "other permission rules could apply",
        ),
    ]
    deny = settings.get("permissions", {}).get("deny", [])
    needs += [
        (
            "mcp__*" in deny and "Read" in deny and "Bash" in deny,
            "the deny list is short",
        ),
        ("Write" not in deny, "Write is denied, so the human is never asked"),
        (
            bool(settings.get("env", {}).get("ONESCRIPT_SERVED")),
            "prompt.py isn't told where serve writes",
        ),
    ]
    for ok, why in needs:
        if not ok:
            out.append(_row(where, "failing", why))
    return out or [_row(where, "satisfied")]


def check_repo(repo: Path, pins: dict) -> list[dict]:
    """At check-in: the doors in the repo still match their pins."""
    out = []
    for src in sorted(DOORS):
        want = pins.get("doors", {}).get(src)
        path = repo / src
        if want is None:
            out.append(_row(src, "failing", "no pin"))
        elif not path.exists():
            out.append(_row(src, "failing", "missing"))
        elif sha256(path) != want:
            out.append(_row(src, "failing", "changed since it was pinned; re-pin"))
        else:
            out.append(_row(src, "satisfied"))
    try:
        out += settings_rows(
            json.loads((repo / SETTINGS).read_text()), SETTINGS + " (says)"
        )
    except (OSError, ValueError) as e:
        out.append(_row(SETTINGS, "failing", f"unreadable: {type(e).__name__}"))
    return out


def check_seat(pins: dict, root: Path = Path("/")) -> list[dict]:
    """Inside the seat: each installed door matches, is root's, and is shut."""
    out = []
    for src, installed in sorted(DOORS.items()):
        path = root / installed.lstrip("/")
        want = pins.get("doors", {}).get(src)
        try:
            st = path.stat()
            got = sha256(path)
        except OSError:
            out.append(_row(installed, "failing", "missing"))
            continue
        if got != want:
            out.append(_row(installed, "failing", "doesn't match its pin"))
        elif st.st_uid != 0:
            out.append(_row(installed, "failing", "not root's"))
        elif st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            out.append(_row(installed, "failing", "writable by others"))
        elif os.geteuid() != 0 and os.access(path, os.W_OK):
            out.append(_row(installed, "failing", "the seat's user can write it"))
        else:
            out.append(_row(installed, "satisfied"))
    settings = root / DOORS[SETTINGS].lstrip("/")
    try:
        out += settings_rows(
            json.loads(settings.read_text()), DOORS[SETTINGS] + " (says)"
        )
    except (OSError, ValueError) as e:
        out.append(_row(DOORS[SETTINGS], "failing", f"unreadable: {type(e).__name__}"))
    return out


def failing(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["verdict"] != "satisfied"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="doors")
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("pin")
    pp.add_argument("--repo", type=Path, required=True)
    for name in ("check", "exec"):
        c = sub.add_parser(name)
        c.add_argument("--pins", type=Path, required=True)
        c.add_argument("--root", type=Path, default=Path("/"))
        if name == "exec":
            c.add_argument("argv", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)

    if a.cmd == "pin":
        out = a.repo / "seat" / "doors.json"
        out.write_text(json.dumps(pin(a.repo), indent=1, sort_keys=True) + "\n")
        print(f"pinned {len(DOORS)} doors")
        return 0

    try:
        rows = check_seat(json.loads(a.pins.read_text()), a.root)
    except (OSError, ValueError) as e:
        rows = [_row(str(a.pins), "failing", f"pins unreadable: {type(e).__name__}")]
    bad = failing(rows)
    for r in rows:
        print(f"{r['verdict']:<10} {r['where']}  {r['why']}".rstrip())
    if bad:
        print(f"refused: {len(bad)} door(s) not shut; the seat does not start")
        return 2
    if a.cmd == "exec":
        argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
        if not argv:
            print("refused: nothing to run")
            return 2
        sys.stdout.flush()  # exec replaces the process; unflushed rows are lost
        os.execvp(argv[0], argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
