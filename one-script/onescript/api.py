"""api — the one script as functions the app calls. No terminal, no tty.

    cfg = Config(box=..., keys=..., constitution=..., ci=..., root=..., grove=...,
                 venv=...)
    checkin(cfg)                      # boot: record, probes, the four gates
    scope(cfg, by=("who",))           # code proposes a stack; the card and subject
    seal_scope(cfg, subject, ...)     # the human's seal over that exact set
    serve(cfg, spec, max_chars=N)     # write the one file the model reads
    take_proposals(cfg, rows)         # the model's rows in, judged, nothing written
    seal_proposal(cfg, subject, ...)  # the human's seal; a sealed pass is written
    checkout(cfg)                     # reverse, then the morning screen

`__main__` is a thin wrapper over these. Every function opens the box fresh,
writes its `invocation` row, and returns plain data (dicts, lists, strings), so
the app can serialise it. A refusal comes back as {"refused": why, "code": n}
and is never an exception: the box wasn't opened, or the state doesn't allow it.

The record is the only state between calls. The serve key, which makes the
session's ids, is the one thing the record must not hold; it is kept beside the
keys (`serve.key`, 0600), made fresh at each check-in, and is never the seal
key.

Seals without a terminal: a seal is either the human's HMAC proof over the
subject (needs a key in the keys file) or a sealed Nestor pair whose conclusion
is the exact subject (`nestor_seal`), read from a pair the caller hands over or
from Nestor's store, opened read-only. The app uses the second.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import boot, gate, nestor_seal, record
from . import serve as served
from .run import PKG, Run

DESK = ("desk", "claude")
ANCHOR = "anchor.json"  # the sealed tip, beside the keys: outside the box
SERVE_KEY = "serve.key"  # this session's id key, beside the keys: outside the box

#: Set in the tests gate's child. A checkin started under it never runs the
#: tests gate again: the gate runs the suite, the suite runs checkin, and an
#: unguarded loop forked until the box ran out of memory (2026-10-06, Kart
#: Z7X2TJQX, a mutant with the keys refusal removed).
NESTED = "ONESCRIPT_IN_TESTS_GATE"

_STAMP = {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}


@dataclass(frozen=True)
class Config:
    box: Path
    keys: Path
    constitution: Path
    ci: Path
    root: Path
    grove: Path
    venv: Path
    now: str | None = None  # a fixed clock, for replays and tests
    no_tests: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KeysExposed(Exception):
    """A keys file anyone but the owner can read is refused, not used."""


def _keys(path: Path) -> dict[str, bytes]:
    """The signing secrets live beside the box, never in it: the record's own
    three-way check flags any file in the box the run didn't write."""
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({DESK[0]: secrets.token_hex(32)}, f)
    if path.stat().st_mode & 0o077:
        raise KeysExposed(f"{path} is readable beyond its owner; chmod 600 it")
    return {k: bytes.fromhex(v) for k, v in json.loads(path.read_text()).items()}


def _law(text: str) -> dict:
    """Trace IDs as the constitution writes them: CONST-0, CONST-0-1, CONST-I-1."""
    ids = sorted(set(re.findall(r"CONST-(?:0|[IVXL]+)(?:-\d+)*", text)))
    return {"trace_ids": ids, "grants": []}


def _save_serve_key(cfg: Config, key: bytes | None) -> None:
    path = cfg.keys.parent / SERVE_KEY
    if key is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.hex())
    path.chmod(0o600)


def _load_serve_key(cfg: Config) -> bytes | None:
    path = cfg.keys.parent / SERVE_KEY
    try:
        if path.stat().st_mode & 0o077:
            return None  # a key others can read isn't a key
        return bytes.fromhex(path.read_text().strip()) or None
    except (OSError, ValueError):
        return None


def _last_boot(rows: list[dict]) -> dict | None:
    """The boot report as the record holds it. A hard close outlives the
    invocation that found it: nothing in memory carries it to the next one."""
    i = next(
        (k for k in range(len(rows) - 1, -1, -1) if rows[k]["kind"] == "boot"), None
    )
    if i is None:
        return None
    b = rows[i]
    before = [r for r in rows[:i] if r["kind"] == "reconcile"]
    return {
        "hard_close": b["hard_close"],
        "lines": b["lines"],
        "options": b.get("options", boot.OPTIONS if b["hard_close"] else []),
        # as boot() wrote it: the last reconcile before this check-in, and when
        "report": {"state": "current", "at": before[-1]["ts"]}
        if before
        else {"state": "never"},
        # a checkout after this check-in ends the run; a turn needs a new one
        "checked_out": any(r["kind"] == "reconcile" for r in rows[i + 1 :]),
        "probes": b.get("probes", []),
        "gates": b.get("gates", []),
        "egress": b.get("egress", []),
        "anchor": b.get("anchor"),
    }


