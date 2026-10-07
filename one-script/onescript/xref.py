"""xref — every file arrives with two hashes, and every reference is checked.

Cites: CONST-VI (the record: append and read), INVARIANTS §1 (three states).

Home: here, in onescript (operator, 2026-10-06), because the rows go through
`record.py`. Serve's home under D2 is willow-bot
(https://github.com/willow-memory/willow-bot); when serve lands there it reads
the index this writes. It does not rebuild it.

Four steps, no model reading anything:

  arrive   each tracked file at a pinned commit gets git's blob id (what GitHub
           holds), the same id recomputed here from the bytes on disk, whether
           the two match, and the system's own full SHA-256. A mismatch is
           recorded and the file is left out of the index.
  extract  ids (Q, D, OW, F, CONST, INVARIANTS §, PR #), markdown links,
           file:line citations and commit SHAs, cached by the file's SHA-256 so
           an unchanged file is never scanned twice.
  check    a link to a path that isn't there, a line past the end of its file, a
           pinned citation whose file changed, an id cited and never defined, an
           id defined twice in one repo. A SHA found in none of the repos is
           external, not broken.
  record   one write per output through the record, so each carries a pointer
           and a chained row; every row is unattested until the human seals.

The same repos at the same commits give the same index, byte for byte. The
only clock is the record's.

    python -m onescript.xref index --repo ../willows-grove --repo ../willow-bot \\
        --repo ../willow-mcp --out /tmp/xref [--cache DIR] [--record BOX]
    python -m onescript.xref slice --index /tmp/xref/index.json --id Q9 \\
        --repo ../willows-grove ... --out q9.txt

The slice is for people. Handing it to the model is Q19's question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

TEXT_SUFFIXES = (".md", ".py")
MAX_BYTES = 5_000_000
EXTRACT_VERSION = 1  # bump when extract() changes, so the cache misses
FAMILIES = ("Q", "D", "OW", "F", "CONST", "INV", "PR")


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ── git ──────────────────────────────────────────────────────────────────────
class GitError(Exception):
    pass


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError as e:
        raise GitError(str(e)) from e
    if p.returncode != 0:
        raise GitError(p.stderr.decode("utf-8", "replace").strip())
    return p.stdout


def blob_id(data: bytes, fmt: str = "sha1") -> str:
    """Git's blob id, computed here rather than asked of git."""
    return hashlib.new(fmt, b"blob %d\0" % len(data) + data).hexdigest()


