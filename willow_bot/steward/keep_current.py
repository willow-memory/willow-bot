"""keep_current.py — the steward acting on what ``current`` sees, as far as
the bot may act alone: it writes the pull trigger a webhook never did,
refreshes a stale install where there is precedent, and files for the person
what needs a person. It restarts, upgrades and copies nothing.

Each tick (``run``), four checks, each with its own cadence:

- **restart owed** (every tick): a running user unit whose code changed
  after it started. Its code is its ``WorkingDirectory`` checkout and every
  checkout installed editable into the venv it runs from. "Changed" is the
  commit checked out when the unit started (from the checkout's reflog)
  against ``HEAD``, counting only files that aren't docs: a pull of
  Markdown owes nothing. A venv whose packages were reinstalled after the
  unit started owes one too.
- **stale install** (every tick): an editable install older than the
  arrival (reflog time) of its checkout's last ``pyproject.toml`` change, so
  a new dependency, pin or entry point never reached the venv (the
  willow-bot label read 1.8.0 for weeks while the code was 1.24). Refreshed
  by the bot, two a tick, only where there is precedent: the checkout's own
  ``.venv`` (what ``install_receipt`` already does after a pull), or the box
  venv willow-mcp's pip_sync allowlist pairs with its repo (through the
  broker), from a clean checkout on its default branch, after a dry run with
  no conflict; ``pip check`` after, and a broken result is filed high.
  Another repo installed into a venv is never the bot's to refresh: filed.
  ``WILLOW_BOT_KEEP_CURRENT_REFRESH=0`` turns refreshing off.
- **behind** (hourly): a checkout on its default branch, clean, behind its
  origin (``current.checkout``) gets the gitsync trigger the push webhook
  would have written; the next sweep pulls it as usual.
- **copies** (daily): a byte copy, pin, engine path or scheduled workflow
  from ``current.copies`` that has fallen behind its source, and almanac
  catalog entries still marked live that the link checker could not reach.
- **pypi** (daily): a venv holding a package with a known vulnerability, or
  a yanked release (``pypi_fresh``), filed at high priority with each fix
  and whether the venv's own ranges allow it. Upgrades that are only newer
  are counted in the receipt, never filed.

What needs a person is filed once as ``human_required`` (with
``WILLOW_BOT_MCP``), keyed so the same finding is never filed twice, and
resolved by a later tick that finds it gone. A check that didn't run this
tick resolves nothing. State lives in ``willow-bot/keep_current.json``.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from willow_bot import current, pypi_fresh, venvs
from willow_bot import paths as _paths

#: Seconds between runs of the slower checks; the cheap ones run every tick.
CADENCE = {"behind": 3600, "copies": 86400, "pypi": 86400}
#: Filings per tick: the steward's MCP calls share one rate (willow-mcp's 60 a
#: minute per app), so a backlog is filed a few at a time, most urgent first.
FILE_PER_TICK = 5
_URGENCY = {"refresh": 0, "restart": 0, "pypi": 1, "install": 2, "copies": 3}
_DOC_SUFFIXES = (".md", ".rst", ".txt")
_DOC_DIRS = ("docs/", "posts/")


def path() -> Path:
    return _paths.bot_dir() / "keep_current.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def _git(checkout: Path, *args: str) -> str:
    got = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                         timeout=20, check=False)  # fmt: skip
    if got.returncode != 0:
        raise OSError((got.stderr.strip() or f"git {args[0]} exited {got.returncode}")[:200])
    return got.stdout


def is_doc(name: str) -> bool:
    return name.endswith(_DOC_SUFFIXES) or name.startswith(_DOC_DIRS) or "/docs/" in name


def head_at(checkout: Path, when: float) -> str | None:
    """The commit ``checkout`` had checked out at ``when``: the newest reflog
    entry made at or before it, by the reflog's own time (when HEAD moved),
    never the commit's date: an old commit pulled late moved HEAD late.
    ``None`` when the reflog doesn't reach back."""
    for line in _git(checkout, "log", "-g", "--date=unix", "--format=%H %gd", "HEAD").splitlines():
        sha, _, selector = line.partition(" ")
        moved = selector.rpartition("{")[2].rstrip("}")
        if moved.isdigit() and int(moved) <= when:
            return sha
    return None


