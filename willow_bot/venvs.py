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
runs the wrong Python or none. The plan builds a fresh venv from the old
one's package list and editable checkouts instead.

Inside means under ``$WILLOW_HOME/willow-bot/`` or inside a willow-bot
checkout (``one-script/deep_thought.measure_venvs`` draws the same line).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
                continue
            cfg = _read_cfg(venv)
            inside = any(key.is_relative_to(h) for h in homes)
            have = installed(key)
            seen[key] = {
                "path": str(key),
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


def references(old: str) -> list[str]:
    """Installed systemd user units whose text names ``old``. Read-only: the
    operator re-renders them (``scripts/install-service.sh``)."""
    hits: list[str] = []
    unit_dir = _unit_dir()
    if not unit_dir.is_dir():
        return hits
    for unit in sorted(unit_dir.glob("*.service")):
        try:
            if old in unit.read_text(encoding="utf-8"):
                hits.append(str(unit))
        except OSError:
            continue
    return hits


def plan(inv: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """For each venv outside the bot: the commands that recreate it inside,
    in order. Nothing here runs them."""
    inv = inventory() if inv is None else inv
    if inv.get("status") == "no-box":
        return []
    home = Path(inv["home"])
    steps: list[dict[str, Any]] = []
    for v in inv["venvs"]:
        if v["inside"]:
            continue
        old = Path(v["path"])
        target = home / _target_name(old)
        major_minor = ".".join(v["python"].split(".")[:2])
        py = f"python{major_minor}" if major_minor else "python3"
        reqs = target / "requirements.from-old.txt"
        cmds = [
            f"{py} -m venv {target}",
            f"{old}/bin/pip list --format=freeze --exclude-editable > {reqs}",
            f"{target}/bin/pip install -r {reqs}",
        ]
        cmds += [f"{target}/bin/pip install -e '{path}'  # add the extras it had, e.g. [dev,seat]"
                 for path in v["editables"]]
        cmds.append(f"{target}/bin/pip check")
        units = references(str(old))
        notes = []
        if target.exists():
            notes.append(f"{target} already exists: check it before reusing the name")
        if any(Path(p).name == "willow-bot" for p in v["editables"]):
            cmds.append(f"{target}/bin/python -c 'import willow_bot'")
        steps.append({
            "old": str(old),
            "new": str(target),
            "python": v["python"] or "unknown",
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
        print(f"  {where}  {v['path']}  python {v['python'] or '?'}  {pkgs}  lock: {_lock_word(v['lock'])}")
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
