#!/usr/bin/env python3
"""Deterministic session flow: transcript JSONL in, map out.

Same transcript -> same output. Shape, not content: operator turns are
numbered and timed, not quoted; tool calls keep their name, target paths,
command verb and outcome. Outliers (errors, refusals, failed checks) are
listed first, never folded into counts.

  python3 make_flow.py TRANSCRIPT.jsonl [SUBAGENT_DIR] OUT_DIR
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = "/home/user/"
PATH_RE = re.compile(r"(/home/user/[\w.\-/]+|/tmp/claude-0/[\w.\-/]+)")
NOISE_PREFIXES = (
    "<system-reminder>",
    "Another Claude session",
    "<task-notification>",
    "[SYSTEM NOTIFICATION",
    "Tool loaded",
    "<wake",
)


def rel(p: str) -> str:
    p = p.rstrip(".,;:)'\"")
    if p.startswith(ROOT):
        return p[len(ROOT) :]
    if "/scratchpad/" in p:
        return "scratchpad/" + p.split("/scratchpad/", 1)[1]
    return p


def repo_of(path: str) -> str:
    path = path.strip("/")
    return path.split("/", 1)[0] if "/" in path else "(home)"


def bash_verb(cmd: str) -> str:
    c = cmd.strip()
    for pat, v in (
        (r"\bgit clone\b", "git clone"),
        (r"\bgit commit\b", "git commit"),
        (r"\bgit push\b", "git push"),
        (r"\bpytest\b", "pytest"),
        (r"\bruff\b", "ruff"),
        (r"check_\w+\.py", "repo check"),
        (r"\bcurl\b", "curl"),
        (r"\bpip install\b", "pip install"),
        (r"\bgrep\b|\brg\b", "grep"),
        (r"\bsed -n\b|\bcat\b|\bhead\b", "read"),
        (r"python3 - <<", "python"),
        (r"\bgit (log|show|status|branch)\b", "git read"),
    ):
        if re.search(pat, c):
            return v
    return c.split()[0] if c.split() else "bash"


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def is_operator(o: dict) -> bool:
    if o.get("type") != "user" or o.get("isMeta"):
        return False
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list) and any(
        isinstance(x, dict) and x.get("type") == "tool_result" for x in c
    ):
        return False
    t = text_of(c).strip()
    return bool(t) and not t.startswith(NOISE_PREFIXES)


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def events_from(rows: list[dict], actor: str) -> list[dict]:
    ev, results = [], {}
    for o in rows:
        c = (o.get("message") or {}).get("content")
        if o.get("type") == "user" and isinstance(c, list):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_result":
                    body = x.get("content")
                    body = body if isinstance(body, str) else text_of(body)
                    results[x.get("tool_use_id")] = (
                        bool(x.get("is_error")),
                        body or "",
                    )
    turn = 0
    for o in rows:
        ts = o.get("timestamp", "")
        if actor == "desk" and is_operator(o):
            turn += 1
            ev.append(
                {
                    "ts": ts,
                    "actor": "operator",
                    "kind": "turn",
                    "turn": turn,
                    "chars": len(text_of((o.get("message") or {}).get("content"))),
                }
            )
            continue
        if o.get("type") != "assistant":
            continue
        for x in (o.get("message") or {}).get("content") or []:
            if not isinstance(x, dict) or x.get("type") != "tool_use":
                continue
            name, inp = x["name"], x.get("input") or {}
            e = {
                "ts": ts,
                "actor": actor,
                "kind": "tool",
                "tool": name,
                "turn": turn,
                "paths": [],
                "detail": "",
            }
            if name in ("Read", "Write", "Edit"):
                e["paths"] = [rel(inp.get("file_path", ""))]
                e["detail"] = {"Read": "read", "Write": "write", "Edit": "edit"}[name]
            elif name in ("Grep", "Glob"):
                e["paths"] = [rel(inp.get("path", ""))] if inp.get("path") else []
                e["detail"] = "search"
            elif name == "Bash":
                cmd = inp.get("command", "")
                e["detail"] = bash_verb(cmd)
                e["paths"] = sorted({rel(p) for p in PATH_RE.findall(cmd)})
            elif name == "Agent":
                e["detail"] = inp.get("description", "")
            elif name == "WebSearch":
                e["detail"] = inp.get("query", "")
            elif name == "WebFetch":
                e["detail"] = re.sub(r"^https?://([^/]+).*", r"\1", inp.get("url", ""))
            elif name.startswith("mcp__"):
                bits = [
                    str(inp[k])
                    for k in ("owner", "repo", "pullNumber", "app_id", "method")
                    if k in inp
                ]
                e["detail"] = " ".join(bits)
            err, body = results.get(x.get("id"), (False, ""))
            judged = name.startswith(("WebFetch", "mcp__Willow")) or (
                name == "Bash" and len(body) < 2000
            )
            refused = judged and bool(
                re.search(
                    r"Not Found|EGRESS_BLOCKED|would be reformatted|blocked by the network",
                    body,
                )
            )
            e["outcome"] = "error" if err else ("refused" if refused else "ok")
            m = re.search(r"agentId: (\w+)", body)
            if m:
                e["agent_id"] = m.group(1)
            ev.append(e)
    return ev


def main() -> None:
    transcript, outdir = Path(sys.argv[1]), Path(sys.argv[-1])
    subdir = Path(sys.argv[2]) if len(sys.argv) == 4 else None
    outdir.mkdir(parents=True, exist_ok=True)
    rows = load(transcript)
    ev = events_from(rows, "desk")
    names = {e["agent_id"]: e["detail"] for e in ev if e.get("agent_id")}
    if subdir and subdir.exists():
        for f in sorted(subdir.glob("agent-*.jsonl")):
            aid = f.stem.removeprefix("agent-")
            ev += events_from(load(f), f"agent:{names.get(aid, aid)}")
    ev.sort(key=lambda e: (e["ts"], e["actor"]))
    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()[:16]

    marks_path = outdir / "marks.json"
    marks = json.loads(marks_path.read_text()) if marks_path.exists() else []
    marked = {}
    for m in marks:  # operator marks win the node colour over self-flags
        if m["turn"] not in marked or m["marked_by"] == "operator":
            marked[m["turn"]] = m
    tools = [e for e in ev if e["kind"] == "tool"]
    outliers = [e for e in tools if e["outcome"] != "ok"]
    touched = collections.defaultdict(
        lambda: {"read": set(), "write": set(), "by": set(), "first": "", "last": ""}
    )
    for e in tools:
        for p in e["paths"]:
            if not p.strip("/"):
                continue
            r = touched[repo_of(p)]
            kind = "write" if e["detail"] in ("write", "edit", "git commit") else "read"
            r[kind].add(p)
            r["by"].add(e["actor"].split(":")[0])
            r["first"] = r["first"] or e["ts"][11:16]
            r["last"] = e["ts"][11:16]
    import subprocess

    t0, t1 = ev[0]["ts"], ev[-1]["ts"]
    commits = []
    for repo in sorted(touched):
        g = Path(ROOT) / repo
        if not (g / ".git").exists():
            continue
        out = subprocess.run(
            [
                "git",
                "-C",
                str(g),
                "log",
                "--all",
                f"--since={t0}",
                f"--until={t1}",
                "--name-only",
                "--format=@@%H %cI %s",
            ],
            capture_output=True,
            text=True,
        ).stdout
        cur = None
        for line in out.splitlines():
            if line.startswith("@@"):
                sha, when, subj = line[2:].split(" ", 2)
                cur = {
                    "repo": repo,
                    "sha": sha[:7],
                    "ts": when,
                    "subject": subj,
                    "files": [],
                }
                commits.append(cur)
            elif line.strip() and cur:
                cur["files"].append(line.strip())
                touched[repo]["write"].add(f"{repo}/{line.strip()}")
    commits.sort(key=lambda c: c["ts"])
    milestones = [
        e
        for e in tools
        if e["detail"] in ("git clone", "git commit", "git push")
        or e["tool"]
        in (
            "mcp__github__create_pull_request",
            "Agent",
            "mcp__claude-code-remote__add_repo",
        )
    ]

    (outdir / "session-flow.json").write_text(
        json.dumps(
            {
                "transcript_sha256_16": digest,
                "events": ev,
                "commits": commits,
                "marks": marks,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )

    # Mermaid: one node per operator turn that led to tool work; milestones as side nodes.
    lines = ["flowchart TD"]
    by_turn = collections.defaultdict(list)
    for e in tools:
        if e["actor"] == "desk":
            by_turn[e["turn"]].append(e)
    turns = [e for e in ev if e["kind"] == "turn"]
    prev = None
    for t in turns:
        work = by_turn.get(t["turn"], [])
        repos = sorted({repo_of(p) for e in work for p in e["paths"] if p})
        cnt = collections.Counter(
            e["tool"].replace("mcp__", "").split("__")[-1] for e in work
        )
        label = f"T{t['turn']} {t['ts'][11:16]}"
        if cnt:
            label += "<br/>" + ", ".join(f"{k}×{v}" for k, v in sorted(cnt.items()))
        if repos:
            label += "<br/>" + " · ".join(repos[:4]) + (" …" if len(repos) > 4 else "")
        node = f"T{t['turn']}"
        if t["turn"] in marked:
            mk = marked[t["turn"]]
            label += (
                "<br/>"
                + ("MARKED " if mk["marked_by"] == "operator" else "")
                + mk["kind"].upper()
                + (" by operator" if mk["marked_by"] == "operator" else " (unattested)")
            )
        lines.append(f'  {node}["{label}"]')
        if t["turn"] in marked:
            color = {
                "failure": "fill:#fdecea,stroke:#c0392b",
                "direction": "fill:#fef5e7,stroke:#d68910",
                "flag": "fill:#f4ecf7,stroke:#7d3c98",
                "conversational": "fill:#e8f6f3,stroke:#148f77",
            }.get(
                marked[t["turn"]]["kind"],
                "fill:#eaf2f8,stroke:#5d6d7e,stroke-dasharray:5 5",
            )
            lines.append(f"  style {node} {color},stroke-width:3px")
        if prev:
            lines.append(f"  {prev} --> {node}")
        prev = node
        for i, e in enumerate(
            x for x in work if x in milestones or x["outcome"] != "ok"
        ):
            sid = f"{node}_{i}"
            tag = (
                e["detail"]
                if e["tool"] == "Bash"
                else e["tool"].split("__")[-1] + " " + e["detail"]
            )
            tag = tag.replace('"', "'")[:48]
            shape = (
                f'{sid}{{{{"{tag}"}}}}' if e["outcome"] != "ok" else f'{sid}(["{tag}"])'
            )
            lines.append(f"  {node} -.-> {shape}")
            if e["outcome"] != "ok":
                lines.append(f"  style {sid} stroke:#c0392b,stroke-width:2px")
    mmd = "\n".join(lines) + "\n"
    (outdir / "session-flow.mmd").write_text(mmd)

    md = [
        f"# Session flow: {transcript.stem}",
        "",
        f"Generated by `make_flow.py` from the transcript (sha256 prefix `{digest}`) "
        "and its subagent transcripts. Deterministic: same input, same map. "
        "Shape, not content: operator turns are numbered and timed, not quoted.",
        "",
        f"- operator turns: {len(turns)}",
        f"- tool calls: {len(tools)} (desk {sum(e['actor'] == 'desk' for e in tools)}, "
        f"subagents {sum(e['actor'] != 'desk' for e in tools)})",
        f"- outliers (errors / refusals): {len(outliers)}",
        "",
        "## Outliers first",
        "",
        "### Marked by the operator (measurement, not inference)",
        "",
        "| turn | kind | what | marked at | evidence |",
        "|---|---|---|---|---|",
    ]
    op = [m for m in marks if m["marked_by"] == "operator"]
    me = [m for m in marks if m["marked_by"] != "operator"]
    md += [
        f"| T{m['turn']} | {m['kind']} | {m['what']} | T{m['marked_at_turn']} | "
        + ", ".join(f"T{x}" for x in m.get("evidence_turns", []))
        + " |"
        for m in op
    ]
    md += [
        "",
        "### Agent reported (NOT attestation; for the operator to confirm or reject)",
        "",
        "| turn | kind | what | reported at | evidence |",
        "|---|---|---|---|---|",
    ]
    md += [
        f"| T{m['turn']} | {m['kind']} | {m['what']} | T{m['marked_at_turn']} | "
        + ", ".join(f"T{x}" for x in m.get("evidence_turns", []))
        + " |"
        for m in me
    ]
    md += [
        "",
        "### Detected from tool results",
        "",
        "| time | actor | tool | detail | outcome |",
        "|---|---|---|---|---|",
    ]
    md += [
        f"| {e['ts'][11:19]} | {e['actor']} | {e['tool']} | {e['detail'][:60]} | {e['outcome']} |"
        for e in outliers
    ]
    md += [
        "",
        "## Where the session went (by repo)",
        "",
        "| repo | read | written | by | first–last |",
        "|---|---|---|---|---|",
    ]
    for repo in sorted(touched):
        r = touched[repo]
        md.append(
            f"| {repo} | {len(r['read'])} | {len(r['write'])} | {', '.join(sorted(r['by']))} | {r['first']}–{r['last']} |"
        )
    md += ["", "## Files written", ""]
    for repo in sorted(touched):
        for p in sorted(touched[repo]["write"]):
            md.append(f"- `{p}`")
    md += [
        "",
        "## Commits made in the session (from git, not the transcript)",
        "",
        "| when (UTC) | repo | sha | subject | files |",
        "|---|---|---|---|---|",
    ]
    md += [
        f"| {c['ts'][:19]} | {c['repo']} | `{c['sha']}` | {c['subject'][:70]} | {len(c['files'])} |"
        for c in commits
    ]
    md += [
        "",
        "## Milestones",
        "",
        "| time | turn | what | detail |",
        "|---|---|---|---|",
    ]
    md += [
        f"| {e['ts'][11:19]} | T{e['turn']} | {e['tool']} | {e['detail'][:70]} |"
        for e in milestones
    ]
    md += ["", "## Flow", "", "```mermaid", mmd.rstrip(), "```", ""]
    (outdir / "session-flow.md").write_text("\n".join(md))
    print(
        f"{len(turns)} turns, {len(tools)} tool calls, {len(outliers)} outliers -> {outdir}"
    )


if __name__ == "__main__":
    main()