def code_since(checkout: Path, when: float) -> dict[str, Any]:
    """What changed in ``checkout``'s code after ``when``: ``{"status",
    "head", "then", "paths"}``, paths being the files that aren't docs."""
    try:
        head = _git(checkout, "rev-parse", "HEAD").strip()
        then = head_at(checkout, when)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unreachable", "detail": str(exc)[:200]}
    if then is None:
        return {"status": "unreachable", "head": head, "detail": "the reflog doesn't reach back to the start"}
    if then == head:
        return {"status": "empty", "head": head, "then": then, "paths": []}
    try:
        names = _git(checkout, "diff", "--name-only", then, head).split()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unreachable", "head": head, "detail": str(exc)[:200]}
    code = [n for n in names if not is_doc(n)]
    return {"status": "populated" if code else "empty", "head": head, "then": then, "paths": code}


def _unit_props(units: list[str]) -> dict[str, dict[str, str]]:
    if not units:
        return {}
    got = subprocess.run(["systemctl", "--user", "show", "--timestamp=unix",
                          "--property=Id,ActiveEnterTimestamp,WorkingDirectory", *units],
                         capture_output=True, text=True, timeout=20, check=False)  # fmt: skip
    if got.returncode != 0:
        raise OSError(got.stderr.strip() or f"systemctl exited {got.returncode}")
    out: dict[str, dict[str, str]] = {}
    for block in got.stdout.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id"):
            out[props["Id"]] = props
    return out


def restarts_owed(inv: dict[str, Any]) -> dict[str, Any]:
    """Each running unit whose code changed since it started."""
    by_unit: dict[str, list[Path]] = {}
    venvs_of: dict[str, list[Path]] = {}
    for v in inv.get("venvs", []):
        for unit in (v.get("runs") or {}).get("units", []):
            by_unit.setdefault(unit, []).extend(Path(p) for p in v.get("editables", []))
            venvs_of.setdefault(unit, []).append(Path(v["path"]))
    try:
        props = _unit_props(sorted(by_unit))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unreachable", "detail": str(exc)[:200], "owed": []}
    owed = []
    for unit, editables in sorted(by_unit.items()):
        p = props.get(unit, {})
        started = p.get("ActiveEnterTimestamp", "").lstrip("@")
        if not started.isdigit():
            continue
        roots = list(dict.fromkeys([*editables, *([Path(p["WorkingDirectory"])] if p.get("WorkingDirectory") else [])]))
        for root in roots:
            if not (root / ".git").exists():
                continue
            change = code_since(root, int(started))
            if change["status"] == "populated":
                owed.append({"unit": unit, "checkout": str(root), "started": int(started), **change})
        # Reinstalled after the start (a refresh, an upgrade): the running
        # process still has the old packages loaded.
        for venv in venvs_of.get(unit, []):
            newest = max((d.stat().st_mtime for d in venv.glob("lib/python*/site-packages/*.dist-info")), default=0)
            if newest > int(started):
                owed.append({"unit": unit, "checkout": str(venv), "started": int(started), "status": "populated",
                             "head": f"install@{int(newest)}", "then": "", "paths": [f"packages reinstalled in {venv}"]})  # fmt: skip
    return {"status": "populated" if owed else "empty", "owed": owed}


def arrived_at(checkout: Path, commit: str) -> int | None:
    """When ``commit`` arrived in ``checkout``'s HEAD, by the reflog's time:
    the oldest reflog entry, walking back from the newest, that still
    contains it. ``None`` when the reflog has no entry containing it."""
    arrived = None
    for line in _git(checkout, "log", "-g", "--date=unix", "--format=%H %gd", "HEAD").splitlines():
        sha, _, selector = line.partition(" ")
        got = subprocess.run(["git", "-C", str(checkout), "merge-base", "--is-ancestor", commit, sha],
                             capture_output=True, timeout=20, check=False)  # fmt: skip
        if got.returncode != 0:
            break
        moved = selector.rpartition("{")[2].rstrip("}")
        arrived = int(moved) if moved.isdigit() else arrived
    return arrived