# ── 1 arrive ─────────────────────────────────────────────────────────────────
def arrive(repo: Path, name: str, rev: str = "HEAD") -> dict:
    """One repo at one commit. state is populated, empty or unreachable."""
    head = {"repo": name, "rev": rev}
    try:
        git(repo, "rev-parse", "--git-dir")
    except GitError as e:
        return {**head, "state": "unreachable", "reason": f"not a git repo: {e}"}
    try:
        commit = git(repo, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
        commit = commit.decode().strip()
    except GitError:
        has_any = git(repo, "rev-list", "-n1", "--all").strip()
        if not has_any:
            return {**head, "state": "empty", "reason": "no commits"}
        return {**head, "state": "unreachable", "reason": f"no commit {rev!r}"}
    fmt = git(repo, "rev-parse", "--show-object-format").decode().strip() or "sha1"
    remote = bool(git(repo, "branch", "-r", "--contains", commit).strip())
    root_tree = git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    files, trees = [], [{"path": "", "git_tree": root_tree}]
    out = git(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", commit)
    for entry in filter(None, out.split(b"\0")):
        meta, path_b = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = path_b.decode("utf-8", "surrogateescape")
        if kind == "tree":
            trees.append({"path": path, "git_tree": oid})
            continue
        row = {"path": path, "mode": mode, "git_blob": oid}
        if kind == "commit":
            files.append({**row, "state": "submodule"})
            continue
        disk = repo / path
        try:
            data = (
                os.readlink(disk).encode("utf-8", "surrogateescape")
                if mode == "120000"
                else disk.read_bytes()
            )
        except OSError:
            files.append({**row, "state": "missing", "blob_match": False})
            continue
        local = blob_id(data, fmt)
        files.append(
            {
                **row,
                "state": "present",
                "local_blob": local,
                "blob_match": local == oid,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    files.sort(key=lambda r: r["path"])
    trees.sort(key=lambda r: r["path"])
    return {
        **head,
        "state": "populated",
        "commit": commit,
        "object_format": fmt,
        "on_remote": remote,
        "files": files,
        "trees": trees,
    }


# ── 2 extract ────────────────────────────────────────────────────────────────
_ID = r"(?<![A-Za-z0-9_\-])(OW|Q|D|F)([1-9]\d{0,2})(?![A-Za-z0-9_])"
RE_ID = re.compile(_ID)
RE_ID_DEF_ROW = re.compile(r"^\|\s*\**\s*" + _ID + r"\s*\**\s*\|")
RE_ID_DEF_HEAD = re.compile(r"^#{1,6}\s+\**\s*" + _ID)
RE_CONST = re.compile(r"\bCONST-([IVXLC]+)\b")
RE_INV = re.compile(r"INVARIANTS(?:\.md)?\s*§\s*(\d+)")
RE_INV_DEF = re.compile(r"^#{1,6}\s+§(\d+)\b")
RE_PR = re.compile(r"\bPR\s*#?(\d{1,5})\b")
RE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_EXT = r"py|md|mdx|ts|tsx|js|rs|go|json|toml|ya?ml|sh|html|sql|txt"
RE_FILELINE = re.compile(
    r"(?<![\w/.\-])((?:[\w.\-]+/)*[\w.\-]+\.(?:" + _EXT + r")):(\d+)"
    r"(?:[-–](\d+))?(?:@([0-9a-f]{7,40}))?(?![\w])"
)
RE_SHA = re.compile(r"(?<![0-9A-Za-z_/@#.\-])([0-9a-f]{7,40})(?![0-9A-Za-z_])")
RE_GH = re.compile(
    r"^https?://github\.com/([\w.\-]+)/([\w.\-]+)/(blob|tree|commit)/([^/#?\s]+)"
    r"(?:/([^#?\s]*))?(?:#L(\d+)(?:-L(\d+))?)?"
)
RE_ANCHOR_L = re.compile(r"^L(\d+)(?:-L(\d+))?$")


def _lines(text: str, md: bool):
    """(lineno, line), skipping fenced code in markdown."""
    fence = None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if md and (s.startswith("```") or s.startswith("~~~")):
            mark = s[:3]
            fence = None if fence == mark else (fence or mark)
            continue
        if fence is None:
            yield n, line


def extract(data: bytes, suffix: str, is_invariants: bool) -> dict:
    """Raw references in one file. Depends on its bytes alone, so it caches.
    Q/D/OW/F ids are read from markdown only: in code they collide with lint
    codes (F401) and the like."""
    text = data.decode("utf-8", "replace")
    md = suffix == ".md"
    defs, refs = [], []
    for n, line in _lines(text, md):
        defined = set()
        if md:
            for rx in (RE_ID_DEF_ROW, RE_ID_DEF_HEAD):
                m = rx.match(line)
                if m:
                    key = m.group(1) + m.group(2)
                    defs.append({"family": m.group(1), "id": key, "line": n})
                    defined.add(key)
            if line.lstrip().startswith("#"):
                for m in RE_CONST.finditer(line):
                    key = "CONST-" + m.group(1)
                    defs.append({"family": "CONST", "id": key, "line": n})
                    defined.add(key)
            if is_invariants:
                m = RE_INV_DEF.match(line)
                if m:
                    key = "§" + m.group(1)
                    defs.append({"family": "INV", "id": key, "line": n})
                    defined.add(key)
        for m in RE_ID.finditer(line) if md else ():
            key = m.group(1) + m.group(2)
            if key not in defined:
                refs.append({"kind": "id", "family": m.group(1), "key": key, "line": n})
        for m in RE_CONST.finditer(line):
            key = "CONST-" + m.group(1)
            if key not in defined:
                refs.append({"kind": "id", "family": "CONST", "key": key, "line": n})
        for m in RE_INV.finditer(line):
            refs.append(
                {"kind": "id", "family": "INV", "key": "§" + m.group(1), "line": n}
            )
        for m in RE_PR.finditer(line):
            refs.append({"kind": "pr", "family": "PR", "key": m.group(1), "line": n})
        if md:
            for m in RE_LINK.finditer(line):
                refs.append({"kind": "link", "key": m.group(1), "line": n})
        for m in RE_FILELINE.finditer(line):
            refs.append(
                {
                    "kind": "fileline",
                    "key": m.group(1),
                    "start": int(m.group(2)),
                    "end": int(m.group(3) or m.group(2)),
                    "pin": m.group(4),
                    "line": n,
                }
            )
        for m in RE_SHA.finditer(line):
            tok = m.group(1)
            if re.search(r"\d", tok) and re.search(r"[a-f]", tok):
                refs.append({"kind": "sha", "key": tok, "line": n})
    return {"defs": defs, "refs": refs}


def _cached_extract(cache: Path | None, sha: str, data: bytes, suffix, inv) -> dict:
    tag = f"v{EXTRACT_VERSION}-{sha}{suffix}{'.inv' if inv else ''}.json"
    if cache is not None:
        hit = cache / tag
        if hit.exists():
            return json.loads(hit.read_text(encoding="utf-8"))
    out = extract(data, suffix, inv)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / tag).write_text(canon(out), encoding="utf-8")
    return out


# ── 3 check ──────────────────────────────────────────────────────────────────
def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _resolve_sha(repos: dict, tokens: set) -> dict:
    """token -> sorted list of (repo, full sha); 'ambiguous' marks a short clash."""
    found: dict = {t: [] for t in tokens}
    if not tokens:
        return found
    order = sorted(tokens)
    for name, r in sorted(repos.items()):
        if r["arrival"]["state"] != "populated":
            continue
        req = "".join(f"{t}^{{commit}}\n" for t in order).encode()
        out = git(r["path"], "cat-file", "--batch-check", stdin=req).decode()
        for tok, res in zip(order, out.splitlines()):
            parts = res.split()
            if len(parts) >= 2 and parts[1] == "commit":
                found[tok].append([name, parts[0]])
            elif "ambiguous" in res:
                found[tok].append([name, "ambiguous"])
    return found


def _merged_prs(repo: Path) -> set:
    subjects = git(repo, "log", "--all", "--format=%s").decode("utf-8", "replace")
    nums = re.findall(r"\(#(\d+)\)|Merge pull request #(\d+)", subjects)
    return {a or b for a, b in nums}


def build(repos_in: list, rev: str = "HEAD", cache: Path | None = None) -> dict:
    """repos_in: [(name, path)]. Returns the index; deterministic for fixed inputs."""
    repos = {}
    for name, path in repos_in:
        repos[name] = {"path": Path(path), "arrival": arrive(Path(path), name, rev)}

    blobs, trees, data_of = {}, {}, {}
    for name, r in repos.items():
        a = r["arrival"]
        if a["state"] != "populated":
            continue
        blobs[name] = {f["path"]: f for f in a["files"]}
        trees[name] = {t["path"] for t in a["trees"]}
        for f in a["files"]:
            if (
                f.get("blob_match")
                and f["mode"] != "120000"
                and f["bytes"] <= MAX_BYTES
            ):
                data = (r["path"] / f["path"]).read_bytes()
                # Changed since it was hashed: index nothing it didn't hash.
                if hashlib.sha256(data).hexdigest() == f["sha256"]:
                    data_of[(name, f["path"])] = data

    defs, raw = [], []
    for (name, path), data in sorted(data_of.items()):
        if not path.endswith(TEXT_SUFFIXES):
            continue
        suffix = posixpath.splitext(path)[1]
        inv = posixpath.basename(path) == "INVARIANTS.md"
        sha = blobs[name][path]["sha256"]
        ex = _cached_extract(cache, sha, data, suffix, inv)
        defs += [{**d, "repo": name, "path": path} for d in ex["defs"]]
        raw += [{**x, "repo": name, "path": path} for x in ex["refs"]]

    defined: dict = {}
    for d in defs:
        defined.setdefault(d["id"], {}).setdefault(d["repo"], []).append(d)
    tokens = {x["key"] for x in raw if x["kind"] == "sha"}
    for x in raw:
        m = RE_GH.match(x["key"]) if x["kind"] == "link" else None
        if m and m.group(3) == "commit" and re.fullmatch(r"[0-9a-f]{7,40}", m.group(4)):
            tokens.add(m.group(4))
    shas = _resolve_sha(repos, tokens)
    prs = {
        n: _merged_prs(r["path"])
        for n, r in repos.items()
        if r["arrival"]["state"] == "populated"
    }

    refs = [_check(x, defined, shas, prs, blobs, trees, data_of) for x in raw]
    dupes = []
    for key, by_repo in sorted(defined.items()):
        for name, ds in sorted(by_repo.items()):
            if len(ds) > 1:
                dupes.append(
                    {
                        "kind": "id-defined-twice",
                        "repo": name,
                        "key": key,
                        "status": "broken",
                        "at": sorted(f"{d['path']}:{d['line']}" for d in ds),
                    }
                )

    refs.sort(key=lambda x: (x["repo"], x["path"], x["line"], x["kind"], x["key"]))
    defs.sort(key=lambda d: (d["repo"], d["path"], d["line"], d["id"]))
    inputs = []
    for name, r in sorted(repos.items()):
        a = r["arrival"]
        inputs.append({k: v for k, v in a.items() if k not in ("files", "trees")})
    return {
        "inputs": inputs,
        "arrival": {
            n: {
                "files": r["arrival"].get("files", []),
                "trees": r["arrival"].get("trees", []),
            }
            for n, r in sorted(repos.items())
        },
        "defs": defs,
        "refs": refs,
        "dupes": dupes,
    }


def _check(x, defined, shas, prs, blobs, trees, data_of) -> dict:
    name, kind, key = x["repo"], x["kind"], x["key"]
    out = dict(x)
    if kind == "id":
        where = defined.get(key, {})
        if name in where:
            out["status"] = "ok"
        elif where:
            out["status"], out["detail"] = "cross-repo", ",".join(sorted(where))
        else:
            out["status"] = "undefined"
    elif kind == "pr":
        out["status"] = "merged" if key in prs.get(name, set()) else "not-in-history"
    elif kind == "sha":
        hits = shas.get(key, [])
        real = [h for h in hits if h[1] != "ambiguous"]
        if any(h[1] == "ambiguous" for h in hits) and not real:
            out["status"] = "ambiguous"
        elif not real:
            out["status"] = "external"
        else:
            local = [h for h in real if h[0] == name]
            out["status"] = "ok" if local else "cross-repo"
            out["detail"] = ",".join(sorted(f"{h[0]}@{h[1][:12]}" for h in real))
    elif kind == "link":
        out.update(_check_link(name, x["path"], key, blobs, trees, data_of, shas))
    elif kind == "fileline":
        out.update(_check_fileline(name, x, blobs, data_of))
    return out


def _range(data: bytes | None, start: int, end: int) -> dict:
    if data is None:
        return {"status": "ok"}
    n = _line_count(data)
    if start < 1 or end > n or end < start:
        return {"status": "broken", "detail": f"lines {start}-{end} of {n}"}
    return {"status": "ok"}


def _check_link(name, src, target, blobs, trees, data_of, shas) -> dict:
    gh = RE_GH.match(target)
    if gh:
        repo = next((n for n in blobs if n.lower() == gh.group(2).lower()), None)
        if repo is None:
            return {"status": "external"}
        if gh.group(3) == "commit":
            hit = [
                h
                for h in shas.get(gh.group(4), [])
                if h[0] == repo and h[1] != "ambiguous"
            ]
            if not hit:
                return {"status": "broken", "detail": f"commit not in {repo}"}
            return {"status": "ok" if repo == name else "cross-repo", "detail": repo}
        path = (gh.group(5) or "").strip("/")
        if path not in blobs[repo] and path not in trees[repo]:
            return {
                "status": "broken",
                "detail": f"{repo}:{path} not at indexed commit",
            }
        if gh.group(6):
            start = int(gh.group(6))
            end = int(gh.group(7) or start)
            return {**_range(data_of.get((repo, path)), start, end), "detail": repo}
        return {"status": "ok" if repo == name else "cross-repo", "detail": repo}
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", target) or target.startswith("#"):
        return {"status": "external"}
    path, _, anchor = target.partition("#")
    path = unquote(path)
    full = (
        path.lstrip("/")
        if path.startswith("/")
        else posixpath.join(posixpath.dirname(src), path)
    )
    full = posixpath.normpath(full)
    if full == "." or full.startswith("../") or full == "..":
        return {"status": "outside-repo", "detail": full}
    if full not in blobs[name] and full not in trees[name]:
        return {"status": "broken", "detail": f"{full} not at indexed commit"}
    m = RE_ANCHOR_L.match(anchor)
    if m:
        start = int(m.group(1))
        return _range(data_of.get((name, full)), start, int(m.group(2) or start))
    return {"status": "ok"}


def _check_fileline(name, x, blobs, data_of) -> dict:
    """A path with a directory resolves from the citing file, the repo root, or a
    unique tail. A bare filename resolves only beside the citing file: `gate.py`
    alone could be any repo's gate.py, so elsewhere it is unanchored, not broken."""
    p = x["key"]
    files = blobs[name]
    here = posixpath.normpath(posixpath.join(posixpath.dirname(x["path"]), p))
    if "/" not in p:
        if here not in files:
            return {"status": "unanchored"}
        hit = here
    else:
        hit = next((c for c in (here, p) if c in files), None)
        if hit is None:
            tail = [f for f in files if f.endswith("/" + p)]
            if len(tail) > 1:
                return {
                    "status": "ambiguous",
                    "detail": f"{len(tail)} paths end with {p}",
                }
            hit = tail[0] if tail else None
        if hit is None:
            return {"status": "external"}
    res = _range(data_of.get((name, hit)), x["start"], x["end"])
    if res["status"] == "ok" and x.get("pin"):
        if not files[hit]["git_blob"].startswith(x["pin"]):
            now = files[hit]["git_blob"][:12]
            res = {"status": "moved", "detail": f"{hit} is now {now}"}
    return {**res, "target": hit}


# ── report ───────────────────────────────────────────────────────────────────
BROKEN = ("broken", "undefined")


def report(index: dict) -> str:
    """Plain text for people. Deterministic: no clock, sorted."""
    lines = ["xref report", ""]
    for i in index["inputs"]:
        bad = [
            f["path"]
            for f in index["arrival"][i["repo"]]["files"]
            if f.get("state") == "present" and not f["blob_match"]
        ]
        lines.append(
            f"{i['repo']}: {i['state']}"
            + (
                f" @ {i['commit'][:12]} on_remote={i['on_remote']}"
                if i.get("commit")
                else ""
            )
            + (f" ({i['reason']})" if i.get("reason") else "")
            + (f"; blob mismatch: {len(bad)}" if bad else "")
        )
        for p in bad:
            lines.append(f"  mismatch  {p}")
    counts: dict = {}
    for r in index["refs"]:
        k = (r["kind"], r["status"])
        counts[k] = counts.get(k, 0) + 1
    lines += ["", "references (kind status count):"]
    lines += [f"  {k} {s} {n}" for (k, s), n in sorted(counts.items())]
    lines += ["", f"ids defined twice in one repo: {len(index['dupes'])}"]
    for d in index["dupes"]:
        lines.append(f"  {d['repo']} {d['key']}: {', '.join(d['at'])}")
    lines += ["", "broken:"]
    for r in index["refs"]:
        if r["status"] in BROKEN:
            lines.append(
                f"  {r['repo']}:{r['path']}:{r['line']} {r['kind']} {r['key']}"
                + (f" ({r['detail']})" if r.get("detail") else "")
            )
    return "\n".join(lines) + "\n"


# ── 4 record and the slice ───────────────────────────────────────────────────
def record_outputs(box: Path, index_bytes: bytes, report_bytes: bytes, inputs) -> dict:
    """Writes both outputs through the record. Unattested until sealed."""
    from . import gate, record

    pkg = Path(__file__).resolve().parent
    rec = record.Record(
        box,
        lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
        record.version_of(pkg),
        gate.token(),
    )
    who = gate.system()
    rec.write_file(who, "xref/index.json", index_bytes, provenance="authored")
    rec.write_file(who, "xref/report.txt", report_bytes, provenance="authored")
    return rec.append(
        "xref",
        who,
        inputs=[
            {k: i.get(k) for k in ("repo", "state", "commit", "on_remote")}
            for i in inputs
        ],
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )


def slice_id(index: dict, repo_paths: dict, key: str) -> str:
    """Every line that defines or cites `key`, tagged with where it came from.
    Re-reads each file at the indexed commit and refuses one whose bytes no
    longer match the index."""
    commits = {i["repo"]: i.get("commit") for i in index["inputs"]}
    files = {n: {f["path"]: f for f in a["files"]} for n, a in index["arrival"].items()}
    m = re.fullmatch(r"PR\s*#?(\d+)", key.strip())
    kind, want = ("pr", m.group(1)) if m else ("id", key.strip())
    hits = [
        ("def", d["repo"], d["path"], d["line"])
        for d in index["defs"]
        if kind == "id" and d["id"] == want
    ]
    hits += [
        ("cite", r["repo"], r["path"], r["line"])
        for r in index["refs"]
        if r["kind"] == kind and r["key"] == want
    ]
    out = [f"slice {key} — for people; serving it to the model waits on Q19", ""]
    texts: dict = {}
    for role, name, path, n in sorted(set(hits), key=lambda h: (h[0] != "def", h[1:])):
        if (name, path) not in texts:
            data = git(Path(repo_paths[name]), "show", f"{commits[name]}:{path}")
            want = files[name][path]["sha256"]
            texts[(name, path)] = (
                data.decode("utf-8", "replace").splitlines()
                if hashlib.sha256(data).hexdigest() == want
                else None
            )
        lines = texts[(name, path)]
        text = "[refused: bytes changed since index]" if lines is None else lines[n - 1]
        sha = files[name][path]["sha256"][:16]
        out.append(
            f"{role}\t{name}@{commits[name][:12]}\t{path}:{n}\tsha256:{sha}\t{text}"
        )
    return "\n".join(out) + "\n"


# ── cli ──────────────────────────────────────────────────────────────────────
def _repos(args) -> list:
    out = []
    for spec in args.repo:
        name, _, path = spec.rpartition("=") if "=" in spec else ("", "", spec)
        out.append((name or Path(path).resolve().name, path))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xref")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("index")
    a.add_argument("--repo", action="append", required=True, help="path or name=path")
    a.add_argument("--rev", default="HEAD")
    a.add_argument("--out", required=True)
    a.add_argument("--cache")
    a.add_argument("--record", help="record box directory")
    s = sub.add_parser("slice")
    s.add_argument("--index", required=True)
    s.add_argument("--repo", action="append", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "index":
        idx = build(_repos(args), args.rev, Path(args.cache) if args.cache else None)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        ib = (canon(idx) + "\n").encode()
        rb = report(idx).encode()
        (out / "index.json").write_bytes(ib)
        (out / "report.txt").write_bytes(rb)
        if args.record:
            row = record_outputs(Path(args.record), ib, rb, idx["inputs"])
            print(f"record row {row['n']} {row['hash']}")
        print(rb.decode(), end="")
        return 0
    idx = json.loads(Path(args.index).read_text(encoding="utf-8"))
    text = slice_id(idx, dict(_repos(args)), args.id)
    Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
