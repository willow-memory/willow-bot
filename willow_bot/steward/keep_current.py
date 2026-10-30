"""keep_current.py — the steward acting on what ``current`` sees, as far as
the bot may act alone: it writes the pull trigger a webhook never did, and
files for the person what needs a person. It installs, restarts and copies
nothing.

Each tick (``run``), four checks, each with its own cadence:

- **restart owed** (every tick): a running user unit whose code changed
  after it started. Its code is its ``WorkingDirectory`` checkout and every
  checkout installed editable into the venv it runs from. "Changed" is the
  commit checked out when the unit started (from the checkout's reflog)
  against ``HEAD``, counting only files that aren't docs: a pull of
  Markdown owes nothing.
- **stale install** (every tick): an editable install older than its
  checkout's last ``pyproject.toml`` change, so a new dependency, pin or
  entry point never reached the venv (the willow-bot label read 1.8.0 for
  weeks while the code was 1.24).
- **behind** (hourly): a checkout on its default branch, clean, behind its
  origin (``current.checkout``) gets the gitsync trigger the push webhook
  would have written; the next sweep pulls it as usual.
- **copies** (daily): a byte copy, pin, engine path or scheduled workflow
  from ``current.copies`` that has fallen behind its source.

What needs a person is filed once as ``human_required`` (with
``WILLOW_BOT_MCP``), keyed so the same finding is never filed twice, and
resolved by a later tick that finds it gone. A check that didn't run this
tick resolves nothing. State lives in ``willow-bot/keep_current.json``.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from willow_bot import current, venvs
from willow_bot import paths as _paths

#: Seconds between runs of the slower checks; the cheap ones run every tick.
CADENCE = {"behind": 3600, "copies": 86400}
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
    entry at or before it. ``None`` when the reflog doesn't reach back."""
    for line in _git(checkout, "log", "-g", "--format=%H %ct", "HEAD").splitlines():
        sha, _, ct = line.partition(" ")
        if ct.isdigit() and int(ct) <= when:
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
    for v in inv.get("venvs", []):
        for unit in (v.get("runs") or {}).get("units", []):
            by_unit.setdefault(unit, []).extend(Path(p) for p in v.get("editables", []))
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
    return {"status": "populated" if owed else "empty", "owed": owed}


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
                changed = _git(Path(checkout), "log", "-1", "--format=%ct %h", "--", "pyproject.toml").split()
            except (OSError, subprocess.SubprocessError):
                continue
            if when is not None and changed and int(changed[0]) > when:
                stale.append({"venv": v["path"], "checkout": checkout, "units": v["runs"]["units"],
                              "installed": int(when), "pyproject": int(changed[0]), "commit": changed[1]})  # fmt: skip
    return {"status": "populated" if stale else "empty", "stale": stale}


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
    for o in sections.get("restart", {}).get("owed", []):
        found[f"restart:{o['unit']}:{o['head']}"] = {
            "section": "restart",
            "title": f"{o['unit']} runs code older than its checkout"[:200],
            "summary": (f"{o['checkout']} moved from {o['then'][:12]} to {o['head'][:12]} after {o['unit']} started; "
                        f"{len(o['paths'])} code files changed ({', '.join(o['paths'][:5])}). "
                        f"Restart it: systemctl --user restart {o['unit']}"),
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
    for e in cp.get("engine", []):
        for vertical, paths in sorted((e.get("behind") or {}).items()):
            found[f"engine:{e['workspace']}:{vertical}"] = {
                "section": "copies", "title": f"{vertical} is behind its engine ({e['workspace']})"[:200],
                "summary": (f"Engine paths to propagate: {', '.join(paths['update'] + paths['add'])}. "
                            "Its own dry run decided this; local overrides are never counted."),
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
    sections: dict[str, dict[str, Any]] = {"restart": restarts_owed(inv), "install": stale_installs(inv)}
    checked = {"restart", "install"}
    if _due(state, "behind", now):
        sections["behind"] = behind()
        ran["behind"] = now
    if _due(state, "copies", now):
        sections["copies"] = current.copies()
        ran["copies"] = now
        checked.add("copies")
    # A section that couldn't run says nothing about what it would have found.
    checked = {s for s in checked if (sections.get(s) or {}).get("status") != "unreachable"}

    found = findings(sections)
    filed: dict[str, dict[str, Any]] = dict(state.get("filed") or {})
    items: list[dict[str, Any]] = []
    for key, f in sorted(found.items()):
        if key in filed:
            items.append({"key": key, "state": "open", "id": filed[key]["id"]})
            continue
        if not enable_mcp or call is None:
            items.append({"key": key, "state": "unfiled", "reason": "WILLOW_BOT_MCP not enabled", "title": f["title"]})
            continue
        try:
            result = call("human_required_enqueue", {"app_id": app, "kind": "review", "priority": "normal",
                                                     "title": f["title"], "summary": f["summary"][:2000],
                                                     "source_ref": f"keep-current:{key}"[:200]})  # fmt: skip
            err = result.get("error") if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001 — not filed; retried next tick
            result, err = None, str(exc)[:300]
        item_id = result.get("id") if isinstance(result, dict) and not err else None
        if err or not item_id:
            items.append({"key": key, "state": "could-not-file", "error": str(err or "no item id")[:300]})
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
            result = call("human_required_resolve", {"app_id": app, "item_id": row["id"], "status": "resolved",
                                                     "note": "keep-current: no longer found"})  # fmt: skip
            err = result.get("error") if isinstance(result, dict) else None
        except Exception as exc:  # noqa: BLE001 — retried next tick
            err = str(exc)[:300]
        if err:
            items.append({"key": key, "state": "resolve-refused", "id": row["id"], "error": str(err)[:300]})
        else:
            items.append({"key": key, "state": "resolved", "id": row["id"]})
            del filed[key]
    _save({"ran": ran, "filed": filed})
    return {
        "event": "steward_keep_current",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ran": sorted(sections),
        "restart_owed": [(o["unit"], o["checkout"]) for o in sections["restart"].get("owed", [])],
        "stale_installs": [(s["venv"], s["checkout"]) for s in sections["install"].get("stale", [])],
        "pull_asked": [(b.get("repo"), b["pull"]) for b in (sections.get("behind") or {}).get("behind", [])],
        "unreachable": {k: v.get("detail") for k, v in sections.items() if v.get("status") == "unreachable"},
        "items": items,
    }
