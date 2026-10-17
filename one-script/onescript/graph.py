"""graph — a code-graph read that says when, where and whether it can be trusted.

Cites: SPEC.md ruling R15 and SPEC-tools.md phase T-cbm-4. The seam W4 uses
(T-cbm-1 `symbol` cites); nothing calls it yet.

It wraps `cbm` (codebase-memory-mcp), the binary named by `ONESCRIPT_CBM`
(default /home/user/tools/bin/cbm). onescript never imports cbm and never runs
its `install`, `update` or `--ui`: only the read tools below are allowed, and
`index_repository` only through `reindex`.

Every read comes back as `{state, why, stamp, rows, total}`:

  populated     the graph holds rows for the query
  empty         the graph is there and fresh and holds none (or has no such
                project); worded "not in the graph at <stamp>", never "does not
                exist"
  unreachable   no binary, a timeout, output that cannot be read, or a
                freshness that cannot be told; nothing is said about the code
  stale         the index is older than the checkout (refused, rows withheld)

`stamp` is {project, nodes, edges, head_sha, dirty, indexed_at, branch}. cbm
records no commit, only when (`projects.indexed_at` in its store); the branch
`list_projects` shows is the live one, not the one indexed. So an index is stale
when HEAD moved after `indexed_at` (the later of the HEAD commit's time and the
HEAD reflog's last move, which also catches a checkout of another branch or an
older commit), or a tracked file that differs from HEAD was modified after
`indexed_at`. The
resolution is a second: a commit made in the very second of indexing reads as
fresh. `dirty` is the stamp's own fact: tracked files differ from HEAD.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_CBM = "/home/user/tools/bin/cbm"
DEFAULT_CACHE = "~/.cache/codebase-memory-mcp"
TIMEOUT = 60

#: What a read may call. Nothing that installs, updates, or opens a UI.
READ_TOOLS = frozenset(
    {
        "search_graph",
        "query_graph",
        "trace_path",
        "get_code_snippet",
        "get_architecture",
        "search_code",
        "detect_changes",
    }
)
_META = frozenset({"list_projects", "index_status"})

Runner = Callable[[str, dict], str]


class Unreachable(Exception):
    """The graph could not be consulted; the message says why."""


def cbm_path() -> str:
    return os.environ.get("ONESCRIPT_CBM") or DEFAULT_CBM


def cbm_runner(path: str | None = None, timeout: int = TIMEOUT) -> Runner:
    """A runner that calls `cbm <tool> '<json>'`. Raises `Unreachable`."""
    exe = path or cbm_path()

    def run(tool: str, args: dict) -> str:
        if tool not in READ_TOOLS | _META | {"index_repository"}:
            raise Unreachable(f"cbm tool '{tool}' is not one this seam calls")
        try:
            p = subprocess.run(
                [exe, tool, json.dumps(args)],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise Unreachable(f"cbm: {type(e).__name__}: {e}") from e
        if p.returncode != 0 and not p.stdout.strip():
            raise Unreachable(f"cbm {tool}: exit {p.returncode}, no output")
        return p.stdout

    return run


def _git(root: str, *args: str) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise Unreachable(f"git: {type(e).__name__}: {e}") from e
    if p.returncode != 0:
        raise Unreachable(f"git {args[0]}: {p.stderr.strip() or p.returncode}")
    return p.stdout


def _indexed_at(project: str, cache: str | os.PathLike | None) -> float:
    base = Path(
        os.path.expanduser(
            str(cache or os.environ.get("ONESCRIPT_CBM_CACHE") or DEFAULT_CACHE)
        )
    )
    db = base / f"{project}.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "select indexed_at from projects where name = ?", (project,)
            ).fetchone()
        finally:
            con.close()
        return (
            datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (sqlite3.Error, TypeError, ValueError) as e:
        raise Unreachable(f"cannot tell when '{project}' was indexed: {e}") from e


def _facts(root: str) -> dict:
    head = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changed = [
        p for p in _git(root, "diff", "--name-only", "-z", "HEAD").split("\0") if p
    ]
    when = [int(_git(root, "log", "-1", "--format=%ct", "HEAD").strip())]
    try:  # the last time HEAD moved, however it moved
        gd = _git(root, "reflog", "-1", "--date=unix", "--format=%gd", "HEAD").strip()
        when.append(int(gd.split("@{", 1)[1].rstrip("}")))  # HEAD@{<unix time>}
    except (Unreachable, ValueError, IndexError):
        pass
    return {"head": head, "branch": branch, "changed": changed, "moved": max(when)}


def _projects(runner: Runner) -> list[dict]:
    try:
        got = json.loads(runner("list_projects", {}))["projects"]
    except (ValueError, KeyError, TypeError) as e:
        raise Unreachable(f"cbm list_projects: unreadable ({type(e).__name__})") from e
    if not isinstance(got, list):
        raise Unreachable("cbm list_projects: unreadable (projects is not a list)")
    return [p for p in got if isinstance(p, dict)]


def parse(out: str) -> tuple[int, list[str]]:
    """(total, rows) from a cbm result. Text results carry `total: N` then the
    rows under `results:`; a JSON result is its list (or `results`/`rows`)."""
    text = out.strip()
    if text[:1] in "[{":
        try:
            j = json.loads(text)
        except ValueError:
            j = None
        if isinstance(j, dict):
            j = next((j[k] for k in ("results", "rows", "matches") if k in j), None)
        if isinstance(j, list):
            return len(j), [json.dumps(r, sort_keys=True) for r in j]
    total, rows, inside = None, [], False
    for ln in text.splitlines():
        if ln.startswith("total:"):
            try:
                total = int(ln.split(":", 1)[1])
            except ValueError:
                raise Unreachable("cbm: unreadable total") from None
        elif ln.startswith("results:"):
            inside = True
        elif ln.startswith(("has_more:", "hint:")):
            inside = False
        elif inside and ln.strip():
            rows.append(ln.rstrip())
    if total is None:
        raise Unreachable("cbm: output has no total; cannot tell empty from broken")
    return total, rows


def _out(state: str, why: str, stamp: dict | None, rows=(), total: int = 0) -> dict:
    return {
        "state": state,
        "why": why,
        "stamp": stamp,
        "rows": list(rows),
        "total": total,
    }


def _said(stamp: dict) -> str:
    return (
        f"{stamp['project']} nodes={stamp['nodes']} edges={stamp['edges']} "
        f"head={stamp['head_sha'][:12]} dirty={stamp['dirty']}"
    )


def graph_read(
    project: str,
    tool: str,
    args: dict | None = None,
    *,
    runner: Runner | None = None,
    cache: str | os.PathLike | None = None,
) -> dict:
    """One graph read, stamped. Never raises; never collapses the states."""
    if tool not in READ_TOOLS:
        return _out(
            "unreachable", f"'{tool}' is not a graph read this seam makes", None
        )
    try:
        run = runner or cbm_runner()
        found = [p for p in _projects(run) if p.get("name") == project]
        if not found:
            return _out("empty", f"project '{project}' is not in the graph", None)
        p = found[0]
        root = p.get("root_path")
        if not isinstance(root, str) or not os.path.isdir(root):
            raise Unreachable(f"project root '{root}' is not a directory here")
        f = _facts(root)
        stamp = {
            "project": project,
            "nodes": p.get("nodes"),
            "edges": p.get("edges"),
            "head_sha": f["head"],
            "dirty": bool(f["changed"]),
            "indexed_at": _indexed_at(project, cache),
            "branch": f["branch"],
        }
        if f["moved"] > stamp["indexed_at"]:
            return _out(
                "stale",
                "HEAD moved after the index was made; re-index before asking",
                stamp,
            )
        for rel in f["changed"]:
            try:
                newer = os.stat(os.path.join(root, rel)).st_mtime > stamp["indexed_at"]
            except OSError:
                newer = True  # gone from the tree: the index may still hold it
            if newer:
                return _out(
                    "stale",
                    f"'{rel}' changed after the index was made; re-index before asking",
                    stamp,
                )
        total, rows = parse(run(tool, {**(args or {}), "project": project}))
    except Unreachable as e:
        return _out("unreachable", str(e), None)
    if total == 0:
        return _out("empty", f"not in the graph at {_said(stamp)}", stamp)
    return _out("populated", "", stamp, rows, total)


def reindex(root: str | os.PathLike, *, runner: Runner | None = None) -> dict:
    """Index (again) the repository at `root`: the one call this seam makes that
    writes, and only to cbm's own store. Returns {state, why}."""
    try:
        out = (runner or cbm_runner())("index_repository", {"repo_path": str(root)})
    except Unreachable as e:
        return {"state": "unreachable", "why": str(e)}
    return {"state": "populated", "why": out.strip()[:200]}
