"""api — the one script as functions the app calls. No terminal, no tty.

    cfg = Config(box=..., keys=..., constitution=..., ci=..., root=..., grove=...,
                 venv=..., keyring=..., nestor_db=..., phone=...)
    checkin(cfg)                      # boot: record, probes, the four gates
    scope(cfg, by=("who",))           # code proposes a stack; the card and subject
    seal_scope(cfg, subject, ...)     # the human's seal over that exact set
    serve(cfg, spec, max_chars=N)     # write the one file the model reads
    take_proposals(cfg, rows)         # the model's rows in, judged, nothing written
    escalate(cfg, task)               # the deterministic chain on the served scope
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
is the exact subject (`nestor_seal`). The app uses the second. The pair must be
`sealed` in Nestor's store right now (`Config.nestor_db`, opened read-only), and
its signature is checked against the operator's keyring (`Config.keyring`, ed25519
public keys only), both fixed in `Config` and never taken from the caller.
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
from . import escalate as esc
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
    #: The operator's keyring of public keys, fixed here and resolved outside the
    #: box. A seal is checked against this and nothing the caller hands over.
    keyring: Path | None = None
    #: Nestor's store, read-only: a pair is a seal only while it is `sealed`
    #: there right now.
    nestor_db: Path | None = None
    #: Set by the APK when the run is on the phone, and only then. A gate whose
    #: tool, venv or clone is missing defers on the phone and hard-closes on the
    #: box; a missing tool never makes a run a phone run.
    phone: bool = False


PUBLIC_KEYRING = "verifiers.public.json"
NO_STORE = (
    "unreachable: no Nestor store is configured to confirm it "
    "($WILLOW_NESTOR_DB or $NESTOR_DB)"
)


def default_keyring() -> Path | None:
    """The public-only export of the operator's keyring: `$WILLOW_KEYRING`, else
    `$WILLOW_HOME/config/verifiers.public.json`; None when neither is set. The
    signing keyring (`verifiers.json`) is never the default: the box doesn't read
    it. `onescript keys export` writes this file."""
    if os.environ.get("WILLOW_KEYRING"):
        return Path(os.environ["WILLOW_KEYRING"]).expanduser()
    if os.environ.get("WILLOW_HOME"):
        return Path(os.environ["WILLOW_HOME"]).expanduser() / "config" / PUBLIC_KEYRING
    return None


def default_nestor_db() -> Path | None:
    """Nestor's store: `$WILLOW_NESTOR_DB` as the broker names it, else Nestor's
    own `$NESTOR_DB`, else None."""
    got = os.environ.get("WILLOW_NESTOR_DB") or os.environ.get("NESTOR_DB")
    return Path(got).expanduser() if got else None


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
    no_tests: bool,
    ci_text: str,
    venv: Path,
    root: Path,
    grove: Path,
    phone: bool = False,
) -> dict:
    pin = re.search(r"ruff==([\d.]+)", ci_text)
    cfg: dict = {
        "pins": {"tools": {"ruff": pin.group(1)}} if pin else {},
        "version_of": _in_venv(venv),
        "found_in": f"in {venv}",
        "repos": [str(root), str(grove)],
        "phone": phone,
    }
    if no_tests:
        cfg["tests_skipped"] = [{"name": "onescript", "why": "--no-tests was given"}]
    elif os.environ.get(NESTED):
        cfg["tests_skipped"] = [
            {
                "name": "onescript",
                "why": f"this check-in runs inside the tests gate ({NESTED})",
            }
        ]
    else:
        # Set in the child's environment only. Setting it in this process
        # would make every later check-in in a long-lived process skip the gate.
        cfg["tests"] = [
            {
                "name": "onescript",
                "env": {NESTED: "1"},
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
    for what, where in (("keyring", cfg.keyring), ("Nestor store", cfg.nestor_db)):
        if where is not None and where.resolve().is_relative_to(cfg.box.resolve()):
            return None, _refused(f"the {what} can't live inside the box", 2)
    try:
        keys = _keys(cfg.keys)
    except KeysExposed as e:
        return None, _refused(str(e), 2)
    law = _law(law_text)
    nested = bool(os.environ.get(NESTED))  # set only in the tests gate's child
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
        phone=cfg.phone,
        no_tests=cfg.no_tests,
        keyring=str(cfg.keyring) if cfg.keyring else None,
        nestor_db=str(cfg.nestor_db) if cfg.nestor_db else None,
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
            _gate_cfg(
                cfg.no_tests,
                info["ci_text"],
                cfg.venv,
                cfg.root,
                cfg.grove,
                cfg.phone,
            )
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
    cfg: Config,
    subject: str,
    proof: str | None,
    pair: object,
    verifiers: Iterable[str] | None,
) -> dict:
    """One seal, by whichever means the caller brought. A row either way: the
    seal, or the refusal and why.

    A Nestor seal is checked against the operator's keyring and Nestor's store,
    both fixed in `cfg`: the caller can hand over a pair to point at, never a
    keyring to check it by, and never a pair the store no longer holds as
    sealed."""

    def refuse(why: str) -> dict:
        state = {"state": "unreachable"} if why.startswith("unreachable") else {}
        return run.rec.append(
            "refused", run.sys, at="seal", reason=why, subject=subject, **state
        )

    if pair is not None or (proof is None and cfg.nestor_db is not None):
        if cfg.keyring is None:
            return refuse(
                "unreachable: no operator keyring is configured "
                "($WILLOW_KEYRING or $WILLOW_HOME/config/verifiers.public.json)"
            )
        if cfg.nestor_db is None:
            return refuse(NO_STORE)
        if not nestor_seal.have_ed25519():
            return refuse(nestor_seal.NO_CRYPTO)
        try:
            ring = nestor_seal.load_keyring(cfg.keyring)
            found = nestor_seal.current(cfg.nestor_db, subject, pair)
        except nestor_seal.Unverifiable as e:
            return refuse(f"unreachable: {e}")
        if not found:
            return refuse(
                "no pair for this subject is sealed in Nestor's store right now"
                if pair is not None
                else "no sealed Nestor pair has this subject as its conclusion"
            )
        allowed = frozenset(verifiers) if verifiers is not None else None
        good = next(
            (p for p in found if nestor_seal.check(p, subject, ring, allowed)[0]),
            found[0],  # none verifies: the refusal says why
        )
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
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's seal over a scope's exact subject (`serve:<hash>`). A Nestor
    seal is checked against `cfg.keyring` and `cfg.nestor_db`, never the caller's."""
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
    row = _seal(run, cfg, subject, proof, pair, verifiers)
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
    `empty`, never truncated. Without one it is `empty` too: nothing is served
    uncapped."""
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
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's seal over one proposal's hash (`proposal:<hash>`). Only a
    proposal that passed is sealable, and only a sealed pass is written. The
    stored proposal must hash to the subject, or there is nothing to seal."""
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal", subject=subject)) is not None:
        return refusal
    _, why = run.find_proposal(subject)
    if why is not None:
        row = run.rec.append("refused", run.sys, at="seal", reason=why, subject=subject)
        return {"row": row, "sealed": False, "written": None, "code": 1}
    row = _seal(run, cfg, subject, proof, pair, verifiers)
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