def _installed_at(venv: Path, checkout: str) -> float | None:
    """When ``checkout`` was last installed editable into ``venv``: its
    ``direct_url.json``'s modification time."""
    for direct in venv.glob("lib/python*/site-packages/*.dist-info/direct_url.json"):
        try:
            data = json.loads(direct.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("dir_info") or {}).get("editable") and data.get("url", "").endswith(checkout):
            return direct.stat().st_mtime
    return None


def stale_installs(inv: dict[str, Any]) -> dict[str, Any]:
    """Each editable install older than its checkout's last ``pyproject.toml``
    change, in a venv something runs."""
    stale = []
    for v in inv.get("venvs", []):
        if not (v.get("runs") or {}).get("units"):
            continue
        for checkout in dict.fromkeys(v.get("editables", [])):
            when = _installed_at(Path(v["path"]), checkout)
            try:
                changed = _git(Path(checkout), "log", "-1", "--format=%H %ct", "--", "pyproject.toml").split()
                arrived = arrived_at(Path(checkout), changed[0]) if changed else None
            except (OSError, subprocess.SubprocessError):
                continue
            moved = max(int(changed[1]), arrived or 0) if changed else 0
            if when is not None and changed and moved > when:
                stale.append({"venv": v["path"], "checkout": checkout, "units": v["runs"]["units"],
                              "installed": int(when), "pyproject": moved, "commit": changed[0][:7]})  # fmt: skip
    return {"status": "populated" if stale else "empty", "stale": stale}


#: Refreshes per tick: each is a real pip install.
REFRESH_PER_TICK = 2


def _refresh_enabled() -> bool:
    return os.environ.get("WILLOW_BOT_KEEP_CURRENT_REFRESH", "1").strip() != "0"


