"""diverge.py -- the divergence finder: where two refs part, down to the smallest unit.

The operator, 2026-10-21: "I kinda wanna use the merge check and the one script
and whatevers to see if we can actually see where, deterministically, using the
system, what the smallest points of divergance we can isolate." Then: "build it".

    python -m onescript diverge A B [--repo NAME ...] [--repos DIR] [--json]
        [--record] [--behaviour] [--box-a BOX --box-b BOX]

Each level narrows the one before it to what BOTH sides changed against
``git merge-base A B`` (per repo):

  0 refs   A and B each resolve to a commit and have a merge base; no common base
           is said, and that repo is ``unreachable`` (never guessed from).
  1 files  changed on A only / B only / both (same bytes on both = convergent).
           Renames follow ``--find-renames`` and are keyed by their base path.
           Binary files, symlinks and submodules are compared by blob only.
  2 units  a Python file is cut into units: a top-level def / class / assignment /
           import, and each method or class attribute as ``Class.name``. A unit is
           hashed over ``ast.dump(node, include_attributes=False)``, so a comment, a
           blank line or a re-wrapped call is no change. A class's own unit is its
           shell without the methods, so editing one method flags ``C.m`` and not
           ``C``. Per unit: a_only / b_only / both_same (identical hash on both
           sides: convergent, reported, not a divergence) / both_different (a
           divergence point). A file that is not Python (or does not parse) falls
           back to unified-diff hunks (git's, ``-U0``, myers), keyed by base range
           and content hash; the smallest unit there is a hunk.
  3 deps   a unit changed on one side that REFERENCES a unit changed on the other.
           References resolve through the file's real import bindings (from-imports,
           ``import x as z``, module aliases, relative imports into the package, one
           hop of ``__init__`` re-export) and the file's own top-level names, never
           by a bare attribute name: matching ``.get`` / ``run`` / ``mark`` by name
           gave false edges that resolution through imports removed. A unit that is
           the same on both sides is not a target (merged, it is unchanged).
  4 behaviour  ``--behaviour`` only. A throwaway clone under a tmp dir (a shared-object
           clone, so the repo's own ``.git`` is never written), ``git merge
           --no-commit`` of B into A, never on a real branch. A conflict is a
           divergence point (its hunks are listed); with none, each side's changed
           tests run on the merged tree and pass/fail is said per side. This is
           merge_check's own pytest runner.

The minimal set is: level-2 both_different units + level-1 both-changed hunks that
differ (and whole files that cannot be cut: binary, rename/rename, modify/delete)
+ level-3 edges + level-4 conflicts and failures, with counts per level.

Three states, not collapsed: ``populated`` (a divergence point), ``empty`` (none),
``unreachable`` (a ref, a repo or a merge base is missing, said why). Any repo
unreachable makes the whole run unreachable; the other repos are still reported.
Exit: 0 empty, 1 populated, 2 unreachable.

Deterministic: every list is sorted, the output is ``canon.strict_sorted_compact_utf8``
bytes, paths are repo-relative (never an absolute path), and the only clock is the
record's. Same refs, same bytes, under any PYTHONHASHSEED. The one caveat is
``ast.dump``: unit hashes are those of the running interpreter's ast, so compare
hashes between runs on one Python minor version. ``--record`` appends one ``diverge``
row (through ``Record.append``, under the lock) with the refs, the bases and the
sha-256 of that canonical result; re-running yields the same hash.

``--box-a/--box-b`` compare two ``record.jsonl`` chains: ``verify_chain`` on each
first (a broken or missing chain is ``unreachable``, said), then the first row index
where the chains split (same prefix hashes, then different) and what each side wrote
after it (counts and kinds, in order).

Known limits, said once: units inside top-level ``if``/``try`` blocks are one unit per
statement (named ``if#0``, by order); inheritance is not followed for ``self.x``;
dynamic access (``getattr``, ``import_module``) is not a reference; two files that
land on the same path through different keys (an add beside a rename) surface at
level 4, not before; hunks adjacent but not overlapping in the base are not paired.

Stdlib only, apart from the kernel's own canon, record and xref.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import canon as _canon
from .xref import GitError, git

canon = _canon.strict_sorted_compact_utf8

TOOL = "diverge/v1"
GROUPS = ("a_only", "b_only", "both_same", "both_different")
_EMPTY_COUNTS = {
    **{f"files_{g}": 0 for g in GROUPS},
    **{f"units_{g}": 0 for g in GROUPS},
    **{f"hunks_{g}": 0 for g in GROUPS},
    "edges": 0,
    "conflicts": 0,
    "test_failures": 0,
    "minimal": 0,
}
_PLAIN_MODES = ("100644", "100755")


def h256(data: bytes | str) -> str:
    return hashlib.sha256(
        data if isinstance(data, bytes) else data.encode()
    ).hexdigest()


def _text(b: bytes) -> str:
    return b.decode("utf-8", "backslashreplace")


# -- git: refs, bases, changes -----------------------------------------------


def _resolve(repo: Path, ref: str) -> str | None:
    try:
        return (
            git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
            .decode()
            .strip()
        )
    except GitError:
        return None


def _merge_bases(repo: Path, a: str, b: str) -> list[str]:
    try:
        return sorted(git(repo, "merge-base", "--all", a, b).decode().split())
    except GitError:
        return []


class _Change:
    """One side's change to one file, keyed by its base path (or its new path
    when it is an add)."""

    __slots__ = ("status", "old", "new", "old_oid", "new_oid", "mode")

    def __init__(self, status, old, new, old_oid, new_oid, mode):
        self.status, self.old, self.new = status, old, new
        self.old_oid, self.new_oid, self.mode = old_oid, new_oid, mode

    @property
    def key(self) -> str:
        return self.old if self.old is not None else self.new


def _changes(repo: Path, base: str, tip: str) -> dict[str, _Change]:
    out = git(
        repo,
        "diff",
        "--raw",
        "-z",
        "--no-abbrev",
        "--find-renames",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        base,
        tip,
    )
    parts = out.split(b"\0")
    res: dict[str, _Change] = {}
    i = 0
    while i < len(parts) and parts[i]:
        omode, nmode, ooid, noid, status = parts[i].decode()[1:].split(" ")
        i += 1
        p1 = _text(parts[i])
        i += 1
        p2 = None
        if status[0] in "RC":
            p2 = _text(parts[i])
            i += 1
        s = status[0]
        zero = set(noid) == {"0"}
        if s == "R":
            ch = _Change("R", p1, p2, ooid, noid, nmode)
        elif s == "C":
            ch = _Change("A", None, p2, None, noid, nmode)
        elif s == "A":
            ch = _Change("A", None, p1, None, noid, nmode)
        elif s == "D":
            ch = _Change("D", p1, None, ooid, None, omode)
        else:  # M, T
            ch = _Change("M", p1, p1, ooid, None if zero else noid, nmode)
        res[ch.key] = ch
    return res


class _Blobs:
    """``git cat-file --batch``: many reads, one process."""

    def __init__(self, repo: Path):
        self.p = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )

    def get(self, spec: str) -> bytes | None:
        self.p.stdin.write(spec.encode("utf-8", "surrogateescape") + b"\n")
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) < 3 or head[1] == b"missing":
            return None
        size = int(head[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        return data

    def close(self) -> None:
        try:
            self.p.stdin.close()
        finally:
            self.p.wait()


# -- level 2: units -----------------------------------------------------------


def _put(out: dict, name: str, node, kind: str, digest: str | None = None) -> None:
    n, k = name, 1
    while n in out:
        k += 1
        n = f"{name}@{k}"
    out[n] = (node, digest or h256(ast.dump(node, include_attributes=False)), kind)


def _store_names(node) -> list[str]:
    """The plain names an assignment binds (tuple unpacking included)."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return []
    names: list[str] = []

    def walk(t):
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                walk(e)
        elif isinstance(t, ast.Starred):
            walk(t.value)

    for t in targets:
        walk(t)
    return names


