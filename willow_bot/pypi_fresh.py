"""pypi_fresh.py — each venv's packages against PyPI.

``willow-bot-steward pypi [--json]``

For every venv ``venvs`` finds, and every package in it that isn't
installed editable (those follow their checkout, not PyPI), this asks PyPI's
JSON API what it knows: the installed release's known vulnerabilities and
whether it was yanked, and every release since with its upload date. Each
package is then one of:

- ``vulnerable`` — the installed release has a known vulnerability; ``fix``
  is the smallest release that fixes all of them, and ``fix_in_range`` says
  whether the venv's own requirements allow it (no cooldown: a fix waits
  for nothing).
- ``yanked`` — the installed release was withdrawn.
- ``pinned`` — something in the venv requires it with ``==``: a pin made on
  purpose (a CI ruff, an exact runtime pin). Never an upgrade candidate;
  still ``vulnerable`` or ``yanked`` when it is.
- ``patch`` — a newer release in the same major.minor, inside every range
  the venv declares, at least ``COOLDOWN_DAYS`` old.
- ``minor`` / ``major`` — a newer one beyond that: the person's to decide.
- ``current`` — none of the above.

Pre-releases are never candidates. A venv's "declared" ranges are the
``Requires-Dist`` lines of everything installed in it. A package PyPI
couldn't be asked about is ``unreachable``, never ``current``. It reads
only; nothing is installed.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from willow_bot import venvs

COOLDOWN_DAYS = 7
_REQ = re.compile(r"^Requires-Dist:\s*(.+)$")


def _fetch_json(url: str) -> dict[str, Any]:
    """GET a PyPI JSON page. Module-level so tests keep it off the network."""
    import requests

    r = requests.get(url, timeout=20, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise TypeError(f"unexpected answer from {url}")
    return data


class _PyPI:
    """PyPI's answers, each asked once per run."""

    def __init__(self) -> None:
        self.projects: dict[str, Any] = {}
        self.releases: dict[tuple[str, str], Any] = {}

    def project(self, name: str) -> dict[str, Any]:
        if name not in self.projects:
            try:
                self.projects[name] = _fetch_json(f"https://pypi.org/pypi/{name}/json")
            except Exception as exc:  # noqa: BLE001 — unreachable, per package
                self.projects[name] = exc
        got = self.projects[name]
        if isinstance(got, Exception):
            raise got
        return got

    def release(self, name: str, version: str) -> dict[str, Any]:
        key = (name, version)
        if key not in self.releases:
            try:
                self.releases[key] = _fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")
            except Exception as exc:  # noqa: BLE001
                self.releases[key] = exc
        got = self.releases[key]
        if isinstance(got, Exception):
            raise got
        return got


def declared(venv: Path) -> dict[str, list[tuple[str, str]]]:
    """``{package: [(dependent, specifier), ...]}`` from every installed
    distribution's ``Requires-Dist``. A requirement behind an extra counts
    too: a range is honoured whether or not the extra is in use."""
    out: dict[str, list[tuple[str, str]]] = {}
    for meta in sorted(venv.glob("lib/python*/site-packages/*.dist-info/METADATA")):
        dependent = meta.parent.name.split("-", 1)[0]
        try:
            text = meta.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            m = _REQ.match(line)
            if not m:
                continue
            try:
                req = Requirement(m.group(1))
            except InvalidRequirement:
                continue
            out.setdefault(venvs._norm(req.name), []).append((dependent, str(req.specifier)))
    return out


def _uploaded(files: list[dict[str, Any]]) -> datetime | None:
    times = [f.get("upload_time_iso_8601") or f.get("upload_time") for f in files]
    times = [t for t in times if t]
    if not times:
        return None
    return min(datetime.fromisoformat(str(t).replace("Z", "+00:00")).replace(tzinfo=timezone.utc) for t in times)


