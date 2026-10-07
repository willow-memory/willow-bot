"""md_split: in an agent transcript, does markdown mark who wrote what?

    python3 md_split.py transcript.jsonl

Read only, stdlib only. Each human-side and model-side text is classed by
role (from the transcript) and by markdown or not (from the text itself).
"""

import json
import re
import sys
from collections import Counter

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
HARNESS = (
    "Stop hook feedback",
    "Tool loaded.",
    "[Request interrupted",
    "<task-notification>",  # a background task finishing, not the human
)
MD_ANY = [
    re.compile(r"^#{1,6}\s", re.M),  # heading
    re.compile(r"^\s*\|.+\|\s*$", re.M),  # table row
    re.compile(r"^```", re.M),  # code fence
    re.compile(r"\*\*[^*\n]+\*\*"),  # bold
    re.compile(r"`[^`\n]+`"),  # inline code
    re.compile(r"\[[^\]\n]+\]\([^)\s]+\)"),  # link
    re.compile(r"^>\s", re.M),  # blockquote
]
LIST_LINE = re.compile(r"^\s*(?:[-*]|\d+\.)\s", re.M)


def is_md(text: str) -> bool:
    return any(rx.search(text) for rx in MD_ANY) or len(LIST_LINE.findall(text)) >= 2


def texts(path: str):
    """(role, source, text) for every human-side and model-side text."""
    for raw in open(path, encoding="utf-8"):
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        kind, msg = o.get("type"), o.get("message") or {}
        if kind == "user" and not o.get("isMeta"):
            c = msg.get("content")
            parts = (
                [c]
                if isinstance(c, str)
                else [
                    b.get("text", "")
                    for b in c or []
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
            )
            for t in parts:
                t = REMINDER.sub("", t).strip()
                if t:
                    src = "harness" if t.startswith(HARNESS) else "typed"
                    yield "user", src, t
        elif kind == "assistant":
            for b in msg.get("content") or []:
                if (
                    isinstance(b, dict)
                    and b.get("type") == "text"
                    and b["text"].strip()
                ):
                    yield "assistant", "model", b["text"].strip()
        elif kind == "attachment":
            a = o.get("attachment") or {}
            if a.get("type") == "file":
                c = a.get("content")
                if isinstance(c, dict):  # {"type": "text", "file": {"content": ...}}
                    c = (c.get("file") or {}).get("content", "")
                yield "user", "attached", str(c or "")
            elif a.get("type") == "queued_command" and a.get("humanTurn"):
                yield "user", "typed", str(a.get("prompt", "")).strip()


def main(path: str) -> int:
    rows = list(texts(path))
    grid = Counter((r, s, is_md(t)) for r, s, t in rows)
    print(f"{len(rows)} texts")
    print(f"  {'role':9} {'source':9} {'markdown':>9} {'plain':>7}")
    for r, s in [
        ("user", "typed"),
        ("user", "attached"),
        ("user", "harness"),
        ("assistant", "model"),
    ]:
        print(f"  {r:9} {s:9} {grid[(r, s, True)]:9} {grid[(r, s, False)]:7}")
    print("\nyour side, but markdown:")
    for r, s, t in rows:
        if r == "user" and s != "harness" and is_md(t):
            print(f"  [{s}] {t[:90].replace(chr(10), ' ')}")
    print("\nmodel side, but plain:")
    for r, s, t in rows:
        if r == "assistant" and not is_md(t):
            print(f"  {t[:90].replace(chr(10), ' ')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