def _collect(body, prefix: str, out: dict, module_level: bool) -> list:
    """Fill ``out`` with the units of ``body``; return the leftover statements
    (the class shell's, when ``module_level`` is false)."""
    rest: list = []
    ords: dict[str, int] = {}
    for idx, st in enumerate(body):
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _put(out, prefix + st.name, st, "def")
        elif isinstance(st, ast.ClassDef):
            q = prefix + st.name
            left = _collect(st.body, q + ".", out, False)
            shell = copy.copy(st)
            shell.body = left or [ast.Pass()]
            _put(out, q, shell, "class")
        elif _store_names(st):
            for nm in _store_names(st):
                _put(out, prefix + nm, st, "assign")
        elif module_level and isinstance(st, (ast.Import, ast.ImportFrom)):
            for al in st.names:
                nm = f"{'.' * getattr(st, 'level', 0)}{getattr(st, 'module', None) or ''}"
                bound = f"{al.name}{' as ' + al.asname if al.asname else ''}"
                name = (
                    f"from {nm} import {bound}"
                    if isinstance(st, ast.ImportFrom)
                    else f"import {bound}"
                )
                _put(out, name, st, "import", digest=h256("import"))
        elif module_level:
            if (
                idx == 0
                and isinstance(st, ast.Expr)
                and isinstance(st.value, ast.Constant)
                and isinstance(st.value.value, str)
            ):
                kind = "<docstring>"
                _put(out, kind, st, "stmt")
                continue
            kind = type(st).__name__.lower()
            k = ords.get(kind, 0)
            ords[kind] = k + 1
            _put(out, f"{kind}#{k}", st, "stmt")
        else:
            rest.append(st)
    return rest


class _Parsed:
    """One Python file at one commit: its units and its import bindings."""

    def __init__(self, path: str, mod: str | None, is_pkg: bool, tree):
        self.path, self.mod, self.is_pkg, self.tree = path, mod, is_pkg, tree
        self.units: dict[str, tuple] = {}
        _collect(tree.body, "", self.units, True)
        self.bindings: dict[str, tuple] = {}
        _bind(_scope_nodes(tree), mod, is_pkg, self.bindings)


def _scope_nodes(tree):
    """Every node in the module's own scope: not inside a def, class or lambda."""
    stack = list(reversed(tree.body))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(n))))


def _absolute(
    mod: str | None, is_pkg: bool, level: int, name: str | None
) -> str | None:
    if level == 0:
        return name
    if mod is None:
        return None
    pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
    drop = level - 1
    if drop > len(pkg) or (not pkg and level):
        return None
    pkg = pkg[: len(pkg) - drop]
    full = ".".join(pkg + ([name] if name else []))
    return full or None