def _from_record(run: Run) -> None:
    """Grades, claims and acts back from the record, for a checkout that runs
    in a different invocation from the turns that made them."""
    rows = run.rec.rows()
    run.graded = [
        {k: v for k, v in r.items() if k not in _STAMP | {"turn"}}
        for r in rows
        if r["kind"] == "grade"
    ]
    run.claims = [c for r in rows if r["kind"] == "claims" for c in r["claims"]]
    run.acts = [
        {"kind": r["act_kind"], **{k: r[k] for k in ("verdict", "reason", "card")}}
        for r in rows
        if r["kind"] == "act"
    ]


def _in_venv(venv: Path):
    """The toolchain gate reads the bot's venv, where the one script's tools
    live (D2), never whatever happens to be first on PATH."""

    def version_of(tool: str) -> str | None:
        exe = venv / "bin" / tool
        if not exe.exists():
            return None
        _, last = boot._run([str(exe), "--version"], ".", timeout=30)
        m = re.search(r"\d+\.\d+(?:\.\d+)?", last)
        return m.group(0) if m else last

    return version_of


def _gate_cfg(
    no_tests: bool, ci_text: str, venv: Path, root: Path, grove: Path
) -> dict:
    pin = re.search(r"ruff==([\d.]+)", ci_text)
    cfg: dict = {
        "pins": {"tools": {"ruff": pin.group(1)}} if pin else {},
        "version_of": _in_venv(venv),
        "found_in": f"in {venv}",
        "repos": [str(root), str(grove)],
    }
    if not no_tests and not os.environ.get(NESTED):
        os.environ[NESTED] = "1"  # inherited by the suite the gate runs
        cfg["tests"] = [
            {
                "name": "onescript",
                "argv": [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "onescript/tests",
                ],
                "cwd": str(PKG.parent),
                "needs": ["pytest"],
            }
        ]
    return cfg


def _read(path: Path) -> tuple[str, str | None]:
    """Text and its hash, or ("", None) when the input is missing: recorded, not guessed."""
    if not path.exists():
        return "", None
    data = path.read_bytes()
    return data.decode("utf-8"), record.h256(data)


def _refused(why: str, code: int) -> dict:
    return {"refused": why, "code": code}


def _open(cfg: Config, cmd: str, argv: Iterable[str]) -> tuple[Run | None, dict]:
    """Open the box and write the `invocation` row: the argv, the root, the
    box, and the hash of every input read. Returns (run, info), or (None, a
    refusal) when there is no law, the keys sit in the box, or are exposed."""
    law_text, law_sha = _read(cfg.constitution)
    ci_text, ci_sha = _read(cfg.ci)
    if law_sha is None:
        return None, _refused(
            f"no law; willows-grove's constitution isn't at {cfg.constitution}"
            " (set ONESCRIPT_GROVE)",
            2,
        )
    if cfg.keys.resolve().is_relative_to(cfg.box.resolve()):
        return None, _refused("the keys file can't live inside the box", 2)
    try:
        keys = _keys(cfg.keys)
    except KeysExposed as e:
        return None, _refused(str(e), 2)
    law = _law(law_text)
    nested = bool(os.environ.get(NESTED))  # before _gate_cfg sets it for the child
    clock: Callable[[], str] = (lambda: cfg.now) if cfg.now else _now
    run = Run(cfg.box, keys, law, clock, anchor=cfg.keys.parent / ANCHOR)
    run.serve_key = _load_serve_key(cfg)
    run.rec.append(
        "invocation",
        run.sys,
        cmd=cmd,
        argv=list(argv),
        root=str(cfg.root),
        grove=str(cfg.grove),
        box=str(cfg.box),
        venv=str(cfg.venv),
        inputs={
            "governance/CONSTITUTION.md": law_sha,
            ".github/workflows/tests.yml": ci_sha,
            "onescript": run.version,
            "git_head": boot._git(str(cfg.root), "rev-parse", "HEAD") or None,
            "grove_head": boot._git(str(cfg.grove), "rev-parse", "HEAD") or None,
            "python": f"{sys.executable} {sys.version.split()[0]}",
            "venv_ruff": _in_venv(cfg.venv)("ruff"),
        },
        trace_ids=len(law["trace_ids"]),
        nested=nested,  # the tests gate is skipped, and says so
    )
    return run, {"ci_text": ci_text, "nested": nested}