def pooled(cfg: Config, argv: Iterable[str] = ("pooled",)) -> dict:
    """The pooled unsealed pass set, for the close-out deposit: every passing
    proposal on record that no human seal covers, one per subject, each as
    {subject, path, data, cites, claim}. Read-only: it writes an `invocation`
    row like every verb and nothing else; it seals and writes nothing. It does
    not need an open box, so a deposit may follow check-out."""
    run, info = _open(cfg, "pooled", argv)
    if run is None:
        return info
    items = run.pooled()
    return {"pooled": items, "count": len(items), "code": 0}


def escalate(
    cfg: Config,
    task: str,
    *,
    rung: esc.Rung | None = None,
    model: str = esc.DEFAULT_MODEL,
    piece_chars: int = esc.PIECE_CHARS,
    ratatosk: Iterable[str] = ("ratatosk",),
    rung_timeout: float = esc.RUNG_TIMEOUT_S,
    argv: Iterable[str] = ("escalate",),
) -> dict:
    """willow-bot's deterministic chain on the current check-in's sealed served
    scope: D0, then pieces to the local rung, every result recorded by hash,
    whatever no rung answered as one human card (see `escalate`). `rung` is the
    local rung, injectable; by default Rat, per piece. Escalation is not a
    failure: the code is 0 unless the box won't allow it."""
    run, info = _open(cfg, "escalate", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "escalate", task=task)) is not None:
        return refusal
    if not isinstance(task, str) or not task.strip():
        return _refused("escalate needs a task", 1)
    if (
        isinstance(piece_chars, bool)
        or not isinstance(piece_chars, int)
        or piece_chars < 1
    ):
        return _refused("piece_chars must be a positive whole number", 1)
    who, family = DESK
    ident = {"who": who, "family": family, "sig": gate.sign(run.keys[who], who, family)}
    try:
        out = esc.run_escalate(
            run,
            ident,
            task,
            rung=rung or esc.ratatosk_rung(tuple(ratatosk), rung_timeout),
            model=model,
            piece_chars=piece_chars,
        )
    except esc.ChainUnavailable as e:
        return _refused(str(e), 2)
    return {**out, "code": 0}


def read_proposals(path: Path) -> list[str]:
    """The lines of a proposal file, as Rat's build B wrote them."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()