def _bind(nodes, mod, is_pkg, into: dict) -> None:
    for n in nodes:
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.asname:
                    into[al.asname] = ("mod", al.name, "module-alias")
                else:
                    top = al.name.split(".")[0]
                    into[top] = ("mod", top, "import")
        elif isinstance(n, ast.ImportFrom):
            absmod = _absolute(mod, is_pkg, n.level, n.module)
            if absmod is None:
                continue
            via = "relative" if n.level else "from-import"
            for al in n.names:
                if al.name != "*":
                    into[al.asname or al.name] = ("from", absmod, al.name, via)


def _parse(data: bytes):
    try:
        return ast.parse(data)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


class _Side:
    """One commit's tree: what files exist, which module each is, parsed lazily."""

    def __init__(self, repo: Path, sha: str, blobs: _Blobs):
        self.sha, self.blobs = sha, blobs
        raw = git(repo, "ls-tree", "-r", "-z", "--name-only", "--full-tree", sha)
        self.raw = {_text(p): p for p in raw.split(b"\0") if p}
        self.paths = set(self.raw)
        self._modidx: dict[str, str] | None = None
        self._parsed: dict[str, _Parsed | None] = {}
        self.key_of: dict[str, str] = {}

    def read(self, path: str) -> bytes | None:
        if path not in self.raw:
            return None
        return self.blobs.get(
            self.sha + ":" + self.raw[path].decode("utf-8", "surrogateescape")
        )

    def module_of(self, path: str) -> tuple[str | None, bool]:
        parts = path.split("/")
        if not parts[-1].endswith(".py"):
            return None, False
        is_pkg = parts[-1] == "__init__.py"
        d = posixpath.dirname(path)
        pk: list[str] = []
        while d and f"{d}/__init__.py" in self.paths:
            pk.insert(0, posixpath.basename(d))
            d = posixpath.dirname(d)
        if is_pkg:
            return (".".join(pk) or None), True
        return ".".join(pk + [parts[-1][:-3]]), False

    @property
    def modidx(self) -> dict[str, str]:
        if self._modidx is None:
            idx: dict[str, str] = {}
            for p in sorted(self.paths):
                m, _ = self.module_of(p)
                if m and m not in idx:
                    idx[m] = p
            self._modidx = idx
        return self._modidx

    def parsed(self, path: str) -> _Parsed | None:
        if path not in self._parsed:
            data = self.read(path)
            tree = _parse(data) if data is not None else None
            if tree is None:
                self._parsed[path] = None
            else:
                mod, is_pkg = self.module_of(path)
                self._parsed[path] = _Parsed(path, mod, is_pkg, tree)
        return self._parsed[path]


def _unit_changes(base: _Parsed | None, side: _Parsed | None) -> dict[str, tuple]:
    """name -> (change, hash) for every unit that differs from the base."""
    bu = {k: v[1] for k, v in (base.units if base else {}).items()}
    su = {k: v[1] for k, v in (side.units if side else {}).items()}
    out = {}
    for name in set(bu) | set(su):
        if bu.get(name) == su.get(name):
            continue
        change = (
            "added" if name not in bu else "removed" if name not in su else "modified"
        )
        out[name] = (change, su.get(name))
    return out


# -- level 2 fallback: hunks ---------------------------------------------------


def _is_binary(data: bytes | None) -> bool:
    return data is not None and b"\0" in data[:8000]


def _hunks(repo: Path, old: tuple | None, new: tuple | None) -> list[dict]:
    """``old``/``new`` are (oid, bytes) or None. One hunk per ``@@``, in order."""
    if old is None or new is None:
        data = (new or old)[1]
        n = len(data.split(b"\n")) - (1 if data.endswith(b"\n") else 0) if data else 0
        lines = [_text(x) for x in data.split(b"\n")[:n]]
        if old is None:
            return [{"base": [0, 0], "removed": [], "added": lines}] if n else []
        return [{"base": [1, n], "removed": lines, "added": []}] if n else []
    out = git(
        repo,
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--diff-algorithm=myers",
        old[0],
        new[0],
    )
    hunks: list[dict] = []
    cur = None
    for line in out.split(b"\n"):
        if line.startswith(b"@@"):
            head = line.split(b" ")[1].lstrip(b"-").split(b",")
            s = int(head[0])
            c = int(head[1]) if len(head) > 1 else 1
            cur = {"base": [s, c], "removed": [], "added": []}
            hunks.append(cur)
        elif cur is not None and line[:1] == b"-":
            cur["removed"].append(_text(line[1:]))
        elif cur is not None and line[:1] == b"+":
            cur["added"].append(_text(line[1:]))
    return hunks


def _span(h: dict) -> tuple[float, float]:
    s, c = h["base"]
    return (s + 0.5, s + 0.5) if c == 0 else (float(s), float(s + c - 1))


def _hhash(h: dict) -> str:
    return h256(
        canon({"base": h["base"], "removed": h["removed"], "added": h["added"]})
    )