def _allowlisted_venv(repo: str) -> str | None:
    """The box venv willow-mcp's pip_sync allowlist binds ``repo`` to, from the
    box's own copy (the broker checks it again; this only spares a refusal)."""
    try:
        allow = json.loads((_paths.willow_home() / "constitutional" / "pip_sync_allowlist.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, _paths.BoxNotConfigured):
        return None
    pairs = {k.lower(): v for k, v in (allow.get("pairs") or {}).items() if isinstance(v, dict)}
    return (pairs.get(repo.lower()) or {}).get("venv")


def how_to_refresh(venv: Path, checkout: Path) -> str | None:
    """``own`` when ``venv`` is the checkout's own ``.venv`` (what
    install_receipt already refreshes after a pull), ``broker`` when it is the
    box venv the pip_sync allowlist pairs with the checkout's repo, else
    ``None``: another repo installed into a venv isn't the bot's to touch."""
    own = checkout / ".venv"
    if own.exists() and own.resolve() == venv.resolve():
        return "own"
    try:
        homes = {_paths.venvs_dir().resolve(), _paths.legacy_venvs_dir().resolve()}
    except _paths.BoxNotConfigured:
        return None
    repo = current._origin_repo(checkout)
    if venv.resolve().parent in homes and repo and _allowlisted_venv(repo) == venv.name:
        return "broker"
    return None


def _clean_on_default(checkout: Path) -> str | None:
    """Why ``checkout`` shouldn't be installed from, or ``None`` when it's clean
    and on its default branch."""
    try:
        if _git(checkout, "status", "--porcelain", "--untracked-files=no").strip():
            return "the checkout has changes"
        default = _git(checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip().partition("/")[2]
        branch = _git(checkout, "branch", "--show-current").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git: {exc}"[:200]
    return None if default and branch == default else f"on {branch or '(detached)'}, not {default or 'the default'}"


def _pip(venv: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(venv / "bin" / "pip"), *args], capture_output=True, text=True, timeout=600, check=False)


def refresh(stale: dict[str, Any], *, call=None) -> dict[str, Any]:
    """Refresh one stale editable install where the bot may: dry run first, and
    ``pip check`` after. Returns its receipt; ``state`` is ``refreshed``,
    ``broken`` (installed, but ``pip check`` fails), ``refused`` (not tried,
    and why) or ``failed``."""
    venv, checkout = Path(stale["venv"]), Path(stale["checkout"])
    row: dict[str, Any] = {"venv": str(venv), "checkout": str(checkout)}
    how = how_to_refresh(venv, checkout)
    if how is None:
        return {**row, "state": "refused", "why": "another repo's install: the person's to refresh"}
    why = _clean_on_default(checkout)
    if why:
        return {**row, "state": "refused", "how": how, "why": why}
    if how == "broker":
        if call is None:
            return {**row, "state": "refused", "how": how, "why": "WILLOW_BOT_MCP not enabled"}
        try:
            result = call("pip_sync_execute", {"checkout": str(checkout), "venv": venv.name})
        except Exception as exc:  # noqa: BLE001 — said, retried next tick
            return {**row, "state": "failed", "how": how, "why": str(exc)[:300]}
        if not isinstance(result, dict) or result.get("error") or not result.get("ok", True):
            return {**row, "state": "failed", "how": how, "why": str((result or {}).get("error") or result)[:300]}
    else:
        extras = venvs.extras_of(venv, str(checkout))
        target = f"{checkout}[{','.join(extras)}]" if extras else str(checkout)
        dry = _pip(venv, "install", "--dry-run", "-e", target)
        if dry.returncode != 0 or "ERROR" in dry.stderr:
            return {**row, "state": "refused", "how": how, "why": "dry run: " + (dry.stderr.strip() or dry.stdout.strip())[-300:]}
        done = _pip(venv, "install", "-q", "-e", target)
        if done.returncode != 0:
            return {**row, "state": "failed", "how": how, "why": (done.stderr.strip() or "pip failed")[-300:]}
    check = _pip(venv, "check")
    if check.returncode != 0:
        return {**row, "state": "broken", "how": how, "why": check.stdout.strip()[-500:]}
    return {**row, "state": "refreshed", "how": how}


def _trigger(repo: str) -> str:
    """Ask the next sweep to pull ``repo``, as a push webhook would have."""
    from integrations import (
        fleet_bridge,  # local import: the receiver's own trigger writer
    )

    fleet_bridge._request_gitsync(repo)
    return "requested"


def behind() -> dict[str, Any]:
    """Checkouts behind their origin, and the pull asked for each one that's
    clean and on its default branch."""
    rows = current.checkouts().get("checkouts", [])
    out = []
    for r in rows:
        if r.get("state") != "behind":
            continue
        if r.get("dirty"):
            out.append({**r, "pull": "not asked: the checkout has changes"})
            continue
        try:
            out.append({**r, "pull": _trigger(r["repo"]) if r.get("repo") else "not asked: no GitHub origin"})
        except Exception as exc:  # noqa: BLE001 — a trigger not written is said, never silent
            out.append({**r, "pull": f"could not ask: {exc}"[:200]})
    return {"status": "populated" if out else "empty", "behind": out}


def findings(sections: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    """``{dedupe key: {"section", "title", "summary"}}`` for what needs a person."""
    found: dict[str, dict[str, str]] = {}
    # One item per unit: a unit can owe its restart to several checkouts and
    # a reinstalled venv at once (grove-serve ran willows-grove, willow-mcp
    # and a reinstall behind, three items for one restart, 2026-10-31), and
    # one restart clears them all.
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for o in sections.get("restart", {}).get("owed", []):
        by_unit.setdefault(o["unit"], []).append(o)
    for unit, rows in sorted(by_unit.items()):
        why = []
        for o in rows:
            if str(o["head"]).startswith("install@"):
                why.append(f"packages were reinstalled in {o['checkout']}")
            else:
                why.append(f"{o['checkout']} moved from {o['then'][:12]} to {o['head'][:12]} "
                           f"({len(o['paths'])} code files: {', '.join(o['paths'][:5])})")
        found[f"restart:{unit}"] = {
            "section": "restart",
            "title": f"{unit} runs code older than its checkout"[:200],
            "summary": f"Since {unit} started, {'; '.join(why)}. Restart it: systemctl --user restart {unit}",
        }
    for b in sections.get("refresh", {}).get("broken", []):
        found[f"refresh-broken:{b['venv']}"] = {
            "section": "refresh", "priority": "high",
            "title": f"refreshing {Path(b['checkout']).name} left {b['venv']} with broken requirements"[:200],
            "summary": f"pip check after the refresh: {b['why']}",
        }
    by_venv: dict[str, list[dict[str, Any]]] = {}
    for s in sections.get("install", {}).get("stale", []):
        by_venv.setdefault(s["venv"], []).append(s)
    for venv, rows in sorted(by_venv.items()):
        names = ", ".join(f"{Path(r['checkout']).name} ({r['commit']})" for r in rows)
        found[f"install:{venv}"] = {
            "section": "install",
            "title": f"{venv} has {len(rows)} editable install(s) older than their pyproject.toml"[:200],
            "summary": (f"Changed since installed: {names}. Run by {', '.join(rows[0]['units'])}. Refresh each with "
                        f"{venv}/bin/pip install -e '<checkout>[<its extras>]' (`willow-bot-steward venvs --plan` "
                        "shows the extras), then restart those units."),
        }
    cp = sections.get("copies") or {}
    for b in cp.get("bytes", []):
        if b.get("status") == "populated" and not b["current"]:
            found[f"copy:{b['copy']}"] = {"section": "copies", "title": f"{b['copy']} is not {b['source']}"[:200],
                                          "summary": f"The copy has drifted from its source. {b.get('detail', '')}".strip()}  # fmt: skip
    for p in cp.get("pin", []):
        if p.get("status") == "populated" and not p["current"]:
            found[f"pin:{p['pin']}"] = {"section": "copies", "title": f"{p['pin']} is behind {p['source']}"[:200],
                                        "summary": "Pinned at " + p.get("pinned", "?") + "; since: "
                                        + "; ".join(p.get("since", [])[:5])}  # fmt: skip
    # One item per engine workspace, naming every vertical behind it: the
    # operator, 2026-10-31, of ten identical almanac items: "fold".
    for e in cp.get("engine", []):
        behind = sorted((e.get("behind") or {}).items())
        if not behind:
            continue
        lines = [f"{vertical}: {', '.join(paths['update'] + paths['add'])}" for vertical, paths in behind]
        found[f"engine:{e['workspace']}"] = {
            "section": "copies",
            "title": (f"{behind[0][0]} is behind its engine ({e['workspace']})" if len(behind) == 1
                      else f"{len(behind)} verticals are behind their engine ({e['workspace']})")[:200],
            "summary": ("Engine paths to propagate, per vertical: " + "; ".join(lines) + ". "
                        "Each vertical's own dry run decided this; local overrides are never counted."),
        }
    # A vertical the dry run skipped is never "behind": say so, or it is silent.
    for e in cp.get("engine", []):
        missing = e.get("missing") or []
        if not missing:
            continue
        owner = e["workspace"].split("/", 1)[0]
        found[f"engine-missing:{e['workspace']}"] = {
            "section": "copies",
            "title": ((f"{missing[0]} is" if len(missing) == 1 else f"{len(missing)} verticals are")
                      + f" not in {e['workspace']}'s workspace on this box")[:200],
            "summary": ("Its engine dry run skipped them, so their drift goes unchecked: " + ", ".join(missing)
                        + ". Clone each beside the others: "
                        + "; ".join(f"git clone https://github.com/{owner}/{v}.git <workspace>/{v}" for v in missing)
                        + "."),
        }
    for v in (sections.get("pypi") or {}).get("venvs", []):
        bad = [p for p in v.get("packages", []) if p.get("kind") in ("vulnerable", "yanked")]
        if not bad:
            continue
        lines = []
        for p in bad:
            if p["kind"] == "yanked":
                lines.append(f"{p['name']} {p['installed']} was yanked")
                continue
            where = "inside its declared range" if p.get("fix_in_range") else "OUTSIDE its declared range"
            lines.append(f"{p['name']} {p['installed']} -> {p.get('fix') or 'no fix released'} ({where}; "
                         f"{', '.join(p['vulnerabilities'][:3])})")  # fmt: skip
        found[f"pypi:{v['path']}"] = {
            "section": "pypi", "priority": "high",
            "title": f"{v['path']} has {len(bad)} vulnerable or yanked package(s)"[:200],
            "summary": "; ".join(lines),
        }
    # One item per almanac workspace, the engine precedent: one decision per
    # catalog sweep, not one item per entry.
    for c in cp.get("catalog", []):
        rows = [v for v in c.get("verticals", []) if v.get("status") == "populated" and v.get("unreached")]
        if not rows:
            continue
        count = sum(len(v["unreached"]) for v in rows)
        lines = [f"{v['repo']}: {', '.join(str(e['id']) for e in v['unreached'])}" for v in rows]
        found[f"catalog:{c['repos_of']}"] = {
            "section": "copies",
            "title": (f"{count} almanac entr{'y is' if count == 1 else 'ies are'} marked live "
                      f"but unreachable ({c['repos_of']})")[:200],
            "summary": ("The link checker's last probe got no response from these, and each is still marked "
                        "live or frozen: " + "; ".join(lines) + ". A curator decides each status in a PR "
                        "(dark, moved, or a transient outage to leave alone); nothing here changes one."),
        }
    for w in cp.get("workflow", []):
        for r in w.get("runs", []):
            if r.get("status") == "populated" and (r["stale"] or r["failed"]):
                found[f"workflow:{r['repo']}:{w['workflow']}"] = {
                    "section": "copies", "title": f"{r['repo']}'s {w['workflow']} is {'red' if r['failed'] else 'stale'}"[:200],
                    "summary": f"Newest run {r['at']}, {r['conclusion']}, {r['age_days']} days ago.",
                }
    return found


def _due(state: dict[str, Any], name: str, now: float) -> bool:
    """Whether ``name`` is due: never run, or its cadence has passed."""
    last = (state.get("ran") or {}).get(name)
    return last is None or now - float(last) >= CADENCE[name]


def run(*, enable_mcp: bool = False, call=None, app: str = "", now: float | None = None) -> dict[str, Any]:
    """One tick. Returns the receipt (the caller emits it)."""
    now = time.time() if now is None else now
    state = load()
    ran = dict(state.get("ran") or {})
    inv = venvs.inventory()
    refreshed: list[dict[str, Any]] = []
    if _refresh_enabled():
        for st in stale_installs(inv)["stale"]:
            if len([r for r in refreshed if r["state"] != "refused"]) >= REFRESH_PER_TICK:
                break
            refreshed.append(refresh(st, call=call if enable_mcp else None))
    sections: dict[str, dict[str, Any]] = {"restart": restarts_owed(inv), "install": stale_installs(inv)}
    sections["refresh"] = {"status": "populated" if refreshed else "empty",
                           "broken": [r for r in refreshed if r["state"] == "broken"]}  # fmt: skip
    checked = {"restart", "install", "refresh"}
    if _due(state, "behind", now):
        sections["behind"] = behind()
        ran["behind"] = now
    if _due(state, "copies", now):
        sections["copies"] = current.copies()
        ran["copies"] = now
        checked.add("copies")
    if _due(state, "pypi", now):
        sections["pypi"] = pypi_fresh.report()
        ran["pypi"] = now
        checked.add("pypi")
    # A section that couldn't run says nothing about what it would have found.
    checked = {s for s in checked if (sections.get(s) or {}).get("status") != "unreachable"}

    found = findings(sections)
    # A finding of a check that didn't run this tick stands until that check
    # runs again: carried, so one still waiting to be filed isn't dropped.
    for key, f in (state.get("found") or {}).items():
        if f.get("section") not in checked and key not in found:
            found[key] = f
    filed: dict[str, dict[str, Any]] = dict(state.get("filed") or {})
    # Restart items used to be filed per (unit, commit) as
    # `restart:<unit>:<head>`, and engine items per (workspace, vertical) as
    # `engine:<workspace>:<vertical>`. A unit or workspace still found keeps
    # its newest such item as its one item; the others resolve, saying where
    # they went.
    folded_into: dict[str, str] = {}
    for key in found:
        if not key.startswith(("restart:", "engine:")) or key in filed:
            continue
        olds = sorted((k for k in filed if k.startswith(f"{key}:")), key=lambda k: filed[k].get("since") or 0)
        if olds:
            filed[key] = filed.pop(olds[-1])
            folded_into.update({k: filed[key]["id"] for k in olds[:-1]})
    items: list[dict[str, Any]] = []
    budget = FILE_PER_TICK
    order = sorted(found.items(), key=lambda kv: (kv[1].get("priority") != "high", _URGENCY.get(kv[1]["section"], 9), kv[0]))
    for key, f in order:
        if key in filed:
            items.append({"key": key, "state": "open", "id": filed[key]["id"]})
            continue
        if not enable_mcp or call is None:
            items.append({"key": key, "state": "unfiled", "reason": "WILLOW_BOT_MCP not enabled", "title": f["title"]})
            continue
        if budget <= 0:
            items.append({"key": key, "state": "waiting", "reason": "filed a few per tick; next tick"})
            continue
        budget -= 1
        try:
            result = call("human_required_enqueue", {"app_id": app, "kind": "review",
                                                     "priority": f.get("priority", "normal"),
                                                     "title": f["title"], "summary": f["summary"][:2000],
                                                     "source_ref": f"keep-current:{key}"[:200]})  # fmt: skip
            err = result.get("error") if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001 — not filed; retried next tick
            result, err = None, str(exc)[:300]
        item_id = result.get("id") if isinstance(result, dict) and not err else None
        if err or not item_id:
            items.append({"key": key, "state": "could-not-file", "error": str(err or "no item id")[:300]})
            if "rate_limited" in str(err):
                budget = 0  # the rate is spent: the rest wait for the next tick
        else:
            filed[key] = {"id": item_id, "since": now, "section": f["section"]}
            items.append({"key": key, "state": "filed", "id": item_id})
    for key, row in sorted(filed.items()):
        if key in found or row.get("section") not in checked:
            continue
        if not enable_mcp or call is None:
            items.append({"key": key, "state": "open", "id": row["id"], "detail": "cleared; resolve owed"})
            continue
        try:
            one = "one engine item per workspace" if key.startswith("engine:") else "one restart item per unit"
            note = (f"keep-current: folded into {folded_into[key]}, {one}"
                    if key in folded_into else "keep-current: no longer found")
            result = call("human_required_resolve", {"app_id": app, "item_id": row["id"], "status": "resolved",
                                                     "note": note})  # fmt: skip
            err = result.get("error") if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001 — retried next tick
            err = str(exc)[:300]
        if err:
            items.append({"key": key, "state": "resolve-refused", "id": row["id"], "error": str(err)[:300]})
        else:
            items.append({"key": key, "state": "resolved", "id": row["id"]})
            del filed[key]
    _save({"ran": ran, "filed": filed, "found": found})
    return {
        "event": "steward_keep_current",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ran": sorted(sections),
        "restart_owed": [(o["unit"], o["checkout"]) for o in sections["restart"].get("owed", [])],
        "stale_installs": [(s["venv"], s["checkout"]) for s in sections["install"].get("stale", [])],
        "refreshed": [(r["venv"], r["checkout"], r["state"], r.get("why", "")[:120]) for r in refreshed],
        "pull_asked": [(b.get("repo"), b["pull"]) for b in (sections.get("behind") or {}).get("behind", [])],
        "pypi": {v["path"]: v.get("counts", {}) for v in (sections.get("pypi") or {}).get("venvs", [])},
        "unreachable": {k: v.get("detail") for k, v in sections.items() if v.get("status") == "unreachable"},
        "items": items,
    }
