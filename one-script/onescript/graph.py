"""graph — a code-graph read that says when, where and whether it can be trusted.

Cites: SPEC.md rulings R15 and R18 and SPEC-tools.md phase T-cbm-4. The seam W4
uses (T-cbm-1 `symbol` cites, `trace_path`); nothing calls it yet.

It wraps `cbm` (codebase-memory-mcp), the binary named by `ONESCRIPT_CBM`
(default /home/user/tools/bin/cbm). onescript never imports cbm and never runs
its `install`, `update` or `--ui`: only the read tools below are allowed, and
`index_repository` only through `reindex`. A missing or broken binary is
`unreachable`, never an empty graph.

Every read comes back as `{state, why, stamp, rows, total}`:

  populated     the graph holds rows for the query
  empty         the graph is there and fresh and holds none (or has no such
                project); worded "not in the graph at <stamp>", never "does not
                exist"
  unreachable   no binary, a timeout, output that cannot be read, or a
                freshness that cannot be told; nothing is said about the code
  stale         the index is older than the checkout (refused, rows withheld)

R18 (recorded guess, 65%): `stale` is a refusal. It does not re-index behind the
caller's back, because a silent re-index changes what the next read says without
the caller having asked; the caller decides, and `reindex` is there to call.

`stamp` is {project, nodes, edges, head_sha, dirty, indexed_at, branch}. cbm
records no commit, only when (`projects.indexed_at` in its store, found under
`ONESCRIPT_CBM_CACHE`, else `CBM_CACHE_DIR`, else ~/.cache/codebase-memory-mcp);
the branch `list_projects` shows is the live one, not the one indexed. So an
index is stale when HEAD moved after `indexed_at` (the later of the HEAD
commit's time and the HEAD reflog's last move, which also catches a checkout of
another branch or an older commit), or ANY file git lists as tracked or
untracked-and-not-ignored (`git ls-files -co --exclude-standard`) has an mtime
after `indexed_at`, or is gone from the tree, or the git staging area (`.git/index`:
`git rm`, `git mv`, `git add -A`) was rewritten after `indexed_at`. LIMITS: the resolution is a second
(a commit or an edit in the very second of indexing reads as fresh; mtimes are compared by whole second); a file copied with an
old mtime (`cp -p`, `rsync -t`, `git stash pop` on some filesystems) after the
index was made is not seen; so a fresh read is "no sign of change", not proof.
`dirty` is the stamp's own fact: tracked files differ from HEAD, or there are
untracked files.
"""

from __future__ import annotations

import json
import os
import re
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
#: Tools for which an exit 1 with no output is cbm's answer for a name it does
#: not hold (observed), not a crash; the project was already confirmed to exist.
MISS_IS_SILENT = {"trace_path": "function_name", "get_code_snippet": "qualified_name"}
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
        # Any non-zero exit, and any stderr text, is a failure: partial stdout from a
        # killed or erroring cbm is never read as an answer. The one exception is
        # cbm's own miss: exactly rc 1, empty stdout, empty stderr, and the name key
        # that was asked for (trace_path, get_code_snippet).
        if p.returncode == 0 and not p.stderr.strip():
            return p.stdout
        key = MISS_IS_SILENT.get(tool)
        if (
            key
            and p.returncode == 1
            and not p.stdout.strip()
            and not p.stderr.strip()
            and args.get(key)
        ):
            return ""
        raise Unreachable(
            f"cbm {tool}: exit {p.returncode}"
            + (f", stderr: {p.stderr.strip()[:80]}" if p.stderr.strip() else "")
        )

    return run