def _pair_hunks(ha: list[dict], hb: list[dict], path: str) -> dict:
    """Group the two sides' hunks of one file by what they do in the base."""
    out = {g: [] for g in GROUPS}
    used_b: set[int] = set()
    for a in ha:
        sa = _span(a)
        hit = False
        for j, b in enumerate(hb):
            sb = _span(b)
            if sa[0] <= sb[1] and sb[0] <= sa[1]:
                hit = True
                used_b.add(j)
                if (
                    a["base"] == b["base"]
                    and a["removed"] == b["removed"]
                    and a["added"] == b["added"]
                ):
                    out["both_same"].append(
                        {"file": path, "base": a["base"], "hash": _hhash(a)}
                    )
                else:
                    out["both_different"].append(
                        {
                            "file": path,
                            "base_a": a["base"],
                            "base_b": b["base"],
                            "a": _hhash(a),
                            "b": _hhash(b),
                        }
                    )
        if not hit:
            out["a_only"].append({"file": path, "base": a["base"], "hash": _hhash(a)})
    for j, b in enumerate(hb):
        if j not in used_b:
            out["b_only"].append({"file": path, "base": b["base"], "hash": _hhash(b)})
    return out


# -- level 3: references -------------------------------------------------------


def _chains(node) -> list[list[str]]:
    out: list[list[str]] = []

    def visit(n):
        if isinstance(n, ast.Attribute):
            parts, cur = [], n
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                out.append([cur.id] + parts[::-1])
            else:
                visit(cur)
            return
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load):
                out.append([n.id])
            return
        for c in ast.iter_child_nodes(n):
            visit(c)

    visit(node)
    return out


def _locals(node) -> set[str]:
    """Names a unit binds itself (they shadow module names) and its own imports."""
    names: set[str] = set()
    glob: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif n is not node and isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            glob.update(n.names)
    return names - glob


def _resolve_name(side: _Side, mod_name: str, name: str, depth: int = 0):
    """(path, unit) of top-level ``name`` in module ``mod_name``, following one
    chain of ``__init__`` re-exports; None when it is not a unit."""
    path = side.modidx.get(mod_name)
    if path is None or depth > 5:
        return None
    pf = side.parsed(path)
    if pf is None:
        return None
    u = pf.units.get(name)
    if u is not None and u[2] != "import":
        return path, name
    b = pf.bindings.get(name)
    if b and b[0] == "from":
        return _resolve_name(side, b[1], b[2], depth + 1)
    return None


def _refs(side: _Side, pf: _Parsed, uname: str):
    """Yield (target path, target unit, how) for each reference ``uname`` makes
    that resolves to a unit; a bare name or attribute that binds to nothing real
    yields nothing."""
    node, _, kind = pf.units[uname]
    shadow = _locals(node)
    local_binds: dict[str, tuple] = {}
    _bind(
        [n for n in ast.walk(node) if isinstance(n, (ast.Import, ast.ImportFrom))],
        pf.mod,
        pf.is_pkg,
        local_binds,
    )
    self_arg = None
    if kind == "def" and "." in uname and node.args.args:
        self_arg = node.args.args[0].arg
    owner = uname.rsplit(".", 1)[0] if "." in uname else None
    for chain in _chains(node):
        root, rest = chain[0], chain[1:]
        b = local_binds.get(root)
        if b is None and root in shadow and root != self_arg:
            continue
        if b is None and root == self_arg and rest and owner:
            tgt = f"{owner}.{rest[0]}"
            if tgt in pf.units:
                yield pf.path, tgt, "self"
            continue
        if b is None:
            b = pf.bindings.get(root)
        if b is None:
            u = pf.units.get(root)
            if u is None or u[2] == "import":
                continue
            cur = ("name", pf.path, root)
            via = "same-file"
        elif b[0] == "mod":
            cur, via = ("module", b[1]), b[2]
        else:
            _, m, n, via = b
            if f"{m}.{n}" in side.modidx:
                cur = ("module", f"{m}.{n}")
            else:
                hit = _resolve_name(side, m, n)
                cur = ("name", hit[0], hit[1]) if hit else None
        for part in rest:
            if cur is None:
                break
            if cur[0] == "module":
                nxt = f"{cur[1]}.{part}"
                if nxt in side.modidx:
                    cur = ("module", nxt)
                else:
                    hit = _resolve_name(side, cur[1], part)
                    cur = ("name", hit[0], hit[1]) if hit else None
            else:
                tpf = side.parsed(cur[1])
                member = f"{cur[2]}.{part}"
                if tpf is not None and member in tpf.units:
                    cur = ("name", cur[1], member)
                else:
                    break
        if cur is not None and cur[0] == "name":
            yield cur[1], cur[2], via


# -- one repo ------------------------------------------------------------------


def _unreachable(name: str, reason: str, **extra) -> dict:
    return {
        "state": "unreachable",
        "reason": reason,
        "counts": dict(_EMPTY_COUNTS),
        "minimal": [],
        **extra,
    }


def _entry(path: str, ch: _Change | None) -> dict | None:
    if ch is None:
        return None
    return {"status": ch.status, "path": ch.new if ch.new is not None else ch.old}


