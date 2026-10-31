"""current.py — what should be current, and whether it is.

``willow-bot-steward current [--json]``

The bot keeps the fleet current; this is the part that looks. It reads, and
says, for each thing that has to keep up with something else:

- **checkouts**: each git checkout under the clone root against its origin's
  default branch, asked with ``git ls-remote`` (nothing fetched, nothing
  written). A push webhook GitHub never delivered leaves a checkout behind
  with no trigger to pull it; this sees it anyway.
- **copies**: files held to a source in another repo (``copies.json``): byte
  copies (after a known replacement), pins (``kernel.pin``), an engine
  workspace's propagation (its own dry run decides, and its overrides are
  never drift), a scheduled workflow's newest run in each repo, and each
  almanac vertical's catalog: entries still marked live that the link checker
  could not reach.

Every answer is ``populated``, ``empty`` or ``unreachable``: a check that
could not run says so and is never counted as current. The comparisons are of
the local checkouts, which is why their freshness is reported beside them.
Nothing here pulls, copies, installs or opens a pull request.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willow_bot import operator_profile

REGISTRY = Path(__file__).with_name("copies.json")
_GIT_TIMEOUT = 20


def _git(checkout: Path, *args: str, timeout: int = _GIT_TIMEOUT) -> str:
    """``git -C checkout ...``'s stdout. Raises ``OSError`` when git fails."""
    got = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                         timeout=timeout, check=False)  # fmt: skip
    if got.returncode != 0:
        raise OSError((got.stderr.strip() or f"git {args[0]} exited {got.returncode}")[:200])
    return got.stdout


def _ls_remote(checkout: Path) -> tuple[str, str]:
    """The origin's default branch and its head commit, asked of the remote
    itself: ``git ls-remote --symref origin HEAD``. Nothing is fetched."""
    out = _git(checkout, "ls-remote", "--symref", "origin", "HEAD", timeout=30)
    branch = sha = ""
    for line in out.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            branch = line[len("ref: refs/heads/"):-len("\tHEAD")]
        elif line.endswith("\tHEAD"):
            sha = line.split("\t", 1)[0]
    if not (branch and sha):
        raise OSError("origin named no default branch")
    return branch, sha


def _origin_repo(checkout: Path) -> str:
    """``owner/name`` of a github.com origin, else ``""``."""
    try:
        url = _git(checkout, "remote", "get-url", "origin").strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(prefix):
            name = url[len(prefix):].removesuffix(".git").strip("/")
            return name if name.count("/") == 1 else ""
    return ""


def _checkout_dirs(root: Path) -> list[Path]:
    """Git checkouts at ``root/<name>`` and ``root/<org>/<name>``. A
    worktree's ``.git`` is a file and counts; ``.worktrees`` and ``worktrees``
    folders are working copies of checkouts already listed, never another."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for first in sorted(root.iterdir()):
        if not first.is_dir() or first.name.startswith("."):
            continue
        if (first / ".git").exists():
            out.append(first)
        for second in sorted(p for p in first.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if second.name not in ("worktrees", ".worktrees") and (second / ".git").exists():
                out.append(second)
    return out


def checkout(path: Path) -> dict[str, Any]:
    """One checkout against its origin. ``state`` is ``current``, ``behind``
    (``behind`` commits when the origin's head is known here, else
    ``unfetched``), ``ahead``, ``diverged``, ``off-default`` (on another
    branch: not judged) or ``unreachable`` (the origin couldn't be asked)."""
    row: dict[str, Any] = {"path": str(path), "repo": _origin_repo(path)}
    try:
        row["branch"] = _git(path, "branch", "--show-current").strip()
        row["head"] = _git(path, "rev-parse", "HEAD").strip()
        row["dirty"] = len([ln for ln in _git(path, "status", "--porcelain").splitlines() if ln.strip()])
    except (OSError, subprocess.SubprocessError) as exc:
        return {**row, "status": "unreachable", "state": "unreachable", "detail": str(exc)[:200]}
    try:
        default, remote = _ls_remote(path)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**row, "status": "unreachable", "state": "unreachable", "detail": str(exc)[:200]}
    row.update({"status": "populated", "default": default, "remote": remote})
    if row["branch"] != default:
        return {**row, "state": "off-default"}
    if row["head"] == remote:
        return {**row, "state": "current"}
    try:
        _git(path, "cat-file", "-e", f"{remote}^{{commit}}")
    except (OSError, subprocess.SubprocessError):
        return {**row, "state": "behind", "behind": "unfetched"}
    counts = _git(path, "rev-list", "--left-right", "--count", f"HEAD...{remote}").split()
    ahead, behind = int(counts[0]), int(counts[1])
    state = "diverged" if ahead and behind else "ahead" if ahead else "behind"
    return {**row, "state": state, "ahead": ahead, "behind": behind}