def _git(root: str, *args: str) -> str:
    try:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", root, *args],
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
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
            str(
                cache
                or os.environ.get("ONESCRIPT_CBM_CACHE")
                or os.environ.get("CBM_CACHE_DIR")
                or DEFAULT_CACHE
            )
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
    dirty = bool(
        _git(root, "status", "--porcelain", "-z", "--untracked-files=normal").strip()
    )
    files = [
        p
        for p in _git(root, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
        if p
    ]
    when = [int(_git(root, "log", "-1", "--format=%ct", "HEAD").strip())]
    try:  # the last time HEAD moved, however it moved
        gd = _git(root, "reflog", "-1", "--date=unix", "--format=%gd", "HEAD").strip()
        when.append(int(gd.split("@{", 1)[1].rstrip("}")))  # HEAD@{<unix time>}
    except (Unreachable, ValueError, IndexError):
        pass
    try:  # the staging area: `git rm`, `git mv`, `git add -A` all rewrite it
        gi = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")
        staged = int(os.stat(gi.strip()).st_mtime)
    except (Unreachable, OSError):
        staged = 0
    return {
        "staged": staged,
        "head": head,
        "branch": branch,
        "dirty": dirty,
        "files": files,
        "moved": max(when),
    }


def _projects(runner: Runner) -> list[dict]:
    try:
        got = json.loads(runner("list_projects", {}))["projects"]
    except (ValueError, KeyError, TypeError) as e:
        raise Unreachable(f"cbm list_projects: unreadable ({type(e).__name__})") from e
    if not isinstance(got, list):
        raise Unreachable("cbm list_projects: unreadable (projects is not a list)")
    return [p for p in got if isinstance(p, dict)]


def _num(out: str, key: str) -> int | None:
    m = re.search(rf"^{key}:\s*(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else None


def _block(out: str, start: str, stop: tuple[str, ...], indented: bool = False):
    """Lines after the line opening `start:` up to a line opening a `stop:` key
    (or, if `indented`, up to the first line that is not indented)."""
    rows, inside = [], False
    for ln in out.splitlines():
        if not inside:
            inside = ln.startswith(start + ":")
            continue
        if ln.startswith(tuple(k + ":" for k in stop)) or (
            indented and ln.strip() and not ln.startswith(" ")
        ):
            break
        if ln.strip():
            rows.append(ln.rstrip())
    return rows


def parse(tool: str, out: str) -> tuple[int, list[str]]:
    """(total, rows) from `cbm <tool>`'s real output. Each tool has its own
    shape (observed against cbm on a throwaway repo, see tests/test_graph.py).
    An answer whose count cannot be found is `Unreachable`: empty is never
    guessed from output that did not say so."""
    text = out.strip()
    if text[:1] == "{":
        try:
            j = json.loads(text)
        except ValueError:
            raise Unreachable(f"cbm {tool}: unreadable JSON") from None
        if not isinstance(j, dict) or "error" in j:
            raise Unreachable(f"cbm {tool}: {str((j or {}).get('error', j))[:120]}")
        if tool == "get_code_snippet":
            return 1, [json.dumps(j, sort_keys=True)]
        raise Unreachable(f"cbm {tool}: unexpected JSON")
    if not text:
        if tool in ("trace_path", "get_code_snippet"):
            return 0, []  # cbm prints nothing for a name it does not hold
        raise Unreachable(f"cbm {tool}: no output; cannot tell empty from broken")
    if tool == "search_graph":
        total, rows = _num(text, "total"), _block(text, "results", ("has_more", "hint"))
    elif tool == "query_graph":
        total, rows = _num(text, "total"), _block(text, "rows", ("total", "hint"))
    elif tool == "trace_path":
        parts = [_num(text, k) for k in ("callers_total", "callees_total")]
        total = (
            sum(p for p in parts if p is not None)
            if any(p is not None for p in parts)
            else None
        )
        head = ("function", "direction", "callers_total", "callees_total")
        rows = [
            ln.rstrip()
            for ln in text.splitlines()
            if ln.strip()
            and not ln.startswith(tuple(h + ":" for h in head))
            and not re.match(r"^(callers|callees):\s*\d+", ln)
        ]
    elif tool == "search_code":
        total, rows = (
            _num(text, "total_results"),
            _block(text, "results", (), indented=True),
        )
    elif tool == "get_architecture":
        total, rows = (
            _num(text, "total_nodes"),
            [ln.rstrip() for ln in text.splitlines() if ln.strip()],
        )
    elif tool == "detect_changes":
        a, b = _num(text, "changed_files"), _num(text, "impacted_total")
        total = None if a is None else a + (b or 0)
        rows = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    else:
        raise Unreachable(f"cbm {tool}: no parser for this tool")
    if total is None:
        raise Unreachable(
            f"cbm {tool}: output has no count; cannot tell empty from broken"
        )
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
    need = MISS_IS_SILENT.get(tool)
    if need and not (args or {}).get(need):
        return _out("unreachable", f"refused: {tool} needs '{need}'", None)
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
            "dirty": f["dirty"],
            "indexed_at": _indexed_at(project, cache),
            "branch": f["branch"],
        }
        if f["moved"] > stamp["indexed_at"]:
            return _out(
                "stale",
                "HEAD moved after the index was made; re-index before asking",
                stamp,
            )
        if f["staged"] > stamp["indexed_at"]:
            return _out(
                "stale",
                "the staging area changed after the index was made (a path may have "
                "been removed, renamed or added); re-index before asking",
                stamp,
            )
        for rel in f["files"]:
            try:
                newer = (
                    int(os.lstat(os.path.join(root, rel)).st_mtime)
                    > stamp["indexed_at"]
                )
            except OSError:
                newer = True  # listed but gone: the index may still hold it
            if newer:
                return _out(
                    "stale",
                    f"'{rel}' changed after the index was made; re-index before asking",
                    stamp,
                )
        total, rows = parse(tool, run(tool, {**(args or {}), "project": project}))
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