def analyze_repo(
    repo: Path, name: str, a_ref: str, b_ref: str, behaviour=False, runner=None
) -> dict:
    if not repo.is_dir():
        return _unreachable(name, f"repo {name!r} not found")
    try:
        git(repo, "rev-parse", "--git-dir")
    except GitError as e:
        return _unreachable(name, f"{name!r} is not a git repo: {e}")
    sha_a, sha_b = _resolve(repo, a_ref), _resolve(repo, b_ref)
    if sha_a is None or sha_b is None:
        miss = [r for r, s in ((a_ref, sha_a), (b_ref, sha_b)) if s is None]
        return _unreachable(
            name,
            f"no commit {miss[0]!r} in {name}"
            + (f" (nor {miss[1]!r})" if len(miss) > 1 else ""),
            a=sha_a,
            b=sha_b,
        )
    bases = _merge_bases(repo, sha_a, sha_b)
    if not bases:
        return _unreachable(
            name,
            f"{name}: {a_ref!r} and {b_ref!r} have no merge base (no common history)",
            a=sha_a,
            b=sha_b,
        )
    base = bases[0]
    ca, cb = _changes(repo, base, sha_a), _changes(repo, base, sha_b)
    files = {g: [] for g in GROUPS}
    units = {g: [] for g in GROUPS}
    hunks = {g: [] for g in GROUPS}
    minimal: list[dict] = []
    unparsed: list[dict] = []
    edges: list[dict] = []
    blobs = _Blobs(repo)
    try:
        sb, sa, sbb = (
            _Side(repo, base, blobs),
            _Side(repo, sha_a, blobs),
            _Side(repo, sha_b, blobs),
        )
        sa.key_of = {c.new: k for k, c in ca.items() if c.new}
        sbb.key_of = {c.new: k for k, c in cb.items() if c.new}
        changed: dict[str, dict[str, tuple]] = {
            "a": {},
            "b": {},
        }  # key -> unit -> (change, hash)
        parsed_files: dict[tuple, _Parsed] = {}
        for key in sorted(set(ca) | set(cb)):
            a, b = ca.get(key), cb.get(key)
            row = {"path": key, "a": _entry(key, a), "b": _entry(key, b)}
            row = {k: v for k, v in row.items() if v is not None}
            if a and b:
                same = a.new_oid == b.new_oid and a.new == b.new
                grp = "both_same" if same else "both_different"
            else:
                grp = "a_only" if a else "b_only"
            files[grp].append(row)
            if a and b and a.new is not None and b.new is not None and a.new != b.new:
                minimal.append(
                    {
                        "level": 1,
                        "kind": "path",
                        "repo": name,
                        "file": key,
                        "a": a.new,
                        "b": b.new,
                    }
                )
            paths = [p for p in (key, a and a.new, b and b.new) if p]
            is_py = all(p.endswith(".py") for p in paths)
            plain = all(
                (c is None or c.mode in _PLAIN_MODES or c.status == "D") for c in (a, b)
            )
            # -- level 2 on Python
            if is_py and plain:
                base_pf = sb.parsed(key) if key in sb.paths else None
                bad = []
                if key in sb.paths and base_pf is None:
                    bad.append("base")
                side_pf = {}
                for tag, ch, sd in (("a", a, sa), ("b", b, sbb)):
                    if ch is None:
                        continue
                    if ch.new is None:
                        side_pf[tag] = None
                        continue
                    pf = sd.parsed(ch.new)
                    if pf is None:
                        bad.append(tag)
                    side_pf[tag] = pf
                if not bad:
                    for tag in side_pf:
                        changed[tag][key] = _unit_changes(base_pf, side_pf[tag])
                        parsed_files[(tag, key)] = side_pf[tag]
                    for uname in sorted(
                        set(changed["a"].get(key, {})) | set(changed["b"].get(key, {}))
                    ):
                        ua, ub = (
                            changed["a"].get(key, {}).get(uname),
                            changed["b"].get(key, {}).get(uname),
                        )
                        if ua and ub:
                            if ua[1] == ub[1]:
                                units["both_same"].append(
                                    {"file": key, "unit": uname, "hash": ua[1]}
                                )
                            else:
                                units["both_different"].append(
                                    {"file": key, "unit": uname, "a": ua[1], "b": ub[1]}
                                )
                                minimal.append(
                                    {
                                        "level": 2,
                                        "kind": "unit",
                                        "repo": name,
                                        "file": key,
                                        "unit": uname,
                                    }
                                )
                        elif ua:
                            units["a_only"].append(
                                {"file": key, "unit": uname, "change": ua[0]}
                            )
                        else:
                            units["b_only"].append(
                                {"file": key, "unit": uname, "change": ub[0]}
                            )
                    continue
                unparsed.append(
                    {"file": key, "why": "does not parse at " + ",".join(sorted(bad))}
                )
            # -- file-level / hunks for the rest
            if not (a and b) or grp == "both_same":
                continue
            if a.new is not None and b.new is not None and a.new != b.new:
                continue  # said above as a path point
            old = None
            if key in sb.paths:
                od = sb.read(key)
                old = (a.old_oid, od) if od is not None else None
            ad = sa.read(a.new) if a.new else None
            bd = sbb.read(b.new) if b.new else None
            binary = (
                not (a.mode in _PLAIN_MODES or a.status == "D")
                or not (b.mode in _PLAIN_MODES or b.status == "D")
                or _is_binary(ad)
                or _is_binary(bd)
                or _is_binary(old[1] if old else None)
            )
            if binary or (a.status == "D") != (b.status == "D"):
                minimal.append(
                    {
                        "level": 1,
                        "kind": "file",
                        "repo": name,
                        "file": key,
                        "why": "binary or non-text: blob only"
                        if binary
                        else "modify/delete",
                    }
                )
                continue
            newa = (a.new_oid, ad) if ad is not None else None
            newb = (b.new_oid, bd) if bd is not None else None
            paired = _pair_hunks(_hunks(repo, old, newa), _hunks(repo, old, newb), key)
            for g in GROUPS:
                hunks[g].extend(paired[g])
            for h in paired["both_different"]:
                minimal.append(
                    {
                        "level": 1,
                        "kind": "hunk",
                        "repo": name,
                        "file": key,
                        "base_a": h["base_a"],
                        "base_b": h["base_b"],
                        "a": h["a"],
                        "b": h["b"],
                    }
                )
        # -- level 3
        eff = {
            t: {(k, u) for k, d in changed[t].items() for u, (_, _h) in d.items()}
            for t in "ab"
        }
        same = {(u["file"], u["unit"]) for u in units["both_same"]}
        eff = {t: s - same for t, s in eff.items()}
        seen: dict[tuple, str] = {}
        for tag, other, sd in (("a", "b", sa), ("b", "a", sbb)):
            for key, uname in sorted(eff[tag]):
                pf = parsed_files.get((tag, key))
                if pf is None or uname not in pf.units:
                    continue
                for tpath, tunit, via in _refs(sd, pf, uname):
                    tkey = sd.key_of.get(tpath, tpath)
                    if (tkey, tunit) == (key, uname) or (tkey, tunit) not in eff[other]:
                        continue
                    k = (tag, key, uname, other, tkey, tunit)
                    seen[k] = min(via, seen.get(k, via))
        for (tag, key, uname, other, tkey, tunit), via in sorted(seen.items()):
            edges.append(
                {
                    "from": {"side": tag, "file": key, "unit": uname},
                    "to": {"side": other, "file": tkey, "unit": tunit},
                    "via": via,
                }
            )
            minimal.append(
                {
                    "level": 3,
                    "kind": "edge",
                    "repo": name,
                    "from": f"{tag}:{key}::{uname}",
                    "to": f"{other}:{tkey}::{tunit}",
                    "via": via,
                }
            )
    finally:
        blobs.close()
    for g in GROUPS:
        units[g].sort(key=lambda x: (x["file"], x["unit"]))
        hunks[g].sort(
            key=lambda x: (x["file"], x["base"] if "base" in x else x["base_a"])
        )
    res = {
        "a": sha_a,
        "b": sha_b,
        "base": base,
        "bases": len(bases),
        "files": files,
        "units": units,
        "hunks": hunks,
        "edges": edges,
        "unparsed": sorted(unparsed, key=lambda x: x["file"]),
    }
    if behaviour:
        beh = _behaviour(repo, name, sha_a, sha_b, ca, cb, runner)
        res["behaviour"] = beh
        for c in beh["conflicts"]:
            minimal.append({"level": 4, "kind": "conflict", "repo": name, **c})
        for side in ("a", "b"):
            if beh["tests"][side]["state"] == "fail":
                minimal.append(
                    {
                        "level": 4,
                        "kind": "test-failure",
                        "repo": name,
                        "side": side,
                        "detail": beh["tests"][side]["detail"],
                    }
                )
    minimal.sort(key=lambda m: canon(m))
    counts = {
        **{f"files_{g}": len(files[g]) for g in GROUPS},
        **{f"units_{g}": len(units[g]) for g in GROUPS},
        **{f"hunks_{g}": len(hunks[g]) for g in GROUPS},
        "edges": len(edges),
        "conflicts": len(res.get("behaviour", {}).get("conflicts", [])),
        "test_failures": sum(
            1
            for s in ("a", "b")
            if res.get("behaviour", {}).get("tests", {}).get(s, {}).get("state")
            == "fail"
        ),
        "minimal": len(minimal),
    }
    res.update(
        state="populated" if minimal else "empty", counts=counts, minimal=minimal
    )
    return res