def _closed_why(run: Run) -> tuple[dict | None, str | None]:
    """The last check-in as the record holds it, and why no motion is allowed
    (None when the box is open)."""
    last = _last_boot(run.rec.rows())
    if last is None:
        return None, "no check-in on record"
    if last["hard_close"]:
        return last, "the last check-in hard-closed"
    if last["checked_out"]:
        return last, "the run checked out after its last check-in"
    return last, None


def _need_open(run: Run, at: str, **fields) -> dict | None:
    """A refusal, recorded, unless the box is open."""
    _, why = _closed_why(run)
    if why is None:
        return None
    run.rec.append("refused", run.sys, at=at, reason=why, **fields)
    return _refused(f"{why}; nothing moves until a check-in opens", 1)


# ── check-in / check-out ────────────────────────────────────────────────────
def checkin(cfg: Config, argv: Iterable[str] = ("checkin",)) -> dict:
    run, info = _open(cfg, "checkin", argv)
    if run is None:
        return info
    try:
        rep = run.checkin(
            _gate_cfg(cfg.no_tests, info["ci_text"], cfg.venv, cfg.root, cfg.grove)
        )
    except boot.BoxWontOpen as e:
        # Recorded as a closed boot, so no later turn opens on an older,
        # clean check-in (Loki AAEDF24D).
        run.rec.append(
            "boot",
            run.sys,
            hard_close=True,
            lines=[f"box won't open: {e}"],
            options=["stop here"],
            probes=e.probes,
            gates=[],
            egress=[],
            anchor=run.rec.anchor_state(),
        )
        _save_serve_key(cfg, None)  # no key: serve fails closed
        return {"wont_open": str(e), "code": 3}
    _save_serve_key(cfg, run.serve_key)
    return {
        "report": rep,
        "nested": info["nested"],
        "code": 1 if rep["hard_close"] else 0,
    }


def checkout(cfg: Config, argv: Iterable[str] = ("checkout",)) -> dict:
    run, info = _open(cfg, "checkout", argv)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    rep, screen = run.checkout(boot_report=last)
    return {"report": rep, "screen": screen, "code": 0}


# ── turn ────────────────────────────────────────────────────────────────────
def turn(
    cfg: Config,
    bite: str,
    proposed: Iterable | None = None,
    argv: Iterable[str] = ("turn",),
) -> dict:
    """One turn as the desk, with the model's proposals when it brought any."""
    run, info = _open(cfg, "turn", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "turn", bite=bite)) is not None:
        return refusal
    who, family = DESK
    ident = {"who": who, "family": family, "sig": gate.sign(run.keys[who], who, family)}
    return {"out": run.turn(ident, bite, proposed=proposed), "code": 0}


def take_proposals(
    cfg: Config,
    rows: Iterable,
    bite: str = "proposals",
    argv: Iterable[str] = ("turn",),
) -> dict:
    """The model's rows in, each through the door and the served cites. Recorded
    and judged; nothing is written. A cite not served is `link_fail`; a row that
    doesn't fit the contract is `refused`."""
    return turn(cfg, bite, proposed=list(rows), argv=argv)


# ── scope, seal, serve ──────────────────────────────────────────────────────
def _spec_tables(run: Run, spec: dict) -> tuple[list[dict], dict]:
    """The stack a spec names, over the record up to `upto` (so rows added since
    can't move a table id) and the card for it."""
    by, match, upto = spec["by"], spec.get("match", {}), spec["upto"]
    stack = served.piles(run.rec.rows()[: upto + 1], *by)
    return served.choose(stack, **match), served.propose(stack, **match)


def scope(
    cfg: Config,
    by: Iterable[str] = ("who",),
    match: dict | None = None,
    upto: int | None = None,
    argv: Iterable[str] = ("scope",),
) -> dict:
    """Code proposes the stack. Returns the card for the human and the exact
    `subject` whose seal makes it a scope, plus the `spec` that names the stack:
    hand it back to `serve` unchanged."""
    run, info = _open(cfg, "scope", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "scope")) is not None:
        return refusal
    rows = run.rec.rows()
    spec = {
        "by": list(by),
        "match": dict(match or {}),
        "upto": rows[-1]["n"] if upto is None else upto,
    }
    try:
        _, card = _spec_tables(run, spec)
    except ValueError as e:
        return _refused(str(e), 1)
    run.rec.append("scope", run.sys, where=card["subject"], spec=spec, card=card)
    return {"spec": spec, **card, "code": 0}