def classify(name: str, installed: str, specs: list[tuple[str, str]], project: dict[str, Any],
             release: dict[str, Any], now: datetime) -> dict[str, Any]:  # fmt: skip
    """One package against PyPI. See the module docstring for the kinds."""
    row: dict[str, Any] = {"name": name, "installed": installed, "status": "populated"}
    try:
        have = Version(installed)
    except InvalidVersion:
        return {**row, "kind": "current", "detail": "a version PyPI's rules can't read"}
    ranges = [SpecifierSet(s) for _, s in specs if s]
    row["pinned_by"] = sorted({d for d, s in specs if _exact(s)})
    vulns = release.get("vulnerabilities") or []
    row["vulnerabilities"] = [v.get("id") for v in vulns]
    row["yanked"] = bool((release.get("info") or {}).get("yanked"))
    releases: list[tuple[Version, datetime | None]] = []
    for raw, files in (project.get("releases") or {}).items():
        try:
            v = Version(raw)
        except InvalidVersion:
            continue
        if v.is_prerelease or not files or all(f.get("yanked") for f in files):
            continue
        releases.append((v, _uploaded(files)))
    in_range = lambda v: all(r.contains(v, prereleases=False) for r in ranges)
    if vulns:
        floors = []
        for vuln in vulns:
            fixed = [Version(f) for f in vuln.get("fixed_in") or [] if _ok(f)]
            if fixed:
                floors.append(min(fixed))
        need = max(floors) if floors else None
        fixes = sorted(v for v, _ in releases if need is not None and v >= need and v > have)
        row["fix"] = str(fixes[0]) if fixes else None
        row["fix_in_range"] = bool(fixes and in_range(fixes[0]))
        return {**row, "kind": "vulnerable"}
    if row["yanked"]:
        return {**row, "kind": "yanked"}
    if row["pinned_by"]:
        return {**row, "kind": "pinned"}
    old_enough = [v for v, when in releases if v > have and when is not None and now - when >= timedelta(days=COOLDOWN_DAYS)]
    patches = [v for v in old_enough if v.release[:2] == have.release[:2] and in_range(v)]
    if patches:
        return {**row, "kind": "patch", "to": str(max(patches))}
    minors = [v for v in old_enough if v.major == have.major]
    if minors:
        return {**row, "kind": "minor", "to": str(max(minors)), "in_range": in_range(max(minors))}
    if old_enough:
        return {**row, "kind": "major", "to": str(max(old_enough)), "in_range": in_range(max(old_enough))}
    return {**row, "kind": "current"}


def _exact(spec: str) -> bool:
    """A pin made on purpose: ``==1.2.3`` or ``===x``, never ``==2.*`` (a range)."""
    return any(sp.operator == "===" or (sp.operator == "==" and not sp.version.endswith(".*"))
               for sp in SpecifierSet(spec))  # fmt: skip


def _ok(raw: str) -> bool:
    try:
        Version(raw)
    except InvalidVersion:
        return False
    return True


def venv_report(venv: Path, pypi: _PyPI, now: datetime) -> dict[str, Any]:
    """Every non-editable package in ``venv`` against PyPI."""
    have = venvs.installed(venv)
    if have.get("status") == "unreachable":
        return {"path": str(venv), "status": "unreachable", "detail": have.get("detail")}
    specs = declared(venv)
    skip = set(have.get("editable") or []) | set(venvs._TOOLING)
    names = sorted(n for n in have.get("packages", {}) if n not in skip)

    def one(name: str) -> dict[str, Any]:
        version = have["packages"][name]
        try:
            project = pypi.project(name)
            release = pypi.release(name, version)
        except Exception as exc:  # noqa: BLE001 — not on PyPI, or PyPI down: unknown, never current
            return {"name": name, "installed": version, "status": "unreachable", "detail": str(exc)[:200]}
        return classify(name, version, specs.get(name, []), project, release, now)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, names))
    counts: dict[str, int] = {}
    for r in rows:
        k = r.get("kind") or r["status"]
        counts[k] = counts.get(k, 0) + 1
    return {"path": str(venv), "status": "populated" if rows else "empty", "counts": counts,
            "packages": [r for r in rows if r.get("kind") not in ("current",)]}  # fmt: skip


def report(*, now: datetime | None = None) -> dict[str, Any]:
    """Every venv ``venvs`` finds, against PyPI."""
    inv = venvs.inventory()
    if inv.get("status") == "no-box":
        return {"status": "unreachable", "detail": inv.get("detail")}
    pypi = _PyPI()
    now = now or datetime.now(timezone.utc)
    rows = [venv_report(Path(v["path"]), pypi, now) for v in inv.get("venvs", [])]
    return {"status": "populated" if rows else "empty", "venvs": rows}


def _print_text(rep: dict[str, Any]) -> None:
    if rep.get("status") == "unreachable":
        print(f"pypi: unknown — {rep['detail']}")
        return
    for v in rep["venvs"]:
        counts = ", ".join(f"{n} {k}" for k, n in sorted(v.get("counts", {}).items()))
        print(f"{v['path']}: {counts or v['status']}")
        for p in v.get("packages", []):
            if p.get("kind") == "vulnerable":
                where = "in range" if p.get("fix_in_range") else "OUTSIDE the declared range"
                print(f"  VULNERABLE {p['name']} {p['installed']} -> {p.get('fix') or 'no fix yet'} ({where}): "
                      f"{', '.join(p['vulnerabilities'])}")  # fmt: skip
            elif p.get("kind") in ("yanked", "patch"):
                print(f"  {p['kind']:10} {p['name']} {p['installed']}" + (f" -> {p['to']}" if p.get("to") else ""))


def main(argv: list[str] | None = None) -> int:
    """``willow-bot-steward pypi [--json]``."""
    args = list(argv if argv is not None else sys.argv[1:])
    if [a for a in args if a != "--json"]:
        print("usage: willow-bot-steward pypi [--json]", file=sys.stderr)
        return 2
    rep = report()
    if "--json" in args:
        print(json.dumps(rep, indent=1))
    else:
        _print_text(rep)
    return 0
