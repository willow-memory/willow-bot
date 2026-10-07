#!/usr/bin/env python3
"""flow: the session-map pieces, put together behind one command.

The pieces stay as they are; this file only joins them:

  make_flow.py   transcript -> session-flow.json/.md/.mmd      (6 view)
  run_flow.py    make_flow on this box, reading inside Kart     (adapter)
  side_runner.py one turn -> session JSON record + .drawio view (3 record, 6 view)
  batch_flow.py  every finished session -> maps + index         (7 reverse, input)
  merge_flows.py many maps -> what sticks out                   (7 reverse)

  flow.py map    TRANSCRIPT [SUBAGENT_DIR] OUT_DIR
  flow.py drawio SESSION_FLOW_JSON OUT_DIR        replay turns through side_runner
  flow.py batch  PROJECTS_DIR OUT_DIR [DEADLINE_S]
  flow.py merge  OUT_DIR LABEL=SESSION_FLOW_JSON ...
  flow.py all    TRANSCRIPT [SUBAGENT_DIR] OUT_DIR  map, then drawio

`drawio` is the join between the two halves. make_flow's events are flat; the
side runner takes one turn at a time. Each operator turn becomes one side
runner turn. Its events are grouped by who did what (actor, tool, verb) with
a count, so a turn that ran 700 builder calls draws as a handful of nodes,
not 700. The worst outcome in a group wins its colour. Events before the
first operator turn have no turn to hang on; they are counted and reported,
never silently dropped. Marks in the map are passed through; the runner only
draws them.
"""

from __future__ import annotations

import collections
import json
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _shape(e: dict) -> str:
    tool = e["tool"].split("__")[-1]
    detail = str(e.get("detail", "")).split(" · ")[0]
    if detail.startswith("kart:") or e["tool"] in ("Bash", "Read", "Write", "Edit"):
        return f"{tool} {detail}".strip()
    return tool


def _who(actor: str) -> str:
    if actor == "desk":
        return "desk"
    name = actor.removeprefix("agent:")
    return name.split()[0] if name else "agent"


def turns_from(flow: dict) -> tuple[list[dict], int]:
    """Group a session-flow.json into side-runner turns. Returns (turns, orphans)."""
    ev = flow["events"]
    turns = [e for e in ev if e["kind"] == "turn"]
    tools = [e for e in ev if e["kind"] == "tool"]
    by = collections.defaultdict(list)
    for e in tools:
        by[e["turn"]].append(e)
    orphans = len(by.pop(0, []))
    out = []
    for t in turns:
        groups: dict[tuple, dict] = {}
        for e in by.get(t["turn"], []):
            key = (_who(e["actor"]), _shape(e))
            g = groups.setdefault(
                key,
                {
                    "n": 0,
                    "paths": collections.Counter(),
                    "bad": 0,
                    "ts": e["ts"],
                    "ms": False,
                },
            )
            g["n"] += 1
            g["paths"].update(p for p in e.get("paths", []) if p)
            g["bad"] += e["outcome"] != "ok"
            g["ms"] |= (
                e["detail"] in ("git commit", "git push", "kart: git commit")
                or e["tool"] == "Agent"
            )
        events = []
        for (who, shape), g in sorted(
            groups.items(), key=lambda kv: (kv[1]["ts"], kv[0])
        ):
            events.append(
                {
                    "kind": "tool",
                    "tool": f"{who} · {shape}" + (f" ×{g['n']}" if g["n"] > 1 else ""),
                    "paths": [p for p, _ in g["paths"].most_common(2)],
                    "outcome": "ok" if not g["bad"] else f"{g['bad']} not ok",
                    "milestone": g["ms"],
                    "ts": g["ts"],
                }
            )
        out.append({"turn": t["turn"], "ts": t["ts"], "events": events})
    return out, orphans


def drawio(flow_json: Path, out_dir: Path) -> dict:
    import side_runner

    flow = json.loads(flow_json.read_text())
    session_id = flow.get("transcript_sha256_16") or flow_json.parent.name
    out_dir.mkdir(parents=True, exist_ok=True)
    record = out_dir / f"session-{session_id}.json"
    if record.exists():
        raise SystemExit(
            f"flow drawio: {record} exists; the record is append-only, so a replay "
            "goes to a fresh directory"
        )
    marks = {}
    for m in flow.get("marks", []):
        if m["turn"] not in marks or m.get("marked_by") == "operator":
            marks[m["turn"]] = m
    turns, orphans = turns_from(flow)
    last = None
    for t in turns:
        last = side_runner.on_turn(out_dir, session_id, t, marks)
    summary = {
        "session": session_id,
        "turns": len(turns),
        "nodes": sum(len(t["events"]) for t in turns),
        "orphan_events": orphans,
        "record": last and last["record"],
        "view": last and last["view"],
    }
    print(json.dumps(summary, sort_keys=True))
    return summary


def _run(script: str, args: list[str]) -> None:
    sys.argv = [script, *args]
    runpy.run_path(str(HERE / script), run_name="__main__")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "map":
        _run("run_flow.py", args)
    elif cmd == "drawio":
        drawio(Path(args[0]), Path(args[1]))
    elif cmd == "batch":
        _run("batch_flow.py", args)
    elif cmd == "merge":
        _run("merge_flows.py", args)
    elif cmd == "all":
        out = Path(args[-1])
        _run("run_flow.py", args)
        drawio(out / "session-flow.json", out / "drawio")
    else:
        raise SystemExit(f"flow: unknown command {cmd!r}\n\n{__doc__}")


if __name__ == "__main__":
    main()