def _seal(
    run: Run,
    subject: str,
    proof: str | None,
    pair: object,
    nestor_db: str | Path | None,
    keyring: dict | str | Path | None,
    verifiers: Iterable[str] | None,
) -> dict:
    """One seal, by whichever means the caller brought. A row either way: the
    seal, or the refusal and why."""

    def refuse(why: str) -> dict:
        return run.rec.append(
            "refused", run.sys, at="seal", reason=why, subject=subject
        )

    if pair is not None or nestor_db is not None:
        if keyring is None:
            return refuse("a Nestor seal needs the keyring to check its signature")
        try:
            ring = (
                keyring
                if isinstance(keyring, dict)
                else nestor_seal.load_keyring(keyring)
            )
            found = [pair] if pair is not None else []
            if nestor_db is not None:
                found += nestor_seal.pairs_for(nestor_db, subject)
        except nestor_seal.Unverifiable as e:
            return refuse(str(e))
        allowed = frozenset(verifiers) if verifiers is not None else None
        good = next(
            (p for p in found if nestor_seal.check(p, subject, ring, allowed)[0]),
            found[0] if found else None,  # none verifies: the refusal says why
        )
        if good is None:
            return refuse("no sealed Nestor pair has this subject as its conclusion")
        return run.seal_nestor(subject, good, ring, allowed)
    if proof is not None:
        human_key = run.keys.get(gate.HUMAN)
        if human_key is None:
            return refuse("no human key on this box; seal with a Nestor pair")
        return run.seal(subject, proof, human_key)
    return refuse("no seal given: a proof or a Nestor pair")


def seal_scope(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    pair: object = None,
    nestor_db: str | Path | None = None,
    keyring: dict | str | Path | None = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's seal over a scope's exact subject (`serve:<hash>`)."""
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal", subject=subject)) is not None:
        return refusal
    if not subject.startswith("serve:"):
        row = run.rec.append(
            "refused", run.sys, at="seal", reason="not a scope subject", subject=subject
        )
        return {"row": row, "sealed": False, "code": 1}
    row = _seal(run, subject, proof, pair, nestor_db, keyring, verifiers)
    return {
        "row": row,
        "sealed": row["kind"] == "seal",
        "code": 0 if row["kind"] == "seal" else 1,
    }


def serve(
    cfg: Config,
    spec: dict,
    max_chars: int | None = None,
    argv: Iterable[str] = ("serve",),
) -> dict:
    """Write the one file the model reads, for the stack `spec` names. Served
    only if the human sealed that exact set. `max_chars` is the caller's cap on
    the served text, sized from the model's context; over it the answer is
    `empty`, never truncated."""
    run, info = _open(cfg, "serve", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "serve")) is not None:
        return refusal
    try:
        tables, card = _spec_tables(run, spec)
    except (ValueError, KeyError, TypeError) as e:
        return _refused(f"bad spec: {type(e).__name__}: {e}", 1)
    doc = run.serve(tables, card["ids"], max_chars=max_chars)
    return {"doc": doc, "state": doc["state"], "why": doc["why"], "code": 0}


def seal_proposal(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    pair: object = None,
    nestor_db: str | Path | None = None,
    keyring: dict | str | Path | None = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's seal over one proposal's hash (`proposal:<hash>`). Only a
    proposal that passed is sealable, and only a sealed pass is written."""
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal", subject=subject)) is not None:
        return refusal
    rows = run.rec.rows()
    prop = next(
        (
            r
            for r in reversed(rows)
            if r["kind"] == "proposal" and r.get("subject") == subject
        ),
        None,
    )
    why = (
        "no such proposal on record"
        if prop is None
        else f"the proposal is {prop['verdict']}, not a pass; there is nothing to seal"
        if prop["verdict"] != "pass"
        else None
    )
    if why is not None:
        row = run.rec.append("refused", run.sys, at="seal", reason=why, subject=subject)
        return {"row": row, "sealed": False, "written": None, "code": 1}
    row = _seal(run, subject, proof, pair, nestor_db, keyring, verifiers)
    if row["kind"] != "seal":
        return {"row": row, "sealed": False, "written": None, "code": 1}
    wrote = run.write_proposal(subject)
    return {
        "row": row,
        "sealed": True,
        "written": wrote if wrote["kind"] == "write" else None,
        "write": wrote,
        "code": 0 if wrote["kind"] == "write" else 1,
    }


def read_proposals(path: Path) -> list[str]:
    """The lines of a proposal file, as Rat's build B wrote them."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()