def checkouts() -> dict[str, Any]:
    """Every checkout under the clone root, against its origin."""
    root = operator_profile.github_root()
    rows = [checkout(p) for p in _checkout_dirs(root)]
    return {"status": "populated" if rows else "empty", "root": str(root), "checkouts": rows}


def repo_path(repo: str) -> Path | None:
    """The local checkout of ``owner/name``: the profile's ``checkouts`` map,
    then ``<root>/<owner>/<name>`` and ``<root>/<name>``, either matched
    case-blind (``willow-memory/willow`` is ``willow-memory/Willow`` here)."""
    mapped = operator_profile.checkouts().get(repo)
    if mapped is not None and mapped.is_dir():
        return mapped
    root = operator_profile.github_root()
    owner, _, name = repo.partition("/")
    for parent in (root / owner, root):
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.name.lower() == name.lower() and (child / ".git").exists():
                return child
    return None


def _split(ref: str) -> tuple[str, str]:
    repo, _, path = ref.partition(":")
    return repo, path


def _read(ref: str) -> tuple[bytes | None, str]:
    """The bytes of ``owner/name:path`` in its local checkout, or ``None``
    and why."""
    repo, path = _split(ref)
    where = repo_path(repo)
    if where is None:
        return None, f"no local checkout of {repo}"
    try:
        return (where / path).read_bytes(), ""
    except OSError as exc:
        return None, f"{ref}: {exc.strerror or exc}"


def _bytes(rule: dict[str, Any]) -> dict[str, Any]:
    row = {"rule": "bytes", "copy": rule["copy"], "source": rule["source"]}
    mine, why = _read(rule["copy"])
    theirs, why2 = _read(rule["source"])
    if mine is None or theirs is None:
        return {**row, "status": "unreachable", "detail": why or why2}
    text = mine
    for old, new in rule.get("replace") or []:
        if text.count(old.encode()) != 1:
            return {**row, "status": "populated", "current": False,
                    "detail": f"the copy should hold {old!r} once: it is not the known difference"}  # fmt: skip
        text = text.replace(old.encode(), new.encode())
    return {**row, "status": "populated", "current": text == theirs}


def _pin(rule: dict[str, Any]) -> dict[str, Any]:
    row = {"rule": "pin", "pin": rule["pin"], "source": rule["source"]}
    raw, why = _read(rule["pin"])
    theirs, why2 = _read(rule["source"])
    if raw is None or theirs is None:
        return {**row, "status": "unreachable", "detail": why or why2}
    try:
        pin = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {**row, "status": "unreachable", "detail": f"the pin isn't JSON: {exc}"}
    now = hashlib.sha256(theirs).hexdigest()
    out = {**row, "status": "populated", "pinned": str(pin.get("commit", ""))[:12], "current": pin.get("sha256") == now}
    if not out["current"]:
        repo, path = _split(rule["source"])
        where = repo_path(repo)
        try:
            log = _git(where, "log", "--format=%h %s", f"{pin.get('commit')}..HEAD", "--", path) if where else ""
            out["since"] = [ln for ln in log.splitlines() if ln][:20]
        except (OSError, subprocess.SubprocessError) as exc:
            out["since_detail"] = str(exc)[:200]
    return out


def _run_dry(workspace: Path, cmd: list[str]) -> str:
    got = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=180, check=False)
    if got.returncode != 0:
        raise OSError((got.stderr.strip() or f"{cmd[0]} exited {got.returncode}")[:200])
    # stderr too: the script names a vertical it has no folder for there.
    return got.stdout + got.stderr


def parse_engine(out: str) -> dict[str, dict[str, list[str]]]:
    """``propagate-engine.sh --dry-run``'s report, per vertical: ``update``
    (``~``), ``add`` (``+``), ``override`` (``o``: owned locally, never
    drift) and ``same`` (``=``)."""
    marks = {"~": "update", "+": "add", "o": "override", "=": "same"}
    verticals: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for line in out.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            current = verticals.setdefault(line[4:-4], {k: [] for k in marks.values()})
            continue
        bits = line.strip().split(" ", 2)
        if current is not None and len(bits) >= 2 and bits[0] in marks:
            current[marks[bits[0]]].append(bits[1])
    return verticals


def parse_missing(out: str) -> list[str]:
    """The verticals the dry run skipped, ``SKIP <vertical> — not linked at
    <path>``: named in its list, with no folder in the workspace, so neither
    checked against the engine nor counted among the verticals it saw."""
    return sorted({line.split()[1] for line in out.splitlines() if line.startswith("SKIP ") and "not linked" in line})