# -- level 4: behaviour --------------------------------------------------------


def _is_test(path: str) -> bool:
    b = posixpath.basename(path)
    return b.endswith(".py") and (b.startswith("test_") or b.endswith("_test.py"))


def _conflicts(wt: Path) -> list[dict]:
    out = []
    unmerged = git(wt, "ls-files", "-u", "-z").split(b"\0")
    stages: dict[str, set[int]] = {}
    for rec in filter(None, unmerged):
        meta, path = rec.split(b"\t", 1)
        stages.setdefault(_text(path), set()).add(int(meta.split()[2]))
    for path in sorted(stages):
        try:
            lines = (wt / path).read_bytes().split(b"\n")
        except OSError:
            lines = []
        found = False
        start = None
        for i, ln in enumerate(lines, 1):
            if ln.startswith(b"<<<<<<< "):
                start = i
            elif ln.startswith(b">>>>>>> ") and start is not None:
                found = True
                out.append(
                    {
                        "file": path,
                        "lines": [start, i],
                        "hash": h256(b"\n".join(lines[start - 1 : i])),
                    }
                )
                start = None
        if not found:
            out.append(
                {
                    "file": path,
                    "lines": [],
                    "hash": "",
                    "why": "stages " + ",".join(map(str, sorted(stages[path]))),
                }
            )
    return out


