#!/usr/bin/env python3
"""Merge several session-flow.json maps and list what sticks out.

Deterministic: same maps in, same bytes out. Shape only, like make_flow:
no prompt text, no tool output, only kinds, verbs, repos, counts and times.

What "sticks out" is computed, never judged:
  - procedures (3-step tool-shape sequences inside one turn) seen in 3+
    sessions, or 3+ times in one: the repetition ladder's "offer" rung
  - outlier kinds (tool + verb that failed) that recur across sessions
  - repos every session touched, and repos only one session touched
  - per-session ratios (subagent share, outlier rate, calls per turn) more
    than 1.5x away from the median of the set
  - operator marks, whichever session they came from

  python3 merge_flows.py OUT_DIR LABEL=path/to/session-flow.json [...]
"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

LADDER = 3  # the operator's repetition ladder: 1 does it, 2 notices, 3 offers
RATIO = 1.5


def repo_key(path: str) -> str:
    """One name per repo across boxes: drop the home layout, fold worktrees."""
    if path.startswith("/tmp/") or path.startswith("/"):
        return "(not a repo) /" + "/".join(path.strip("/").split("/")[:1])
    p = path.strip("/")
    for pre in ("github/willow-memory/", "github/forge-play/", "github/"):
        if p.startswith(pre):
            p = p[len(pre) :]
            break
    parts = p.split("/")
    for i, part in enumerate(parts):
        if part in ("worktrees", ".worktrees"):
            return "/".join(parts[:i]) + " (worktree)"
    return parts[0] if parts and parts[0] else "(home)"


def repo_name(repo: str) -> str:
    """A map-time repo path (a directory holding .git) -> its name. A worktree
    folds into its parent repo: willow-mcp/worktrees/x -> willow-mcp (worktree)."""
    parts = repo.strip("/").split("/")
    for i, part in enumerate(parts):
        if part in ("worktrees", ".worktrees") and i:
            return parts[i - 1] + " (worktree)"
    return parts[-1]


def shape(e: dict) -> str:
    tool = e["tool"].split("__")[-1]
    detail = str(e.get("detail", ""))
    verb = detail.split(" · ")[0] if detail.startswith("kart:") else ""
    if e["tool"] == "Bash" or e["tool"] in ("Read", "Write", "Edit"):
        verb = detail
    return f"{tool}:{verb}" if verb else tool


def load(label: str, path: Path) -> dict:
    d = json.loads(path.read_text())
    ev = d["events"]
    tools = [e for e in ev if e.get("kind") == "tool"]
    turns = [e for e in ev if e.get("kind") == "turn"]
    return {
        "label": label,
        "sha": d.get("transcript_sha256_16", ""),
        "tools": tools,
        "turns": turns,
        "marks": d.get("marks", []),
        "first": ev[0]["ts"][:16] if ev else "",
        "last": ev[-1]["ts"][:16] if ev else "",
    }


def trigrams(tools: list[dict]) -> collections.Counter:
    by = collections.defaultdict(list)
    for e in tools:
        if e["actor"] == "desk":  # the desk's own procedure, not a builder's
            by[e["turn"]].append(shape(e))
    c = collections.Counter()
    for seq in by.values():
        for i in range(len(seq) - 2):
            c[" → ".join(seq[i : i + 3])] += 1
    return c


def main() -> None:
    out = Path(sys.argv[1])
    sessions = [
        load(lbl, Path(p)) for lbl, p in (a.split("=", 1) for a in sys.argv[2:])
    ]
    sessions.sort(key=lambda s: (s["first"], s["label"]))
    out.mkdir(parents=True, exist_ok=True)

    per = {}
    for s in sessions:
        t = s["tools"]
        n = len(t) or 1
        per[s["label"]] = {
            "turns": len(s["turns"]),
            "calls": len(t),
            "desk": sum(e["actor"] == "desk" for e in t),
            "subagent_share": round(sum(e["actor"] != "desk" for e in t) / n, 3),
            "outlier_rate": round(sum(e["outcome"] != "ok" for e in t) / n, 3),
            "calls_per_turn": round(len(t) / (len(s["turns"]) or 1), 1),
            "span": f"{s['first']} → {s['last']}",
            "sha": s["sha"],
        }

    odd = []
    for k in ("subagent_share", "outlier_rate", "calls_per_turn"):
        med = statistics.median(p[k] for p in per.values())
        for lbl, p in sorted(per.items()):
            v = p[k]
            if med and (v > med * RATIO or v < med / RATIO):
                odd.append((k, lbl, v, med))
            elif not med and v:
                odd.append((k, lbl, v, med))

    tri = {s["label"]: trigrams(s["tools"]) for s in sessions}
    seen_in = collections.defaultdict(set)
    total = collections.Counter()
    for lbl, c in tri.items():
        for g, n in c.items():
            seen_in[g].add(lbl)
            total[g] += n
    procs = sorted(
        (g for g in total if len(seen_in[g]) >= LADDER or total[g] >= LADDER),
        key=lambda g: (-len(seen_in[g]), -total[g], g),
    )

    fails = collections.defaultdict(lambda: collections.Counter())
    for s in sessions:
        for e in s["tools"]:
            if e["outcome"] != "ok":
                fails[shape(e) + f" [{e['outcome']}]"][s["label"]] += 1
    recurring = sorted(
        (k for k in fails if len(fails[k]) >= 2),
        key=lambda k: (-len(fails[k]), -sum(fails[k].values()), k),
    )

    repos = collections.defaultdict(set)
    for s in sessions:
        for e in s["tools"]:
            if "repos" in e:  # decided at map time by run_flow, on the box itself
                keys = [r if r.startswith("(") else repo_name(r) for r in e["repos"]]
            else:  # older or foreign maps: fall back to the path heuristic
                keys = [repo_key(p) for p in e.get("paths", []) if p.strip("/")]
            for k in keys:
                repos[k].add(s["label"])
    everywhere = sorted(r for r, ls in repos.items() if len(ls) == len(sessions))
    once = sorted((r, next(iter(ls))) for r, ls in repos.items() if len(ls) == 1)

    marks = [dict(m, session=s["label"]) for s in sessions for m in s["marks"]]

    L = [
        "# Merged session flows",
        "",
        "Shape only. Computed, not judged: every line below is a count from the maps.",
        "",
        "## The sessions",
        "",
        "| session | span (UTC) | turns | calls | desk | subagent share | outlier rate | calls/turn | transcript sha |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    L += [
        f"| {lbl} | {p['span']} | {p['turns']} | {p['calls']} | {p['desk']} | {p['subagent_share']} | {p['outlier_rate']} | {p['calls_per_turn']} | `{p['sha']}` |"
        for lbl, p in per.items()
    ]
    L += [
        "",
        "## What sticks out",
        "",
        f"### Ratios more than {RATIO}x off the median",
        "",
    ]
    L += [f"- **{lbl}** {k} = {v} (median {med})" for k, lbl, v, med in odd] or [
        "- none"
    ]
    L += [
        "",
        f"### Desk procedures at the offer rung (in {LADDER}+ sessions, or {LADDER}+ times)",
        "",
        "| procedure (3 steps in one turn) | sessions | times |",
        "|---|---|---|",
    ]
    L += [
        f"| `{g}` | {len(seen_in[g])} ({', '.join(sorted(seen_in[g]))}) | {total[g]} |"
        for g in procs[:25]
    ] or ["| none | | |"]
    if len(procs) > 25:
        L.append(f"| … {len(procs) - 25} more in merged.json | | |")
    L += [
        "",
        "### Failures that recur across sessions",
        "",
        "| shape [outcome] | sessions: count |",
        "|---|---|",
    ]
    L += [
        f"| `{k}` | "
        + ", ".join(f"{lbl}: {n}" for lbl, n in sorted(fails[k].items()))
        + " |"
        for k in recurring
    ] or ["| none | |"]
    L += ["", "### Repos", ""]
    real = {r: ls for r, ls in repos.items() if not r.startswith("(")}
    outside = {r: ls for r, ls in repos.items() if r.startswith("(")}
    L.append("- in every session: " + (", ".join(everywhere) or "none"))
    L.append(
        "- repos, by sessions touching them: "
        + ", ".join(
            f"{r} ({len(real[r])})"
            for r in sorted(real, key=lambda r: (-len(real[r]), r))
        )
    )
    L.append(
        "- outside any repo, top 8: "
        + ", ".join(
            f"{r.removeprefix('(not a repo) ')} ({len(outside[r])})"
            for r in sorted(outside, key=lambda r: (-len(outside[r]), r))[:8]
        )
    )
    single = [(r, lbl) for r, lbl in once if not r.startswith("(")]
    L += [f"- repo only in {lbl}: {r}" for r, lbl in single[:15]]
    days: dict[str, dict[str, int]] = {}
    for s in sessions:
        d = days.setdefault(
            s["first"][:10],
            {"sessions": 0, "turns": 0, "calls": 0, "polls": 0, "subagent": 0},
        )
        d["sessions"] += 1
        d["turns"] += len(s["turns"])
        d["calls"] += len(s["tools"])
        d["polls"] += sum(e["tool"].endswith("task_status") for e in s["tools"])
        d["subagent"] += sum(e["actor"] != "desk" for e in s["tools"])
    L += [
        "",
        "### By day (UTC, by session start)",
        "",
        "| day | sessions | turns | calls | status polls | subagent calls |",
        "|---|---|---|---|---|---|",
    ]
    L += [
        f"| {k} | {v['sessions']} | {v['turns']} | {v['calls']} | {v['polls']} | {v['subagent']} |"
        for k, v in sorted(days.items())
    ]
    L += [
        "",
        "### Operator marks (measurement, from whichever session carried them)",
        "",
        "| session | turn | kind | by | what |",
        "|---|---|---|---|---|",
    ]
    L += [
        f"| {m['session']} | T{m['turn']} | {m['kind']} | {m.get('marked_by', '')} | {str(m.get('what', ''))[:90]} |"
        for m in marks
    ] or ["| none | | | | |"]
    (out / "merged.md").write_text("\n".join(L) + "\n")
    (out / "merged.json").write_text(
        json.dumps(
            {
                "sessions": per,
                "odd": [list(x) for x in odd],
                "procedures": {
                    g: {"sessions": sorted(seen_in[g]), "times": total[g]}
                    for g in procs
                },
                "recurring_failures": {
                    k: dict(sorted(fails[k].items())) for k in recurring
                },
                "repos": {r: sorted(ls) for r, ls in sorted(repos.items())},
                "marks": marks,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    print(
        f"{len(sessions)} sessions, {len(procs)} procedures at the offer rung, {len(recurring)} recurring failures -> {out}"
    )


if __name__ == "__main__":
    main()