def _engine(rule: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"rule": "engine", "workspace": rule["workspace"]}
    where = repo_path(rule["workspace"])
    if where is None:
        return {**row, "status": "unreachable", "detail": f"no local checkout of {rule['workspace']}"}
    try:
        out = _run_dry(where, list(rule["dry_run"]))
    except (OSError, subprocess.SubprocessError) as exc:
        return {**row, "status": "unreachable", "detail": str(exc)[:200]}
    verticals = parse_engine(out)
    behind = {v: {"update": p["update"], "add": p["add"]} for v, p in verticals.items() if p["update"] or p["add"]}
    return {**row, "status": "populated" if verticals else "empty", "verticals": sorted(verticals),
            "missing": parse_missing(out), "behind": behind, "overrides": {v: p["override"] for v, p in verticals.items() if p["override"]}}  # fmt: skip


def _newest_run(repo: str, workflow: str) -> dict[str, Any] | None:
    """The newest run of ``workflow`` in ``repo``, under the App's token
    (module-level so tests keep it off the network)."""
    import github_app  # local import so a test can monkeypatch the module

    runs = github_app._repo_get_pages(repo, f"/actions/workflows/{workflow}/runs",
                                      per_page=1, max_pages=1, key="workflow_runs")  # fmt: skip
    return runs[0] if runs else None


def _workflow(rule: dict[str, Any], engines: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    row: dict[str, Any] = {"rule": "workflow", "workflow": rule["workflow"], "max_age_days": rule["max_age_days"]}
    owner = rule["repos_of"].split("/", 1)[0]
    of = [e for e in engines if e.get("workspace") == rule["repos_of"]]
    if not of or of[0].get("status") != "populated":
        return {**row, "status": "unreachable", "detail": f"no verticals known from {rule['repos_of']}"}
    runs = []
    for vertical in sorted(of[0]["verticals"] + of[0].get("missing", [])):
        repo = f"{owner}/{vertical}"
        try:
            run = _newest_run(repo, rule["workflow"])
        except Exception as exc:  # noqa: BLE001 — any failure to ask is "unreachable", per repo
            runs.append({"repo": repo, "status": "unreachable", "detail": str(exc)[:200]})
            continue
        if run is None:
            runs.append({"repo": repo, "status": "empty", "detail": "it has never run"})
            continue
        when = datetime.fromisoformat(str(run.get("created_at", "")).replace("Z", "+00:00"))
        age = (now - when).total_seconds() / 86400
        runs.append({"repo": repo, "status": "populated", "at": run.get("created_at"),
                     "conclusion": run.get("conclusion"), "age_days": round(age, 1),
                     "stale": age > rule["max_age_days"], "failed": run.get("conclusion") not in (None, "success")})  # fmt: skip
    return {**row, "status": "populated", "runs": runs}


def _catalog(rule: dict[str, Any], engines: list[dict[str, Any]]) -> dict[str, Any]:
    """Each vertical's generated catalog, read for the one disagreement a person
    has to settle: an entry the curator still calls ``live`` or ``frozen`` that
    the link checker's last probe got no response from at all
    (``observed.reachable`` false). A 403 behind bot protection is a response,
    so it is never counted here; ``status`` is the curator's to change, never
    this check's."""
    path = rule.get("path", "catalog.json")
    row: dict[str, Any] = {"rule": "catalog", "repos_of": rule["repos_of"], "path": path}
    owner = rule["repos_of"].split("/", 1)[0]
    of = [e for e in engines if e.get("workspace") == rule["repos_of"]]
    if not of or of[0].get("status") != "populated":
        return {**row, "status": "unreachable", "detail": f"no verticals known from {rule['repos_of']}"}
    verticals = []
    for vertical in sorted(of[0]["verticals"] + of[0].get("missing", [])):
        repo = f"{owner}/{vertical}"
        raw, why = _read(f"{repo}:{path}")
        if raw is None:
            verticals.append({"repo": repo, "status": "unreachable", "detail": why})
            continue
        try:
            entries = json.loads(raw).get("entries")
        except (json.JSONDecodeError, AttributeError) as exc:
            verticals.append({"repo": repo, "status": "unreachable", "detail": f"{path} isn't a catalog: {exc}"[:200]})
            continue
        if not isinstance(entries, list):
            verticals.append({"repo": repo, "status": "unreachable", "detail": f"{path} has no entries list"})
            continue
        unreached = [{"id": e.get("id"), "status": e.get("status"), "checked": (e.get("observed") or {}).get("checked")}
                     for e in entries if isinstance(e, dict) and e.get("status") in ("live", "frozen")
                     and (e.get("observed") or {}).get("reachable") is False]  # fmt: skip
        verticals.append({"repo": repo, "status": "populated" if entries else "empty", "entries": len(entries),
                          "unreached": unreached})  # fmt: skip
    return {**row, "status": "populated", "verticals": verticals}


def copies(registry: Path = REGISTRY, *, now: datetime | None = None) -> dict[str, Any]:
    """Every copy in ``registry`` against its source."""
    try:
        rules = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "unreachable", "detail": f"{registry}: {exc}"}
    engines = [_engine(r) for r in rules.get("engine", [])]
    now = now or datetime.now(timezone.utc)
    return {
        "status": "populated",
        "bytes": [_bytes(r) for r in rules.get("bytes", [])],
        "pin": [_pin(r) for r in rules.get("pin", [])],
        "engine": engines,
        "workflow": [_workflow(r, engines, now) for r in rules.get("workflow", [])],
        "catalog": [_catalog(r, engines) for r in rules.get("catalog", [])],
    }