def _behaviour(
    repo: Path, name: str, sha_a: str, sha_b: str, ca, cb, runner=None
) -> dict:
    """Merge B into A in a throwaway clone; list conflicts; else run each side's
    changed tests on the merged tree."""
    tmp = Path(tempfile.mkdtemp(prefix="diverge-"))
    wt = tmp / "w"
    res = {
        "conflicts": [],
        "merge": "",
        "tests": {
            "a": {"state": "empty", "tests": [], "detail": "no changed tests"},
            "b": {"state": "empty", "tests": [], "detail": "no changed tests"},
        },
    }
    try:
        env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
        for cmd in (
            ["git", "clone", "-q", "--shared", "--no-checkout", str(repo), str(wt)],
            ["git", "-C", str(wt), "checkout", "-q", "--detach", sha_a],
        ):
            p = subprocess.run(cmd, capture_output=True, env=env, check=False)
            if p.returncode:
                res["merge"] = "setup failed: " + _text(p.stderr).strip()[:200]
                res["tests"] = {
                    s: {"state": "unreachable", "tests": [], "detail": res["merge"]}
                    for s in "ab"
                }
                return res
        p = subprocess.run(
            [
                "git",
                "-C",
                str(wt),
                "-c",
                "user.name=diverge",
                "-c",
                "user.email=diverge@localhost",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "merge",
                "--no-commit",
                "--no-ff",
                "-q",
                sha_b,
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        if p.returncode:
            res["conflicts"] = _conflicts(wt)
            res["merge"] = "conflict"
            for s in "ab":
                res["tests"][s] = {
                    "state": "empty",
                    "tests": [],
                    "detail": "not run: the merge conflicts",
                }
            if not res["conflicts"]:
                res["merge"] = "failed: " + _text(p.stderr + p.stdout).strip()[:200]
            return res
        res["merge"] = "clean"
        from . import merge_check as mc

        pl = mc.Places(repo.parent, repo.parent / "willow-bot" / "one-script")
        env2 = dict(os.environ)
        env2.pop("PYTHONHASHSEED", None)
        env2["PYTHONDONTWRITEBYTECODE"] = "1"
        roots = [str(wt)]
        for d in sorted(wt.iterdir()):
            if (
                d.is_dir()
                and not d.name.startswith(".")
                and any((c / "__init__.py").exists() for c in sorted(d.iterdir()) if c.is_dir())
            ):
                roots.append(str(d))
        if (wt / "src").is_dir():
            roots.append(str(wt / "src"))
        if env2.get("PYTHONPATH"):
            roots.append(env2["PYTHONPATH"])
        env2["PYTHONPATH"] = os.pathsep.join(roots)
        env2["ONESCRIPT_HOME"] = (
            str(wt / "one-script")
            if (wt / "one-script").is_dir()
            else env2.get("ONESCRIPT_HOME", "")
        )
        grove = repo.parent / "willows-grove"
        if grove.is_dir():
            env2["ONESCRIPT_GROVE"] = str(grove)
        for s, ch in (("a", ca), ("b", cb)):
            tests = sorted(
                {
                    c.new
                    for c in ch.values()
                    if c.new and _is_test(c.new) and (wt / c.new).is_file()
                }
            )
            if not tests:
                continue
            run = runner or mc._pytest
            ok, detail = run(pl.py(name), str(wt), env2, tests, False)
            res["tests"][s] = {
                "state": "pass" if ok else "fail",
                "tests": tests,
                "detail": detail[:300],
            }
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -- boxes ---------------------------------------------------------------------


def _chain(box: Path):
    """(rows, None) for a box whose chain verifies; (None, why) otherwise."""
    from .record import Record

    if not (box / "record.jsonl").is_file():
        return None, f"no record.jsonl in {box.name!r}"
    rec = Record(box, lambda: "", "", object())
    breaks = rec.verify_chain()
    if breaks:
        return None, f"chain of {box.name!r} is broken: {breaks[0]}"
    return rec.rows(), None


def compare_boxes(box_a: Path, box_b: Path) -> dict:
    ra, wa = _chain(Path(box_a))
    rb, wb = _chain(Path(box_b))
    if ra is None or rb is None:
        return {"state": "unreachable", "reason": "; ".join(w for w in (wa, wb) if w)}
    n = 0
    while n < min(len(ra), len(rb)) and ra[n]["hash"] == rb[n]["hash"]:
        n += 1
    tail_a, tail_b = ra[n:], rb[n:]

    def side(t):
        kinds = [r["kind"] for r in t]
        return {
            "rows_after": len(t),
            "kinds": kinds,
            "counts": {k: kinds.count(k) for k in sorted(set(kinds))},
        }

    res = {"common_rows": n, "a": side(tail_a), "b": side(tail_b)}
    if not tail_a and not tail_b:
        return {"state": "empty", "split_at": None, **res}
    return {
        "state": "populated",
        "split_at": n,
        "relation": "extends" if not tail_a or not tail_b else "forked",
        **res,
    }


# -- the run -------------------------------------------------------------------


def run(
    repos_dir: Path, names: list[str], a: str, b: str, behaviour=False, runner=None
) -> dict:
    repos = {}
    for nm in sorted(set(names)):
        ok = nm and "/" not in nm and "\\" not in nm and nm not in (".", "..")
        repos[nm] = (
            analyze_repo(Path(repos_dir) / nm, nm, a, b, behaviour, runner)
            if ok
            else _unreachable(nm, f"repo name {nm!r} is not a plain name")
        )
    totals = dict(_EMPTY_COUNTS)
    for r in repos.values():
        for k, v in r["counts"].items():
            totals[k] += v
    minimal = sorted(
        (m for r in repos.values() for m in r["minimal"]), key=lambda m: canon(m)
    )
    states = {r["state"] for r in repos.values()}
    state = (
        "unreachable"
        if "unreachable" in states or not repos
        else "populated"
        if minimal
        else "empty"
    )
    out = {
        "tool": TOOL,
        "state": state,
        "refs": {"a": a, "b": b},
        "repos": repos,
        "minimal": minimal,
        "counts": totals,
    }
    if state == "unreachable":
        out["reason"] = (
            "; ".join(
                f"{n}: {r['reason']}"
                for n, r in sorted(repos.items())
                if r["state"] == "unreachable"
            )
            or "no repo named"
        )
    return out


def result_hash(result: dict) -> str:
    return h256(canon.bytes(result))


def render(result: dict, rhash: str) -> str:
    c = result["counts"]
    lines = [
        f"diverge {result['refs']['a']} {result['refs']['b']}  state: {result['state']}"
    ]
    if "reason" in result:
        lines.append(f"  unreachable: {result['reason']}")
    for nm, r in result["repos"].items():
        if r["state"] == "unreachable":
            lines.append(f"  {nm}: unreachable ({r['reason']})")
            continue
        k = r["counts"]
        lines.append(
            f"  {nm}: base {r['base'][:7]}  a {r['a'][:7]}  b {r['b'][:7]}  [{r['state']}]"
        )
        lines.append(
            f"    files  a-only {k['files_a_only']}  b-only {k['files_b_only']}  "
            f"both {k['files_both_same'] + k['files_both_different']} "
            f"(same {k['files_both_same']}, different {k['files_both_different']})"
        )
        lines.append(
            f"    units  a-only {k['units_a_only']}  b-only {k['units_b_only']}  "
            f"both-same {k['units_both_same']}  both-different {k['units_both_different']}"
        )
        lines.append(
            f"    hunks  both-same {k['hunks_both_same']}  both-different {k['hunks_both_different']}"
        )
        lines.append(f"    dependency edges {k['edges']}")
        if "behaviour" in r:
            bh = r["behaviour"]
            lines.append(
                f"    behaviour: merge {bh['merge']}, conflicts {k['conflicts']}, "
                f"tests a {bh['tests']['a']['state']} b {bh['tests']['b']['state']}"
            )
    lines.append(f"  minimal set: {c['minimal']} point(s)")
    for m in result["minimal"]:
        where = (
            m.get("unit")
            and f"{m['file']}::{m['unit']}"
            or m.get("file")
            or f"{m.get('from')} -> {m.get('to')}"
        )
        lines.append(f"    L{m['level']} {m['kind']:11} {m['repo']}: {where}")
    lines.append(f"  result sha256: {rhash}")
    return "\n".join(lines) + "\n"


def render_boxes(res: dict) -> str:
    if res["state"] == "unreachable":
        return f"boxes: unreachable ({res['reason']})\n"
    if res["state"] == "empty":
        return f"boxes: empty (identical, {res['common_rows']} rows)\n"
    return (
        f"boxes: populated, split at row {res['split_at']} ({res['relation']}); "
        f"a +{res['a']['rows_after']} {res['a']['counts']}, b +{res['b']['rows_after']} {res['b']['counts']}\n"
    )


def _record(box: Path, result: dict, rhash: str, clock=None) -> tuple[dict | None, str]:
    from . import api, gate, record as rec_mod

    rec = rec_mod.Record(
        box,
        clock or api._now,
        rec_mod.version_of(Path(__file__).resolve().parent),
        gate.token(),
    )
    breaks = rec.verify_chain()
    if breaks:
        return (
            None,
            f"refused: the box's chain is broken ({breaks[0]}); nothing written",
        )
    row = rec.append(
        "diverge",
        gate.system(),
        refs=result["refs"],
        state=result["state"],
        result_hash=rhash,
        repos={
            n: {"a": r.get("a"), "b": r.get("b"), "base": r.get("base")}
            for n, r in sorted(result["repos"].items())
        },
    )
    return row, f"recorded row {row['n']}"


def run_cli(
    a,
    b,
    *,
    repos=None,
    names=None,
    as_json=False,
    record=False,
    box=None,
    behaviour=False,
    box_a=None,
    box_b=None,
    clock=None,
    out=None,
    err=None,
) -> int:
    out = out or sys.stdout.write
    err = err or sys.stderr.write
    if (box_a is None) != (box_b is None):
        err("refused: --box-a and --box-b come together\n")
        return 2
    if (a is None) != (b is None):
        err("refused: two refs, A and B\n")
        return 2
    if a is None and box_a is None:
        err("refused: give two refs, or --box-a and --box-b\n")
        return 2
    if repos:
        rdir = Path(repos).expanduser().resolve()
    else:
        from . import merge_check as mc

        rdir = mc.default_places().repos
    payload: dict = {}
    rc = 0
    result = rhash = None
    if a is not None:
        result = run(rdir, list(names or ["willow-bot"]), a, b, behaviour)
        rhash = result_hash(result)
        payload.update(result)
        rc = {"empty": 0, "populated": 1, "unreachable": 2}[result["state"]]
    if box_a is not None:
        bx = compare_boxes(Path(box_a), Path(box_b))
        payload["boxes"] = bx
        rc = max(rc, {"empty": 0, "populated": 1, "unreachable": 2}[bx["state"]])
    if as_json:
        out(canon.bytes(payload).decode("utf-8") + "\n")
    else:
        if result is not None:
            out(render(result, rhash))
        if "boxes" in payload:
            out(render_boxes(payload["boxes"]))
    if record and result is not None:
        if box is None:
            err("refused: --record needs a box (--box)\n")
            return 2
        row, why = _record(Path(box), result, rhash, clock)
        err(why + "\n")
        if row is None:
            return 2
    return rc
