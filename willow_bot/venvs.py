"""venvs.py — where the venvs are, and how to bring each one inside the bot.

Seal D2 (2026-10-02): "All is the things should live inside the bot. All the
.venv, the one script. ... as long as it's read only, and the final decision
goes to the human." The bot's venvs live under ``$WILLOW_HOME/willow-bot/venvs/``
(``paths.venvs_dir``). A venv anywhere else is *outside* the bot.

``willow-bot-steward venvs [--plan] [--json]``

This module only reads. It finds every venv (a directory holding
``pyvenv.cfg``) under the box and the clone root, says which are inside the
bot, and with ``--plan`` prints, for each one outside, the commands that
recreate it inside. It never runs them: the operator does, then checks, then
removes the old one by hand.

Recreate, never move: a venv is not relocatable. Every script in its
``bin/`` names the old interpreter path in its shebang, so a moved venv
runs the wrong Python or none. The plan builds a fresh venv from what was
asked for in the old one (its editable checkouts, with the extras they were
installed with, and the packages someone installed by name) held to the old
one's versions as constraints: the same versions, without the leftovers a
freeze would copy in. Moving is not upgrading; an upgrade is its own change.

A venv reached through a symlink (``willow-mcp/.venv`` -> somewhere else) is
found through the link and counted once, under its real path, with the link
kept as an alias. Which units run it is read from the running processes
(``/proc/<pid>/cmdline`` of each running user unit), so a unit that starts
it through a launcher script or a symlinked path is still named.

Inside means under ``$WILLOW_HOME/willow-bot/`` or inside a willow-bot
checkout (``one-script/deep_thought.measure_venvs`` draws the same line).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from willow_bot import paths as _paths

#: How deep under each root a ``pyvenv.cfg`` is looked for: ``org/repo/.venv``
#: under the clone root, ``willow-bot/venvs/<name>`` under the box.
MAX_DEPTH = 3
_SKIP = frozenset({".git", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"})
_CHECKOUT_DIRS = (".venv", "venv")


def _roots() -> list[tuple[str, Path]]:
    """``(label, path)`` for each place to look. The box always; the clone
    root when it exists. A box that is not configured is not searched (the
    caller reports that)."""
    from willow_bot import operator_profile

    roots: list[tuple[str, Path]] = []
    try:
        roots.append(("box", _paths.willow_home()))
    except _paths.BoxNotConfigured:
        pass
    try:
        gh = operator_profile.github_root()
    except Exception:  # noqa: BLE001 — a profile that cannot be read is no clone root
        gh = None
    if gh is not None and gh.is_dir():
        roots.append(("github_root", gh))
    return roots


def _bot_homes() -> list[Path]:
    """Where a venv counts as inside the bot: the box's ``willow-bot/`` and
    every willow-bot checkout (the one this code runs from, and any named
    ``willow-bot`` under the clone root)."""
    from willow_bot import operator_profile

    homes: list[Path] = []
    try:
        homes.append(_paths.bot_dir())
    except _paths.BoxNotConfigured:
        pass
    here = Path(__file__).resolve().parents[1]
    if (here / "pyproject.toml").is_file() and (here / "willow_bot").is_dir():
        homes.append(here)
    try:
        gh = operator_profile.github_root()
    except Exception:  # noqa: BLE001
        gh = None
    if gh is not None and gh.is_dir():
        for cand in (gh / "willow-bot", *gh.glob("*/willow-bot")):
            if cand.is_dir():
                homes.append(cand)
    return [h.resolve() for h in homes]


def _walk(root: Path) -> tuple[list[Path], list[str]]:
    """Every venv under ``root`` to ``MAX_DEPTH``, and the dirs that could
    not be listed (so "none found" is never claimed for a dir never read)."""
    found: list[Path] = []
    unreadable: list[str] = []

    def onerror(exc: OSError) -> None:
        unreadable.append(f"{exc.filename}: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        here = Path(dirpath)
        if "pyvenv.cfg" in filenames:
            found.append(here)
            dirnames[:] = []
            continue
        # os.walk never enters a symlinked dir; a venv behind one is still a
        # venv here (willow-mcp/.venv -> .willow/venvs/willow-mcp), so look in.
        found += [here / d for d in dirnames if (here / d).is_symlink() and (here / d / "pyvenv.cfg").is_file()]
        if len(here.relative_to(root).parts) >= MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP)
    return found, unreadable


def _read_cfg(venv: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            out[key.strip()] = val.strip()
    return out


def _python_version(cfg: dict[str, str]) -> str:
    """``3.12.4`` from ``version_info`` (venv) or ``version`` (virtualenv)."""
    return cfg.get("version_info") or cfg.get("version") or ""


def editables(venv: Path) -> list[str]:
    """Local paths installed editable into ``venv``, from each dist's
    ``direct_url.json`` (PEP 610). The plan re-installs these with ``-e``."""
    out: list[str] = []
    for direct in sorted(venv.glob("lib/python*/site-packages/*.dist-info/direct_url.json")):
        try:
            data = json.loads(direct.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (data.get("dir_info") or {}).get("editable"):
            continue
        url = urlparse(str(data.get("url") or ""))
        if url.scheme == "file":
            out.append(unquote(url.path))
    return out


_DIST_INFO = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)\.dist-info$")
#: Installed by the venv itself, never pinned by ``pip-compile`` by default:
#: not drift when a lock does not list them.
_TOOLING = frozenset({"pip", "setuptools", "wheel", "distribute"})
_LOCK_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;\\#]+)")


def _norm(name: str) -> str:
    """PEP 503 name: runs of ``-_.`` are one ``-``, lower-case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _site_packages(venv: Path) -> list[Path]:
    return sorted(venv.glob("lib/python*/site-packages"))


