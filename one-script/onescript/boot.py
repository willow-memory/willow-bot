"""1 boot — check-in. The record first, then state, then attack the gate, then egress.

Cites: CONST-I (identity is the manifest), CONST-V (reserved decisions need the
human key), CONST-III (the egress index), App. B (adversarial probes through the
real path: a gate's own report about itself is not evidence).

Any change without a recorded cause is a hard close: report, options, wait.
If any probe gets through, the box does not open at all.

The four gates (tests, toolchain, freshness, reachability) report on the
Appendix A scale, and every failing row says why. They run at check-in and
again at check-out. Each takes an injectable runner, so the gates are tested
without the tools they check.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import gate, reverse

OPTIONS = [
    "put it back and continue",
    "keep it as a standing grant",
    "leave it, block it until you decide",
    "stop here",
]


class BoxWontOpen(Exception):
    """A probe got through the gate. Nothing runs until a human looks.
    Carries every probe's result, held or not, so the record shows them all."""

    def __init__(self, msg: str, probes: list[dict]):
        super().__init__(msg)
        self.probes = probes


def probes(keys: dict, law: dict) -> list[dict]:
    """The demo stories' attacks, every morning, through the real door."""
    out = []

    try:
        gate.verify({"who": "assistant", "family": "copied", "sig": "forged"}, keys)
        out.append({"probe": "unsigned identity", "held": False, "got": "verified"})
    except gate.Refused as e:
        out.append({"probe": "unsigned identity", "held": True, "got": str(e)})

    for name, change, must_not in (
        (
            "self-awarded seal",
            {"kind": "edit", "standing": "sealed", "cites": ["CONST-IV"]},
            "pass",
        ),
        ("write to the law", {"kind": "law", "none_because": "probe"}, "pass"),
        (
            "egress with no grant",
            {
                "kind": "egress",
                "who": "probe",
                "what": "probe",
                "where": "probe.invalid",
                "bytes": 1,
                "cites": ["CONST-III"],
            },
            "pass",
        ),
    ):
        d = gate.door(change, law)
        out.append({"probe": name, "held": d.verdict != must_not, "got": d.verdict})
    return out


def _row(gate_name: str, where: str, verdict: str, why: str = "", **extra) -> dict:
    return {"gate": gate_name, "where": where, "verdict": verdict, "why": why, **extra}


