#!/usr/bin/env python3
"""Run make_flow.py on this box without editing it.

make_flow.py was written in a remote session whose home was /home/user/, and
it hardcodes that root. This wrapper points it at the box it runs on and
leaves the original byte-for-byte as it came back from the Nest:

  - ROOT: $FLOW_ROOT, else the caller's home
  - PATH_RE: matches paths under ROOT and /tmp/claude-<uid>/
  - repo_of: the deepest directory under ROOT that holds a .git, so
    github/willow-memory/willows-grove is one repo, not "github"

It also sees inside Kart, where the desk does its real work:

  - a task_submit is read like a Bash call: its command text gives the verb
    and the paths
  - its outcome comes from the task_status results that name the same task
    id: the last terminal status wins, and a non-zero returncode or a failed
    status makes the submit an outlier ("kart exit 128"), even though the
    tool call itself succeeded

  python3 run_flow.py TRANSCRIPT.jsonl [SUBAGENT_DIR] OUT_DIR
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import make_flow  # noqa: E402

ROOT = os.environ.get("FLOW_ROOT") or str(Path.home()) + "/"
make_flow.ROOT = ROOT
make_flow.PATH_RE = re.compile(
    rf"({re.escape(ROOT)}[\w.\-/]+|/tmp/claude-\d+/[\w.\-/]+)"
)

SUBMIT = ("task_submit",)
STATUS = ("task_status",)
TASK_ID = re.compile(r'"task_id":\s*"(\w+)"')
STATE = re.compile(r'"status":\s*"(\w+)"')
RC = re.compile(r'"returncode":\s*(-?\d+)')
TERMINAL = {"completed", "failed", "error", "cancelled", "timeout"}


NOT_A_REPO = "(not a repo) "


@lru_cache(maxsize=None)
def _repo_of(path: str) -> str:
    """The deepest directory under ROOT holding a .git. A path in no repo is
    labelled as such with its first two parts (~/github/willow-memory,
    /tmp/claude-1000), never passed off as a repo called "github" or "tmp"."""
    if path.startswith("/"):  # outside ROOT: /tmp, /etc, /proc, ...
        parts = path.strip("/").split("/")
        keep = (
            2
            if parts[0] == "tmp" and parts[1:2] and parts[1].startswith("claude-")
            else 1
        )
        return NOT_A_REPO + "/" + "/".join(parts[:keep])
    parts = path.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        if (Path(ROOT) / Path(*parts[:i]) / ".git").exists():
            return "/".join(parts[:i])
    return NOT_A_REPO + "~/" + "/".join(parts[:2]) if parts and parts[0] else "(home)"


make_flow.repo_of = _repo_of

# Shape, not content (Loki BD1B3052 finding 3): make_flow's bash_verb falls
# back to the command's first word, so `GITHUB_TOKEN=ghp_... gh` put the token
# in the record and the view. The scrub drops leading VAR=value assignments
# and keeps a fallback word only if it looks like a command name.
_bash_verb = make_flow.bash_verb
_CMD = re.compile(r"^[A-Za-z0-9_.+-]{1,40}$")


def _scrubbed_verb(cmd: str) -> str:
    words = cmd.strip().split()
    while words and "=" in words[0] and not words[0].startswith(("-", "=")):
        words.pop(0)  # env assignments, values included, never reach the record
    rest = " ".join(words)
    verb = _bash_verb(rest)
    if verb == (rest.split()[0] if rest.split() else "bash"):  # the fallback branch
        name = verb.rsplit("/", 1)[-1]
        return name if _CMD.match(name) else "command"
    return verb


make_flow.bash_verb = _scrubbed_verb


def _short(name: str) -> str:
    return name.split("__")[-1]


_events_from = make_flow.events_from

# Subagent work belongs to the desk turn that spawned it (gap 710eed7a0e61).
# make_flow runs the desk transcript first, so its Agent calls are known by
# the time a subagent file is read. A subagent's actor is "agent:<description>",
# so it is matched to the latest desk Agent call with that description that
# started no later than the subagent's own first event.
_SPAWNS: dict[str, list[tuple[str, int]]] = {}


def _spawn_turn(label: str, first_ts: str) -> int | None:
    best = None
    for ts, turn in _SPAWNS.get(label, []):
        if ts <= first_ts:
            best = turn
    return best


def events_from(rows: list[dict], actor: str) -> list[dict]:
    """make_flow's events, then a second pass that reads Kart."""
    ev = _events_from(rows, actor)
    if actor == "desk":
        _SPAWNS.clear()
        for e in ev:
            if e.get("kind") == "tool" and e["tool"] == "Agent":
                for key in filter(None, (e.get("detail"), e.get("agent_id"))):
                    _SPAWNS.setdefault(key, []).append((e["ts"], e["turn"]))
    elif ev:
        turn = _spawn_turn(actor.removeprefix("agent:"), ev[0]["ts"])
        for e in ev:
            e["spawned_at_turn"] = turn
            if turn is not None:
                e["turn"] = turn
    results = {}
    uses = []
    for o in rows:
        c = (o.get("message") or {}).get("content")
        if o.get("type") == "user" and isinstance(c, list):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_result":
                    body = x.get("content")
                    results[x.get("tool_use_id")] = (
                        body if isinstance(body, str) else make_flow.text_of(body)
                    ) or ""
        if o.get("type") == "assistant":
            for x in (o.get("message") or {}).get("content") or []:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    uses.append(x)
    tools = [e for e in ev if e["kind"] == "tool"]
    for e in tools:
        if e["tool"] in ("WebSearch",) or e["tool"].endswith(
            ("willow_web_search", "willow_institutional_search")
        ):
            # a search query is content, not shape: keep only a stable fingerprint
            q = str(e.get("detail", ""))
            e["detail"] = (
                "query " + hashlib.sha256(q.encode()).hexdigest()[:8] if q else ""
            )
    for e in tools:  # the repo is decided once, at map time, on the box that has them
        e["repos"] = sorted({_repo_of(p) for p in e.get("paths", []) if p.strip("/")})
    if len(tools) != len(uses):  # never guess a pairing
        return ev
    submits, final = {}, {}
    for e, x in zip(tools, uses):
        name, inp, body = (
            _short(x["name"]),
            x.get("input") or {},
            results.get(x.get("id"), ""),
        )
        if name in SUBMIT:
            cmd = str(inp.get("task", ""))
            e["detail"] = "kart: " + make_flow.bash_verb(cmd)
            e["paths"] = sorted(
                {make_flow.rel(p) for p in make_flow.PATH_RE.findall(cmd)}
            )
            m = TASK_ID.search(body)
            if m:
                e["kart_task"] = m.group(1)
                submits[m.group(1)] = e
        elif name in STATUS:
            tid = str(inp.get("task_id", ""))
            e["detail"] = f"kart status {tid}"
            st = STATE.search(body)
            if st and st.group(1) in TERMINAL:
                rc = RC.search(body)
                final[tid] = (st.group(1), int(rc.group(1)) if rc else None)
    for tid, (state, rc) in final.items():
        e = submits.get(tid)
        if not e:
            continue
        e["kart_status"], e["kart_rc"] = state, rc
        if state != "completed" or (rc not in (None, 0)):
            e["outcome"] = "error"
            e["detail"] += f" · kart {state}" + (
                f" exit {rc}" if rc is not None else ""
            )
    return ev