def report() -> dict[str, Any]:
    return {"checkouts": checkouts(), "copies": copies()}


def _print_text(rep: dict[str, Any]) -> None:
    co = rep["checkouts"]
    rows = co.get("checkouts", [])
    off = [r for r in rows if r["state"] not in ("current", "off-default")]
    print(f"checkouts: {len(rows)} under {co['root']}, "
          f"{sum(r['state'] == 'current' for r in rows)} current, {len(off)} not")  # fmt: skip
    for r in off:
        extra = f" {r.get('behind')} behind" if r["state"] in ("behind", "diverged") else ""
        extra += f" {r.get('ahead')} ahead" if r["state"] in ("ahead", "diverged") else ""
        detail = f"  ({r['detail']})" if r.get("detail") else ""
        print(f"  {r['state']:11} {r['repo'] or r['path']}{extra}{detail}")
    for r in rows:
        if r["state"] == "off-default":
            print(f"  off-default {r['repo'] or r['path']}  on {r['branch'] or '(detached)'}, default {r['default']}")
    cp = rep["copies"]
    if cp.get("status") == "unreachable":
        print(f"copies: unknown — {cp['detail']}")
        return
    for b in cp["bytes"]:
        word = "unknown" if b["status"] == "unreachable" else "current" if b["current"] else "DIFFERS"
        print(f"copy   {word:8} {b['copy']}  <- {b['source']}" + (f"  ({b['detail']})" if b.get("detail") else ""))
    for p in cp["pin"]:
        word = "unknown" if p["status"] == "unreachable" else "current" if p["current"] else "BEHIND"
        print(f"pin    {word:8} {p['pin']} at {p.get('pinned', '?')}  <- {p['source']}")
        for line in p.get("since", []):
            print(f"         since: {line}")
    for e in cp["engine"]:
        if e["status"] == "unreachable":
            print(f"engine unknown  {e['workspace']}  ({e['detail']})")
            continue
        print(f"engine {e['workspace']}: {len(e['verticals'])} verticals, {len(e['behind'])} behind the engine"
              + (f", {len(e['missing'])} not on this box: {', '.join(e['missing'])}" if e.get("missing") else ""))
        for v, paths in sorted(e["behind"].items()):
            print(f"         {v}: {', '.join(paths['update'] + paths['add'])}")
        for v, paths in sorted(e["overrides"].items()):
            print(f"         {v} owns: {', '.join(paths)} (override)")
    for w in cp["workflow"]:
        if w["status"] == "unreachable":
            print(f"runs   unknown  {w['workflow']}  ({w['detail']})")
            continue
        bad = [r for r in w["runs"] if r["status"] != "populated" or r["stale"] or r["failed"]]
        print(f"runs   {w['workflow']}: {len(w['runs'])} repos, {len(bad)} stale, failed or unknown")
        for r in bad:
            why = r.get("detail") or f"{r['conclusion']} {r['age_days']} days ago"
            print(f"         {r['repo']}: {why}")
    for c in cp.get("catalog", []):
        if c["status"] == "unreachable":
            print(f"catalog unknown {c['repos_of']}  ({c['detail']})")
            continue
        read = [v for v in c["verticals"] if v["status"] != "unreachable"]
        unreached = sum(len(v["unreached"]) for v in read)
        print(f"catalog {c['repos_of']}: {sum(v['entries'] for v in read)} entries in {len(read)} verticals, "
              f"{unreached} marked live the link checker could not reach")  # fmt: skip
        for v in c["verticals"]:
            if v["status"] == "unreachable":
                print(f"         {v['repo']}: unknown ({v['detail']})")
            elif v["unreached"]:
                print(f"         {v['repo']}: {', '.join(str(e['id']) for e in v['unreached'])}")


def main(argv: list[str] | None = None) -> int:
    """``willow-bot-steward current [--json]``."""
    args = list(argv if argv is not None else sys.argv[1:])
    if [a for a in args if a != "--json"]:
        print("usage: willow-bot-steward current [--json]", file=sys.stderr)
        return 2
    rep = report()
    if "--json" in args:
        print(json.dumps(rep, indent=1))
    else:
        _print_text(rep)
    return 0