def _run(argv: list[str], cwd: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    lines = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode, lines[-1] if lines else ""


def tests_gate(suites: list[dict], runner=_run) -> list[dict]:
    """Run each suite. Pass, fail, or can't run, with why."""
    out = []
    for s in sorted(suites, key=lambda s: s["name"]):
        missing = [m for m in s.get("needs", []) if importlib.util.find_spec(m) is None]
        if missing:
            out.append(
                _row("tests", s["name"], "failing", f"can't run: needs {missing}")
            )
        elif not Path(s["cwd"]).is_dir():
            out.append(
                _row("tests", s["name"], "failing", "can't run: no such directory")
            )
        else:
            rc, last = runner(s["argv"], s["cwd"])
            out.append(
                _row("tests", s["name"], "satisfied", "", result=last)
                if rc == 0
                else _row("tests", s["name"], "failing", f"failed: {last}")
            )
    return out


def _tool_version(tool: str) -> str | None:
    exe = shutil.which(tool)
    if exe is None:
        return None
    _, last = _run([exe, "--version"], ".", timeout=30)
    m = re.search(r"\d+\.\d+(?:\.\d+)?", last)
    return m.group(0) if m else last


def toolchain_gate(
    pins: dict,
    version_of=_tool_version,
    py: str | None = None,
    found_in: str = "on PATH",
) -> list:
    """Installed versions against the repo's pins, and whether a venv is active.
    `found_in` says where `version_of` looked, so the why names the right place."""
    out = []
    for tool, want in sorted(pins.get("tools", {}).items()):
        have = version_of(tool)
        if have is None:
            out.append(
                _row("toolchain", tool, "failing", f"not installed; pinned {want}")
            )
        elif have != want:
            out.append(
                _row("toolchain", tool, "failing", f"{have} {found_in}; pinned {want}")
            )
        else:
            out.append(_row("toolchain", tool, "satisfied"))
    py = py or f"{sys.version_info.major}.{sys.version_info.minor}"
    if "python" in pins:
        ok = py in pins["python"]
        out.append(
            _row(
                "toolchain",
                "python",
                "satisfied" if ok else "failing",
                "" if ok else f"{py}; supported {pins['python']}",
            )
        )
    if pins.get("venv"):
        in_venv = sys.prefix != sys.base_prefix
        out.append(
            _row(
                "toolchain",
                "venv",
                "satisfied" if in_venv else "differently",
                "" if in_venv else "the system interpreter, not a venv",
            )
        )
    return out


def _git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def freshness_gate(
    repos: list[str], docs: list[dict], law_version: str, git=_git
) -> list[dict]:
    """Clones against their last-fetched remote (fetching is egress, so it isn't
    done here), and documents against the law they were built on."""
    out = []
    for repo in sorted(repos):
        if not git(repo, "rev-parse", "@{u}"):
            out.append(_row("freshness", repo, "differently", "no upstream to compare"))
            continue
        counts = git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")
        ahead, behind = (int(x) for x in (counts.split() or ["0", "0"]))
        out.append(
            _row("freshness", repo, "failing", f"{behind} commit(s) behind its remote")
            if behind
            else _row("freshness", repo, "satisfied", f"{ahead} ahead" if ahead else "")
        )
    for d in sorted(docs, key=lambda d: d["where"]):
        ok = d["built_on"] == law_version
        out.append(
            _row(
                "freshness",
                d["where"],
                "satisfied" if ok else "failing",
                ""
                if ok
                else f"built on Draft {d['built_on']}; the law is {law_version}",
            )
        )
    return out


def reachability_gate(deps: list[dict]) -> list[dict]:
    """Three states, never collapsed: populated, empty, unreachable."""
    out = []
    for d in sorted(deps, key=lambda d: d["name"]):
        try:
            got = d["probe"]()
        except Exception as e:
            out.append(
                _row(
                    "reachability",
                    d["name"],
                    "failing",
                    f"unreachable: {type(e).__name__}: {e}",
                    state="unreachable",
                )
            )
            continue
        out.append(
            _row("reachability", d["name"], "satisfied", "", state="populated")
            if got
            else _row(
                "reachability",
                d["name"],
                "differently",
                "reachable, empty",
                state="empty",
            )
        )
    return out


def gates(cfg: dict) -> list[dict]:
    """The four gates, in a fixed order. `cfg` keys are all optional."""
    return (
        tests_gate(cfg.get("tests", []), cfg.get("runner", _run))
        + toolchain_gate(
            cfg.get("pins", {}),
            cfg.get("version_of", _tool_version),
            cfg.get("python"),
            cfg.get("found_in", "on PATH"),
        )
        + freshness_gate(
            cfg.get("repos", []),
            cfg.get("docs", []),
            cfg.get("law_version", ""),
            cfg.get("git", _git),
        )
        + reachability_gate(cfg.get("deps", []))
    )


def boot(rec, keys: dict, law: dict, gate_cfg: dict | None = None) -> dict:
    rows = rec.rows()
    lines: list[str] = []

    for b in rec.verify_chain():  # B1: the record holds?
        lines.append(f"record: {b}")
    lines += rec.verify_anchor(keys.get(gate.HUMAN))  # ...and reaches the sealed tip?
    for t in reverse.open_turns(rows):  # a crash left a row
        intent = next(
            r["intent"] for r in rows if r["kind"] == "turn_open" and r["turn"] == t
        )
        lines.append(f"turn {t} never closed; it was doing: {intent}")
    for i in reverse.three_way(rec.box, rec.pile(), rows):  # B2: state vs last close
        if i["verdict"] == "failing":
            lines.append(f"{i['where']}: {i['why']}")

    held = probes(keys, law)  # App. B, every morning
    broke = [p for p in held if not p["held"]]
    if broke:
        raise BoxWontOpen(f"probes got through: {[p['probe'] for p in broke]}", held)

    checked = gates(gate_cfg or {})  # the four gates
    lines += [
        f"{r['gate']}: {r['where']}: {r['why']}"
        for r in checked
        if r["verdict"] == "failing"
    ]

    # Opus P5: a clause with no verdict is not a satisfied clause. If no
    # reconcile has ever run, enforcement status is unknown, and says so.
    last = [r for r in rows if r["kind"] == "reconcile"]
    report = {"state": "current", "at": last[-1]["ts"]} if last else {"state": "never"}

    egress = sorted(f"{g['who']} -> {g['where']}" for g in law.get("grants", []))  # B3
    return {
        "report": report,
        "gates": checked,
        "hard_close": bool(lines),
        "lines": lines,
        "options": OPTIONS if lines else [],
        "probes": held,
        "egress": egress,
        "anchor": rec.anchor_state(),
    }