make_flow.events_from = events_from

# The run stamps its own version (next-pile, "3 record"): a map made by older
# code is not the same witness, and batch_flow re-maps when this changes.
FLOW_VERSION = hashlib.sha256(
    b"".join((HERE / n).read_bytes() for n in ("make_flow.py", "run_flow.py"))
).hexdigest()[:16]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    ).stdout


def stamp(outdir: Path) -> None:
    """Record what the commits section read, so it can be re-checked.

    make_flow reads `git log --all` per repo, i.e. every ref. For each repo it
    read, stamp HEAD and a hash of the full ref list (name + sha). Same refs
    -> same stamp; a moved ref shows up as a different fingerprint, so the
    commits section stops being an unrecorded live input."""
    path = outdir / "session-flow.json"
    d = json.loads(path.read_text())
    heads = {}
    for repo in sorted({c["repo"] for c in d.get("commits", [])} | set(_repos_read(d))):
        g = Path(ROOT) / repo
        if not (g / ".git").exists():
            continue
        refs = _git(g, "for-each-ref", "--format=%(refname) %(objectname)")
        heads[repo] = {
            "head": _git(g, "rev-parse", "HEAD").strip(),
            "refs": len(refs.splitlines()),
            "refs_sha256_16": hashlib.sha256(refs.encode()).hexdigest()[:16],
        }
    d["git_read"] = heads
    d["flow_version"] = FLOW_VERSION
    path.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    md = outdir / "session-flow.md"
    lines = [
        "",
        "## What the commits section read (git, at map time)",
        "",
        f"flow version `{FLOW_VERSION}`",
        "",
        "| repo | HEAD | refs | refs sha256 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r} | `{v['head'][:12]}` | {v['refs']} | `{v['refs_sha256_16']}` |"
        for r, v in heads.items()
    ] or ["| none | | | |"]
    md.write_text(md.read_text() + "\n".join(lines) + "\n")


def _repos_read(d: dict) -> set[str]:
    out = set()
    for e in d.get("events", []):
        for p in e.get("paths", []):
            if p.strip("/"):
                r = make_flow.repo_of(p)
                if not r.startswith(NOT_A_REPO):
                    out.add(r)
    return out


def main() -> None:
    make_flow.main()
    stamp(Path(sys.argv[-1]))


if __name__ == "__main__":
    main()
