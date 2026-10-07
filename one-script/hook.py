"""hook — the model has no tools; it writes only if the user allows.

Write asks the user. Every other tool is denied, Read included, and so are
tools that don't exist yet. The model doesn't read: what serve wrote reaches it
through prompt.py, in the prompt, so it never holds a path and never learns the
box is there (2026-10-07: "Sounds good" to code putting the served content in
the prompt). Anything the hook can't read is denied, and a crash exits 2,
which blocks: it never fails open.

The vault is not checked here: its wall is that the agent's user can't read it.

    python3 one-script/hook.py    # in willow-bot; Claude Code PreToolUse, matcher "*"
"""

import json
import sys

POLICY = {"Write": "ask"}


def decide(event):
    tool = event.get("tool_name") if isinstance(event, dict) else None
    return POLICY.get(tool, "deny")


if __name__ == "__main__":
    try:
        out = {
            "hookEventName": "PreToolUse",
            "permissionDecision": decide(json.load(sys.stdin)),
        }
        print(json.dumps({"hookSpecificOutput": out}))
    except Exception:
        sys.exit(2)  # exit 2 blocks the tool in Claude Code
