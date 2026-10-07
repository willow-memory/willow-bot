"""hook — the model reads only what was served, and writes only if the user allows.

Read is allowed on one file: the served file, named by ONESCRIPT_SERVED. Any
other path is denied, and so is every Read when that variable is unset (Q19,
accepted 2026-10-07: reading is open for people; the model reads only what's
served). Write asks the user. Every other tool is denied, including ones that
don't exist yet. Anything the hook can't read is denied, and a crash exits 2,
which blocks: it never fails open.

The model's job is to read what the last script served; the scripts do the
rest. The vault is not checked here: its wall is that the agent's user can't
read it.

    ONESCRIPT_SERVED=/path/to/box/served.json \
        python3 docs/design/one-script/hook.py    # Claude Code PreToolUse, matcher "*"
"""

import json
import os
import sys

POLICY = {"Read": "allow", "Write": "ask"}


def decide(event):
    tool = event.get("tool_name") if isinstance(event, dict) else None
    decision = POLICY.get(tool, "deny")
    if tool == "Read":
        served = os.environ.get("ONESCRIPT_SERVED", "")
        args = event.get("tool_input")
        path = args.get("file_path") if isinstance(args, dict) else None
        if not served or not isinstance(path, str) or not path:
            return "deny"
        if os.path.realpath(path) != os.path.realpath(served):
            return "deny"
    return decision


if __name__ == "__main__":
    try:
        out = {
            "hookEventName": "PreToolUse",
            "permissionDecision": decide(json.load(sys.stdin)),
        }
        print(json.dumps({"hookSpecificOutput": out}))
    except Exception:
        sys.exit(2)  # exit 2 blocks the tool in Claude Code