def installed(venv: Path) -> dict[str, Any]:
    """What is installed in ``venv``, read from its ``*.dist-info`` directory
    names (no subprocess, nothing imported). Three states: ``unreachable``
    (no ``site-packages`` or one that cannot be listed: the set is unknown, not
    empty), ``empty`` (listed, no distributions) or ``populated``. ``packages``
    maps normalised name to version; ``editable`` names the ones installed with
    ``-e`` (a lock cannot pin those); ``hash`` is a sha256 prefix over the sorted
    ``name==version`` set, so two venvs (or one venv over time) compare."""
    sites = _site_packages(venv)
    if not sites:
        return {"status": "unreachable", "detail": "no lib/python*/site-packages"}
    packages: dict[str, str] = {}
    editable: list[str] = []
    newest = 0.0
    try:
        for site in sites:
            newest = max(newest, site.stat().st_mtime)
            for entry in sorted(site.iterdir()):
                m = _DIST_INFO.match(entry.name)
                if not m:
                    continue
                name = _norm(m["name"])
                packages[name] = m["version"]
                try:
                    direct = json.loads((entry / "direct_url.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(direct, dict) and (direct.get("dir_info") or {}).get("editable"):
                    editable.append(name)
    except OSError as exc:
        return {"status": "unreachable", "detail": f"{exc.filename}: {exc.strerror}"}
    digest = hashlib.sha256("\n".join(f"{n}=={v}" for n, v in sorted(packages.items())).encode()).hexdigest()
    return {
        "status": "populated" if packages else "empty",
        "count": len(packages),
        "hash": digest[:16],
        "modified": datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packages": packages,
        "editable": sorted(editable),
    }


def lock_path(venv: Path) -> Path:
    """Where the human's lock for ``venv`` goes: ``<locks_dir>/<name>.requirements.lock``."""
    return _paths.locks_dir() / f"{_target_name(venv)}.requirements.lock"


def read_lock(path: Path) -> dict[str, str] | None:
    """The ``name -> version`` pins of a ``pip-compile`` lock, or ``None`` when
    the file is not there. Raises ``OSError`` when it is there and unreadable."""
    if not path.is_file():
        return None
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LOCK_PIN.match(line)
        if m:
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def drift(venv: Path, have: dict[str, Any]) -> dict[str, Any]:
    """Installed-versus-lock for ``venv``. The lock is one the human generates
    (``pip-compile --generate-hashes``); the bot only reads it. No lock is
    ``empty`` with ``drift: None``: never "no drift". An unreadable lock or an
    unreadable install is ``unreachable``, also ``drift: None``."""
    path = lock_path(venv)
    base: dict[str, Any] = {"path": str(path), "drift": None}
    try:
        pins = read_lock(path)
    except (OSError, UnicodeDecodeError) as exc:
        return {**base, "status": "unreachable", "detail": f"lock unreadable: {exc}"[:200]}
    if pins is None:
        return {**base, "status": "empty", "detail": "no lock: generate one with pip-compile --generate-hashes"}
    if not pins:
        return {**base, "status": "empty", "detail": "the lock file pins nothing"}
    if have.get("status") == "unreachable":
        return {**base, "status": "unreachable", "detail": f"installed set unknown: {have.get('detail')}"[:200]}
    pkgs: dict[str, str] = have["packages"]
    skip = set(have["editable"])
    missing = sorted(n for n in pins if n not in pkgs)
    changed = [
        {"name": n, "installed": pkgs[n], "locked": v} for n, v in sorted(pins.items()) if n in pkgs and pkgs[n] != v
    ]
    extra = sorted(n for n in pkgs if n not in pins and n not in skip and n not in _TOOLING)
    return {
        **base,
        "status": "populated",
        "drift": bool(missing or changed or extra),
        "missing": missing,
        "changed": changed,
        "extra": extra,
    }


_REQ_EXTRA = re.compile(r"^Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9._-]*).*extra\s*==\s*['\"]([^'\"]+)['\"]")


def requested(venv: Path) -> list[str]:
    """Distributions someone installed by name (pip's ``REQUESTED`` marker,
    PEP 376), not editable and not pip's own tooling: what the plan asks for
    again. A dependency pulled in by another has no marker."""
    out: list[str] = []
    for site in _site_packages(venv):
        for entry in sorted(site.glob("*.dist-info")):
            m = _DIST_INFO.match(entry.name)
            if not m or not (entry / "REQUESTED").is_file():
                continue
            if (entry / "direct_url.json").is_file():
                try:
                    direct = json.loads((entry / "direct_url.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    direct = {}
                if (direct.get("dir_info") or {}).get("editable"):
                    continue
            name = _norm(m["name"])
            if name not in _TOOLING:
                out.append(name)
    return sorted(set(out))


def extras_of(venv: Path, checkout: str) -> list[str]:
    """The extras ``checkout`` was installed editable with, as far as the
    venv shows: an extra whose every requirement is installed. pip records no
    extras, so this is read from what is there, and an extra with no
    requirements of its own is never counted."""
    have = installed(venv).get("packages") or {}
    for site in _site_packages(venv):
        for entry in site.glob("*.dist-info"):
            try:
                direct = json.loads((entry / "direct_url.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if unquote(urlparse(str(direct.get("url") or "")).path) != checkout:
                continue
            try:
                meta = (entry / "METADATA").read_text(encoding="utf-8")
            except OSError:
                return []
            needs: dict[str, set[str]] = {}
            for line in meta.splitlines():
                m = _REQ_EXTRA.match(line)
                if m:
                    needs.setdefault(m.group(2), set()).add(_norm(m.group(1)))
            return sorted(x for x, names in needs.items() if names and all(n in have for n in names))
    return []


def _main_pids() -> dict[str, int]:
    """Each running systemd user service and its main pid. Raises ``OSError``
    when ``systemctl`` can't be asked (no user session): then who runs a venv
    is unknown, never "nobody"."""
    got = subprocess.run(
        ["systemctl", "--user", "show", "--type=service", "--state=running", "--property=Id,MainPID", "*"],
        capture_output=True, text=True, timeout=20, check=False,
    )  # fmt: skip
    if got.returncode != 0:
        raise OSError(got.stderr.strip() or f"systemctl exited {got.returncode}")
    pids: dict[str, int] = {}
    for block in got.stdout.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id") and props.get("MainPID", "0").isdigit() and int(props["MainPID"]):
            pids[props["Id"]] = int(props["MainPID"])
    return pids


def _cmdline(pid: int) -> list[str]:
    try:
        return [a for a in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        return []


def running() -> dict[str, Any]:
    """Which running user units run from which venv: ``{"status", "venvs":
    {real venv path: [unit, ...]}}``. A process runs from a venv when an
    argument of its command line is under that venv's ``bin/`` (the venv's own
    python, or an entry point whose shebang names it). ``unreachable`` when
    the units can't be listed."""
    try:
        pids = _main_pids()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unreachable", "detail": str(exc)[:200], "venvs": {}}
    out: dict[str, list[str]] = {}
    for unit, pid in sorted(pids.items()):
        for arg in _cmdline(pid):
            path = Path(arg)
            if path.parent.name != "bin" or not (path.parent.parent / "pyvenv.cfg").is_file():
                continue
            key = str(path.parent.parent.resolve())
            if unit not in out.setdefault(key, []):
                out[key].append(unit)
            break
    return {"status": "populated" if out else "empty", "venvs": out}


def _target_name(venv: Path) -> str:
    """``<checkout>/.venv`` is named for its checkout; anything else keeps
    its own name."""
    return venv.parent.name if venv.name in _CHECKOUT_DIRS else venv.name


def inventory() -> dict[str, Any]:
    """Every venv found, inside or outside the bot. Three states, like
    ``status``: ``no-box`` when the box is not configured, else
    ``populated`` / ``empty``; ``unreadable`` lists dirs it could not list."""
    try:
        home = _paths.venvs_dir()
    except _paths.BoxNotConfigured as exc:
        return {"status": "no-box", "detail": str(exc)}
    homes = _bot_homes()
    seen: dict[Path, dict[str, Any]] = {}
    unreadable: list[str] = []
    for label, root in _roots():
        found, bad = _walk(root)
        unreadable += bad
        for venv in found:
            key = venv.resolve()
            if key in seen:
                if venv != key and str(venv) not in seen[key]["aliases"]:
                    seen[key]["aliases"].append(str(venv))
                continue
            cfg = _read_cfg(key)
            inside = any(key.is_relative_to(h) for h in homes)
            have = installed(key)
            seen[key] = {
                "path": str(key),
                "aliases": [str(venv)] if venv != key else [],
                "root": label,
                "inside": inside,
                "python": _python_version(cfg),
                "editables": editables(key),
                "installed": {k: v for k, v in have.items() if k not in ("packages", "editable")},
                # A lock is the bot's own venvs' business; an outside venv (the
                # broker's among them) is not checked, and says so.
                "lock": drift(key, have)
                if inside
                else {"status": "unchecked", "drift": None, "detail": "outside the bot: not ours to lock"},
            }
    runs = running()
    for key, v in seen.items():
        if runs["status"] == "unreachable":
            v["runs"] = {"status": "unreachable", "detail": runs["detail"], "units": []}
        else:
            units = runs["venvs"].get(str(key), [])
            v["runs"] = {"status": "populated" if units else "empty", "units": units}
    venvs = sorted(seen.values(), key=lambda v: (v["inside"], v["path"]))
    outside = [v for v in venvs if not v["inside"]]
    return {
        "status": "populated" if venvs else "empty",
        "home": str(home),
        "inside": sum(1 for v in venvs if v["inside"]),
        "outside": len(outside),
        "venvs": venvs,
        "unreadable": unreadable,
    }


def _unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config") / "systemd" / "user"


def references(*names: str) -> list[str]:
    """Installed systemd user units whose text names any of ``names`` (a
    venv's real path and each symlink to it). Read-only: the operator
    re-renders them (``scripts/install-service.sh``)."""
    hits: list[str] = []
    unit_dir = _unit_dir()
    if not unit_dir.is_dir():
        return hits
    for unit in sorted(unit_dir.glob("*.service")):
        try:
            text = unit.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(n and n in text for n in names):
            hits.append(str(unit))
    return hits


def plan(inv: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """For each venv outside the bot: the commands that recreate it inside,
    in order. Nothing here runs them."""
    inv = inventory() if inv is None else inv
    if inv.get("status") == "no-box":
        return []
    home = Path(inv["home"])
    steps: list[dict[str, Any]] = []
    landing: dict[str, list[str]] = {}
    for v in inv["venvs"]:
        if not v["inside"]:
            landing.setdefault(_target_name(Path(v["path"])), []).append(v["path"])
    for v in inv["venvs"]:
        if v["inside"]:
            continue
        old = Path(v["path"])
        target = home / _target_name(old)
        major_minor = ".".join(v["python"].split(".")[:2])
        py = f"python{major_minor}" if major_minor else "python3"
        held = target / "constraints.from-old.txt"
        # What was asked for, held to the versions it has: the same versions,
        # no leftovers. Upgrading is a separate change, never part of a move.
        asks = [f"'{p}[{','.join(x)}]'" if (x := extras_of(old, p)) else f"'{p}'" for p in v["editables"]]
        named = requested(old)
        cmds = [
            f"{py} -m venv {target}",
            f"{old}/bin/pip list --format=freeze --exclude-editable > {held}",
        ]
        if named:
            cmds.append(f"{target}/bin/pip install -c {held} {' '.join(named)}")
        cmds += [f"{target}/bin/pip install -c {held} -e {a}" for a in asks]
        cmds.append(f"{target}/bin/pip check")
        units = references(str(old), *v.get("aliases", []))
        running_units = (v.get("runs") or {}).get("units", [])
        notes = []
        if target.exists():
            notes.append(f"{target} already exists: check it before reusing the name")
        others = [p for p in landing.get(target.name, []) if p != str(old)]
        if others:
            notes.append(f"{', '.join(others)} would land at {target} too: decide whether they become one"
                         " venv (and which set wins) before recreating either")  # fmt: skip
        if not named and not asks:
            notes.append("nothing in it was installed by name or editable: say what it is for before recreating it")
        if any(Path(p).name == "willow-bot" for p in v["editables"]):
            cmds.append(f"{target}/bin/python -c 'import willow_bot'")
        if running_units and not units:
            notes.append(f"run by {', '.join(running_units)} through a path no unit file names"
                         " (a launcher script or a symlink): repoint that, too")  # fmt: skip
        steps.append({
            "old": str(old),
            "new": str(target),
            "python": v["python"] or "unknown",
            "runs": running_units,
            "commands": cmds,
            "repoint": [f"{u} names the old path: re-render it "
                        f"(WILLOW_BOT_VENV_BIN={target}/bin scripts/install-service.sh <unit>)"
                        for u in units],
            "then": f"once everything runs from {target}: remove {old} by hand",
            "notes": notes,
        })
    return steps


def _lock_word(lock: dict[str, Any]) -> str:
    if lock["status"] == "populated":
        return "DRIFT" if lock["drift"] else "matches"
    return {"empty": "none", "unreachable": "unknown", "unchecked": "-"}.get(lock["status"], lock["status"])


def _print_text(inv: dict[str, Any], steps: list[dict[str, Any]] | None) -> None:
    if inv.get("status") == "no-box":
        print(f"venvs: no box — {inv['detail']}")
        return
    print(f"venvs: {inv['inside']} inside the bot, {inv['outside']} outside  (home: {inv['home']})")
    for v in inv["venvs"]:
        where = "inside " if v["inside"] else "OUTSIDE"
        have = v["installed"]
        pkgs = f"{have['count']} pkgs #{have['hash']}" if "hash" in have else f"packages {have['status']}"
        runs = v.get("runs") or {}
        ran = ", ".join(runs.get("units") or []) or ("unknown" if runs.get("status") == "unreachable" else "nothing")
        print(f"  {where}  {v['path']}  python {v['python'] or '?'}  {pkgs}  lock: {_lock_word(v['lock'])}  runs: {ran}")
        for alias in v.get("aliases") or []:
            print(f"           also {alias}")
    for bad in inv["unreadable"]:
        print(f"  could not read {bad}")
    if steps is None:
        if inv["outside"]:
            print("D2: all the .venv live in the bot. `willow-bot-steward venvs --plan` prints the way in.")
        return
    if not steps:
        print("Nothing to move: every venv found is inside the bot.")
        return
    print("\nPlan (recreate, never move: a venv's scripts name its old path). Nothing has been run.")
    for s in steps:
        print(f"\n# {s['old']}  ->  {s['new']}   (python {s['python']})")
        for c in s["commands"]:
            print(f"  {c}")
        for r in s["repoint"]:
            print(f"  # {r}")
        for n in s["notes"]:
            print(f"  # note: {n}")
        print(f"  # {s['then']}")


def main(argv: list[str] | None = None) -> int:
    """``willow-bot-steward venvs [--plan] [--json]``."""
    args = list(argv if argv is not None else sys.argv[1:])
    unknown = [a for a in args if a not in ("--plan", "--json")]
    if unknown:
        print("usage: willow-bot-steward venvs [--plan] [--json]", file=sys.stderr)
        return 2
    inv = inventory()
    steps = plan(inv) if "--plan" in args else None
    if "--json" in args:
        out: dict[str, Any] = dict(inv)
        if steps is not None:
            out["plan"] = steps
        print(json.dumps(out, indent=1))
    else:
        _print_text(inv, steps)
    return 1 if inv.get("status") == "no-box" else 0
