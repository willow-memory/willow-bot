#!/usr/bin/env python3
"""Side runner: JSON (the record) -> .drawio (the view), one turn at a time.

ROUGH DRAFT. Written on paper in the 10-01/02 remote session; first run
2026-10-02 at the desk through flow.py drawio (replayed maps, byte-identical
twice). One fix from that run: a rebuilt view recomputes each turn's hash.

Every turn, the session hands the runner one turn's events. The runner:
  1. appends them to session-<uuid>.json      (append-only, the record)
  2. appends nodes/edges to session-<uuid>.drawio (the view)
  3. stamps both with the turn's timestamp and hash, so a lookup reads the
     files as they stand and never re-reads the transcript.

Rules it keeps:
  - JSON first. If the .drawio write fails, the record is still whole.
  - The view is never trusted over the record: if the .drawio is missing,
    unreadable, or its last turn doesn't match the JSON's, it is rebuilt from
    the JSON, loudly.
  - Shape, not content: nodes carry kinds, paths, states, times. No prompt text.
  - Human marks (failure/direction/flag) are their own layer; the runner only
    draws them, it never writes them.
  - Uncompressed draw.io XML, so it diffs in git and any tool can read it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROW_H, COL_W, NODE_W, NODE_H = 90, 230, 200, 50
STYLE = {
    "turn": "rounded=1;whiteSpace=wrap;fillColor=#ffffff;strokeColor=#555555;",
    "tool": "rounded=1;whiteSpace=wrap;fillColor=#f5f5f5;strokeColor=#999999;fontSize=10;",
    "milestone": "shape=hexagon;whiteSpace=wrap;fillColor=#eaf2fb;strokeColor=#2e6da4;",
    "outlier": "rounded=1;whiteSpace=wrap;fillColor=#fdecea;strokeColor=#c0392b;strokeWidth=2;",
    "grant": "shape=rhombus;whiteSpace=wrap;fillColor=#fef9e7;strokeColor=#b7950b;",
    "mark:failure": "strokeColor=#c0392b;strokeWidth=3;fillColor=#fdecea;",
    "mark:direction": "strokeColor=#d68910;strokeWidth=3;fillColor=#fef5e7;",
    "mark:flag": "strokeColor=#7d3c98;strokeWidth=3;fillColor=#f4ecf7;",
    "mark:conversational": "strokeColor=#148f77;strokeWidth=3;fillColor=#e8f6f3;",
    "mark:agent-reported": "strokeColor=#5d6d7e;strokeWidth=2;dashed=1;fillColor=#eaf2f8;",  # agent reported, unattested
    "edge": "endArrow=block;html=1;",
    "side": "dashed=1;endArrow=open;html=1;",
}


# ── the record ─────────────────────────────────────────────────────────────


def append_record(json_path: Path, turn: dict) -> str:
    """Append one turn (JSON Lines) and return its hash. The record is append-only."""
    line = json.dumps(turn, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(line.encode()).hexdigest()[:16]
    with json_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return digest


def read_record(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    return [
        json.loads(line) for line in json_path.read_text().splitlines() if line.strip()
    ]


# ── the view ───────────────────────────────────────────────────────────────


def new_diagram(session_id: str) -> ET.ElementTree:
    mxfile = ET.Element("mxfile", host="willow-side-runner")
    diagram = ET.SubElement(
        mxfile, "diagram", id=session_id, name=f"session {session_id[:8]}"
    )
    model = ET.SubElement(diagram, "mxGraphModel", grid="1", gridSize="10")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    return ET.ElementTree(mxfile)


def _root(tree: ET.ElementTree) -> ET.Element:
    return tree.getroot().find("./diagram/mxGraphModel/root")


def _vertex(root, cid, label, style, x, y, meta: dict) -> None:
    cell = ET.SubElement(
        root, "mxCell", id=cid, value=label, style=style, vertex="1", parent="1"
    )
    ET.SubElement(
        cell,
        "mxGeometry",
        x=str(x),
        y=str(y),
        width=str(NODE_W),
        height=str(NODE_H),
        **{"as": "geometry"},
    )
    # draw.io keeps unknown attributes; meta rides along for lookup without parsing labels
    for k, v in meta.items():
        cell.set(f"data-{k}", str(v))


def _edge(root, cid, src, dst, style) -> None:
    cell = ET.SubElement(
        root,
        "mxCell",
        id=cid,
        style=style,
        edge="1",
        parent="1",
        source=src,
        target=dst,
    )
    ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})


def draw_turn(tree: ET.ElementTree, turn: dict, digest: str, marks: dict) -> None:
    """Append one turn: a node on the spine, side nodes for what happened in it."""
    root = _root(tree)
    n, ts = turn["turn"], turn["ts"]
    tid = f"T{n}"
    style = (
        STYLE["turn"] + STYLE.get(f"mark:{marks[n]['kind']}", "")
        if n in marks
        else STYLE["turn"]
    )
    label = f"T{n} {ts[11:16]}" + (
        f"\n{marks[n]['kind'].upper()}" if n in marks else ""
    )
    _vertex(
        root, tid, label, style, 0, n * ROW_H, {"ts": ts, "hash": digest, "turn": n}
    )
    if n > 1:
        _edge(root, f"e{n - 1}-{n}", f"T{n - 1}", tid, STYLE["edge"])
    for i, ev in enumerate(turn.get("events", [])):
        kind = (
            "outlier"
            if ev.get("outcome") not in (None, "ok")
            else "grant"
            if ev.get("kind") == "grant"
            else "milestone"
            if ev.get("milestone")
            else "tool"
        )
        sid = f"{tid}.{i}"
        label = f"{ev.get('tool', ev.get('kind', ''))}\n{', '.join(ev.get('paths', [])[:2])}"
        _vertex(
            root,
            sid,
            label,
            STYLE[kind],
            COL_W * (1 + i % 3),
            n * ROW_H + (i // 3) * 20,
            {"ts": ev.get("ts", ts), "outcome": ev.get("outcome", "ok")},
        )
        _edge(root, f"{sid}.e", tid, sid, STYLE["side"])


def last_drawn_turn(tree: ET.ElementTree) -> int:
    turns = [
        int(c.get("data-turn"))
        for c in _root(tree).iter("mxCell")
        if c.get("data-turn")
    ]
    return max(turns, default=0)


def write_atomic(tree: ET.ElementTree, path: Path) -> None:
    ET.indent(tree)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".drawio.tmp")
    with os.fdopen(fd, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
    os.chmod(tmp, 0o644)  # mkstemp makes 0600; a view is readable like any file
    os.replace(tmp, path)  # never a half-written view


def rebuild(
    json_path: Path, drawio_path: Path, session_id: str, marks: dict, why: str
) -> None:
    """The view disagrees with the record: the record wins, loudly."""
    print(
        f"side-runner: rebuilding {drawio_path.name} from the record ({why})",
        file=sys.stderr,
    )
    tree = new_diagram(session_id)
    for turn in read_record(json_path):
        # First run, 2026-10-02: the record row never carries _hash (it is set
        # after the line is written), so a rebuilt view lost every hash. The
        # hash is of the row as written, so recompute it the same way.
        line = json.dumps(turn, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(line.encode()).hexdigest()[:16]
        draw_turn(tree, turn, digest, marks)
    write_atomic(tree, drawio_path)


# ── one turn ───────────────────────────────────────────────────────────────


def on_turn(
    session_dir: Path, session_id: str, turn: dict, marks: dict | None = None
) -> dict:
    """Called once at the end of every turn (the OUT door)."""
    marks = marks or {}
    json_path = session_dir / f"session-{session_id}.json"
    drawio_path = session_dir / f"session-{session_id}.drawio"

    turn["_hash"] = digest = append_record(json_path, turn)  # 1. record first

    try:  # 2. then the view
        tree = (
            ET.parse(drawio_path) if drawio_path.exists() else new_diagram(session_id)
        )
        if last_drawn_turn(tree) != turn["turn"] - 1:
            rebuild(json_path, drawio_path, session_id, marks, "turn gap")
        else:
            draw_turn(tree, turn, digest, marks)
            write_atomic(tree, drawio_path)
    except (ET.ParseError, OSError) as exc:
        rebuild(json_path, drawio_path, session_id, marks, f"{type(exc).__name__}")

    return {
        "turn": turn["turn"],
        "ts": turn["ts"],
        "hash": digest,  # 3. the pile gets
        "record": str(json_path),
        "view": str(drawio_path),
    }  #    pointers only


if __name__ == "__main__":
    # usage sketch: echo '{"turn": 1, "ts": "...", "events": [...]}' | side_runner.py DIR UUID
    d, sid = Path(sys.argv[1]), sys.argv[2]
    print(json.dumps(on_turn(d, sid, json.load(sys.stdin))))
