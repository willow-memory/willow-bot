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
    needs(cfg)                        # the morning screen's NEEDS YOU lines, read-only
    forecast(cfg, wid, dist, by=...)  # a wager row: the forecast, before the outcome
    seal_outcome(cfg, wager, outcome, proof=...)  # the human's seal on what happened
    seal_tip(cfg, "tip:n:hash", proof=...)        # the human seals a tip
    seal_forecast(cfg, wager, proof=...)          # the human: this forecast is mine
    reconcile(cfg, human_key=FILE)    # wagers lined up and scored, read-only
    read_misses(cfg, human_key=FILE)  # the misses, clustered, read-only
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

from . import boot, gate, nestor_seal, record, reverse, view
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
                # The child resolves the grove from its own environment, so
                # hand it the one this check-in resolved (cfg.grove), not
                # whatever it would find beside itself.
                "env": {NESTED: "1", "ONESCRIPT_GROVE": str(grove)},
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
    run.keyring, run.nestor_db = cfg.keyring, cfg.nestor_db
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
    run.pin()  # once per box: the first verb this code runs pins the old tip
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


def checkout(
    cfg: Config,
    argv: Iterable[str] = ("checkout",),
    *,
    human_key: Path | None = None,
    basis=None,
) -> dict:
    """Reverse, then the morning screen. `human_key` is the human's key in a
    file outside the box (0600, `read_human_key`): with it, wager outcomes the
    sealed tip covers count, and GRADES shows their scores. `basis` is the R3
    hook (`table.basis`); left None, `Run.reconciled` binds it whenever the
    box holds table verdict rows."""
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    run, info = _open(cfg, "checkout", argv)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    rep, screen = run.checkout(boot_report=last, human_key=key, basis=basis)
    return {"report": rep, "screen": screen, "code": 0}


def needs(cfg: Config, argv: Iterable[str] = ("needs",), *, basis=None) -> dict:
    """What the morning screen's NEEDS YOU section would list right now, as
    data: `{"needs": [line, ...], "count": n}` (an empty list is the screen's
    "nothing"). The lines are `view.needs_you`, the same ones `checkout` prints,
    drawn from the record by the same reverse report. Read-only, like `pooled`
    and `resume`: it writes the `invocation` row every verb writes and nothing
    else, in particular no `reconcile` row, so it does not check the run out and
    it needs no open box."""
    run, info = _open(cfg, "needs", argv)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    lines = view.needs_you(run.reconciled(basis=basis), last, run.claims, run.acts)
    return {"needs": lines, "count": len(lines), "code": 0}


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
    outcome: dict | None = None,
) -> dict:
    """One seal, by whichever means the caller brought. A row either way: the
    seal, or the refusal and why. With `outcome`, `subject` is a wager's row
    hash and what is sealed is `reverse.outcome_subject(subject, outcome)`.

    A Nestor seal is checked against the operator's keyring and Nestor's store,
    both fixed in `cfg`: the caller can hand over a pair to point at, never a
    keyring to check it by, and never a pair the store no longer holds as
    sealed."""

    signed = subject if outcome is None else reverse.outcome_subject(subject, outcome)

    def refuse(why: str) -> dict:
        state = {"state": "unreachable"} if why.startswith("unreachable") else {}
        return run.rec.append(
            "refused", run.sys, at="seal", reason=why, subject=signed, **state
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
            found = nestor_seal.current(cfg.nestor_db, signed, pair)
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
            (p for p in found if nestor_seal.check(p, signed, ring, allowed)[0]),
            found[0],  # none verifies: the refusal says why
        )
        return run.seal_nestor(subject, good, ring, allowed, outcome)
    if proof is not None:
        human_key = run.keys.get(gate.HUMAN)
        if human_key is None:
            return refuse("no human key on this box; seal with a Nestor pair")
        return run.seal(subject, proof, human_key, outcome)
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


def lore(cfg: Config, subject: str, argv: Iterable[str] = ("lore",)) -> dict:
    """Turn a human-sealed `world/*.md` proposal into `lore` rows, one fact each
    (SPEC R11; `lore.from_sealed`). Refused, recorded, when the proposal is not
    sealed, not a pass, an own idea, or not under world/. Writes no seal and not
    the file (`seal_proposal` does). Ends with the rescope step, as R14 does:
    new rows move the piles they sit in, so a scope at the new tip serves empty
    until the human seals it."""
    from . import lore as lore_rows

    run, info = _open(cfg, "lore", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "lore", subject=subject)) is not None:
        return refusal
    made = lore_rows.from_sealed(run, subject)
    if made and made[0]["kind"] == "refused":
        return {"refused": made[0]["reason"], "row": made[0], "code": 1}
    if not made:  # nothing was written, so nothing moved
        return {
            "lore": [],
            "count": 0,
            "rescope": {
                "why": "no new rows: every fact was already lore; no pile moved",
                "steps": [],
            },
            "code": 0,
        }
    by = "what"
    return {
        "lore": [{"n": r["n"], "where": r["where"], "fact": r["fact"]} for r in made],
        "count": len(made),
        "rescope": {
            "why": "the new lore rows moved the what=lore pile (and the author's "
            "who pile): a scope drawn at the new tip serves empty until the "
            "human seals it",
            "steps": [
                f"python3 -m onescript scope --by {by} --match what=lore",
                "python3 -m onescript seal <the new subject> --proof P",
                f"python3 -m onescript serve --by {by} --match what=lore"
                " --upto <its upto> --max-chars N",
            ],
        },
        "code": 0,
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


def resume(cfg: Config, argv: Iterable[str] = ("resume",)) -> dict:
    """The most recent human-sealed scope, as the record holds it: the `spec`
    (with the `upto` it was proposed at) and the `subject` the human sealed.
    Serving that spec again stays `populated` however many rows were added
    since, where a fresh scope at `upto=latest` would move the subject out from
    under the seal. `sealed_at` is the seal row's own `ts`, the stamp the record
    put on it when the human sealed (never the scope row's, never now); a seal
    row without a `ts` leaves the key out. Read-only, like `pooled`: it writes an `invocation` row and
    nothing else, needs no open box, and never seals, serves or mints. With no
    human seal over a scope, or no scope row behind it, the answer is
    {"resume": None}. A newer seal row that can't be checked here does not hide
    an older verified one: the older one is resumed and the newer one rides
    along as `newer` ({subject, state: "unreachable", why}). Only when no seal
    verifies is the answer {"resume": None, "state": "unreachable"}."""
    run, info = _open(cfg, "resume", argv)
    if run is None:
        return info
    rows = run.rec.rows()
    good, unchecked = run.seal_rows(rows)  # the row itself, not its subject
    seal = newer = None
    for r in reversed(rows):
        subject = str(r.get("subject", ""))
        if r["kind"] != "seal" or not subject.startswith("serve:"):
            continue
        if r["n"] in good:
            seal = r
            break
        if r["n"] in unchecked and newer is None:  # said, not guessed
            newer = {
                "subject": subject,
                "state": "unreachable",
                "why": unchecked[r["n"]],
            }
    if seal is None:
        if newer is not None:
            return {
                "resume": None,
                "state": "unreachable",
                "why": newer["why"],
                "code": 0,
            }
        return {"resume": None, "code": 0}
    scoped = next(
        (
            r
            for r in reversed(rows)
            if r["kind"] == "scope"
            and r["n"] < seal["n"]
            and r.get("where") == seal["subject"]
        ),
        None,
    )
    if scoped is None or not isinstance(scoped.get("spec"), dict):
        return {"resume": None, "code": 0}
    found = {"spec": scoped["spec"], "subject": seal["subject"], "code": 0}
    if seal.get("ts"):
        found["sealed_at"] = seal["ts"]
    if newer is not None:  # a newer seal it could not check: shown, not dropped
        found["newer"] = newer
    return found


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


# ── the forecast lane (SPEC R1: forge-convergence.md:643-645, plus seal_tip
# and reconcile, without which no outcome counts) ────────────────────────────
OUTCOME_KEYS = ("path_index", "path", "winner")  # reverse._match_distribution
COMMITTED_KEYS = {"sha256", "beacon_then", "target"}
TABLE_KEYS = {"world", "sheets_sha"}  # a table forecast's world (SPEC W2 S2)


def forecast(
    cfg: Config,
    wid: str,
    distribution: list[dict],
    *,
    by: str = "desk",
    committed: dict | None = None,
    table: dict | None = None,
    argv: Iterable[str] = ("forecast",),
) -> dict:
    """`forecast()` seals a prediction into the ledger before the outcome
    (forge-convergence.md:643): one `wager` row, chained, whose hash is the
    subject an outcome seal names. "Seals" means committed to the chain; it
    counts only once a tip the human seals holds it before the outcome seal
    (SPEC R3). Refused, with nothing appended but the refusal: a distribution
    that does not stand (`reverse.check_distribution`), a `by` that is not a
    role, a `committed` that is not {sha256, beacon_then, target}, or an id
    already on record (a forecast stands, `play.py:254-256`). `table` names
    the world and the stat sheets a table forecast is about, exactly
    {world, sheets_sha} (`peaks.world_key`); a table session that played
    another world or other sheets can't supply its outcome (`table.basis`)."""
    run, info = _open(cfg, "forecast", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "forecast", id=wid)) is not None:
        return refusal
    why = None
    if not isinstance(wid, str) or not wid.strip():
        why = "a forecast needs an id"
    elif by not in reverse.ROLES:
        why = f"by is a role, one of {list(reverse.ROLES)}, never a name: {by!r}"
    elif committed is not None and (
        not isinstance(committed, dict) or set(committed) != COMMITTED_KEYS
    ):
        why = f"committed carries exactly {sorted(COMMITTED_KEYS)}"
    elif table is not None and (
        not isinstance(table, dict)
        or set(table) != TABLE_KEYS
        or not all(isinstance(v, str) and v for v in table.values())
    ):
        why = f"table names exactly {sorted(TABLE_KEYS)}, as text"
    elif any(r["kind"] == "wager" and r.get("id") == wid for r in run.rec.rows()):
        why = f"a wager {wid!r} is already on record; a forecast stands"
    else:
        why = reverse.check_distribution(distribution)
    if why is not None:
        row = run.rec.append("refused", run.sys, at="forecast", reason=why, id=wid)
        return {"refused": why, "row": row, "code": 1}
    row = run.wager(
        wid,
        distribution,
        by=by,
        committed=committed,
        commit=reverse.COMMIT,
        table=table,
    )
    return {
        "row": row,
        "subject": row["hash"],  # what seal_outcome names
        "tip": record.tip_subject(row),  # what the human signs to commit it
        "code": 0,
    }


def _rescope(run: Run) -> dict:
    """The step after an outcome seal (SPEC R14): the seal is a new row in the
    human's pile, so a scope drawn at the new tip has a new subject, and serving
    it is `empty` until the human seals that one. Read from the last scope the
    human sealed; nothing is proposed, sealed or served here."""
    good, _ = run.seal_rows()
    rows = run.rec.rows()
    spec = None
    for r in reversed(rows):
        if r["kind"] == "seal" and r["n"] in good:
            sub = str(r.get("subject") or "")
            if sub.startswith("serve:"):
                spec = next(
                    (
                        x["spec"]
                        for x in reversed(rows)
                        if x["kind"] == "scope"
                        and x["n"] < r["n"]
                        and x.get("where") == sub
                    ),
                    None,
                )
                break
    by = ",".join(spec["by"]) if spec else "who"
    match = "".join(
        f" --match {k}={v}"
        for k, v in sorted(((spec or {}).get("match") or {}).items())
    )
    return {
        "why": "the outcome seal moved the human's pile: a scope at the new tip "
        "serves empty until the human seals it"
        + ("" if spec else " (no sealed scope on record yet)"),
        "steps": [
            f"python3 -m onescript scope --by {by}{match}",
            "python3 -m onescript seal <the new subject> --proof P",
            f"python3 -m onescript serve --by {by}{match} --upto <its upto>"
            " --max-chars N",
        ],
    }


def _wager_row(run: Run, wager: str) -> dict | None:
    return next(
        (r for r in run.rec.rows() if r["kind"] == "wager" and r["hash"] == wager),
        None,
    )


def seal_outcome(
    cfg: Config,
    wager: str,
    outcome: dict,
    *,
    proof: str | None = None,
    pair: object = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal-outcome",),
) -> dict:
    """`seal_outcome()` is the operator's seal on what happened
    (forge-convergence.md:644) over `reverse.outcome_subject(wager, outcome)`,
    so the outcome is inside what was signed: by the human's proof
    (`Run.seal`), or by a sealed Nestor pair whose conclusion is that subject
    (`Run.seal_nestor`, checked against `cfg.keyring` and `cfg.nestor_db`,
    never the caller's). Refused before any seal row is written: no wager row
    with that hash, an outcome that is not one of path_index / path / winner,
    or one naming no path the wager declared, or more than one. A proofless
    seal is refused; code never writes a seal row. Ends with the rescope step
    (SPEC R14) and the tip to seal next, without which the outcome is not
    covered. Recorded gap (C6): the tip itself is sealed by the human's key;
    a Nestor-sealed tip does not count in `reverse.covered_through`."""
    run, info = _open(cfg, "seal-outcome", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal_outcome", subject=wager)) is not None:
        return refusal
    w = _wager_row(run, wager)
    why = None
    if w is None:
        why = "no wager row on record has that hash"
    elif (
        not isinstance(outcome, dict)
        or len(outcome) != 1
        or next(iter(outcome)) not in OUTCOME_KEYS
    ):
        why = f"an outcome is one of {list(OUTCOME_KEYS)}"
    elif w.get("distribution") and reverse._match_distribution(w, outcome)[1] is None:
        why = "the outcome names no path the wager declared, or more than one"
    elif proof is None and pair is None and cfg.nestor_db is None:
        why = "no seal given: the human's proof or a Nestor pair over the outcome"
    elif proof is not None and run.keys.get(gate.HUMAN) is None:
        why = "no human key on this box to check the proof by"
    if why is not None:
        row = run.rec.append(
            "refused", run.sys, at="seal_outcome", reason=why, subject=wager
        )
        return {"refused": why, "row": row, "sealed": False, "code": 1}
    row = _seal(run, cfg, wager, proof, pair, verifiers, outcome)
    if row["kind"] != "seal":
        return {"row": row, "sealed": False, "code": 1}
    out = {
        "row": row,
        "sealed": True,
        "tip": record.tip_subject(row),  # seal it, or this outcome is not covered
        "rescope": _rescope(run),
        "code": 0,
    }
    if row.get("via") == "nestor":  # the C6 gap, said where it bites
        out["counts_when"] = (
            "sealed by Nestor pair: this outcome counts only once a tip sealed "
            "by the human's key covers it (a Nestor-sealed tip does not count)"
        )
    return out


def seal_forecast(
    cfg: Config,
    wager: str,
    *,
    proof: str | None = None,
    argv: Iterable[str] = ("seal-forecast",),
) -> dict:
    """The human says a forecast is theirs: a proof over
    `reverse.forecast_subject(wager)`. The vouch is authorship, not timing:
    it says who made the forecast, never when; when is the chain order under
    the human's sealed tip (SPEC R3), and a vouch sealed later changes none of it. `by` on a wager row is the run's claim
    (`by_claimed`); a forecast claimed as the human's counts under "human" in
    the calibration only with this seal, else under "human (claimed)"."""
    run, info = _open(cfg, "seal-forecast", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal_forecast", subject=wager)) is not None:
        return refusal
    w = _wager_row(run, wager)
    why = None
    if w is None or w.get("commit") != reverse.COMMIT:
        why = "no forecast row on record has that hash"
    elif proof is None:
        why = "no seal given: the human's proof over the forecast subject"
    elif run.keys.get(gate.HUMAN) is None:
        why = "no human key on this box to check the proof by"
    if why is not None:
        row = run.rec.append(
            "refused", run.sys, at="seal_forecast", reason=why, subject=wager
        )
        return {"refused": why, "row": row, "sealed": False, "code": 1}
    row = run.seal(reverse.forecast_subject(wager), proof, run.keys[gate.HUMAN])  # type: ignore[arg-type]
    ok = row["kind"] == "seal"
    return {"row": row, "sealed": ok, "code": 0 if ok else 1}


def covers(rows: list[dict], after: int | None, upto: int) -> list[dict]:
    """What a tip at row `upto` newly covers: the rows after the anchored tip
    (`after`, None when nothing was sealed) and at or before `upto`. What the
    human's signature on that tip vouches for, shown before they sign and
    again after (SPEC S3): every forecast and outcome row; every table
    verdict session, once, with its row count and the forecast it claims;
    and every `agm_head` row with the ledger head it anchors (SPEC W2 S3), so
    no table row is signed blind. A row's `who` is shown as the record holds
    it: the `table` identity gates the write, it does not sign the row, so a
    row hand-appended under that label shows up here too."""
    wagers = {r["hash"]: r for r in rows if r["kind"] == "wager"}
    out: list[dict] = []
    sessions: dict[str, dict] = {}
    for r in rows:
        if not (after is None or r["n"] > after) or r["n"] > upto:
            continue
        if r["kind"] == "verdict" and r.get("what") == "agm_head":
            out.append(
                {
                    "n": r["n"],
                    "what": "agm_head",
                    "who": r.get("who"),
                    "ledger": r.get("ledger"),
                    "ledger_n": r.get("ledger_n"),
                    "head": r.get("head"),
                    "ts": r.get("ts"),
                }
            )
            continue
        if r["kind"] == "verdict":
            sid = str(r.get("session"))
            got = sessions.get(sid)
            if got is None:
                got = sessions[sid] = {
                    "n": r["n"],
                    "what": "verdict session",
                    "session": sid,
                    "who": sorted({str(r.get("who"))}),
                    "rows": 0,
                    "forecast": None,
                    "ts": r.get("ts"),
                }
                out.append(got)
            got["rows"] += 1
            got["who"] = sorted(set(got["who"]) | {str(r.get("who"))})
            if r.get("forecast"):
                f = wagers.get(r["forecast"])
                got["forecast"] = f.get("id") if f else r["forecast"]
                got["forecast_on_record"] = f is not None
            continue
        if r["kind"] == "wager":
            w, what = r, "forecast"
        elif r["kind"] == "seal" and r.get("wager") in wagers:
            w, what = wagers[r["wager"]], "outcome"
        else:
            continue
        out.append(
            {
                "n": r["n"],
                "what": what,
                "id": w.get("id"),
                "by_claimed": w.get("by"),
                "ts": r.get("ts"),
            }
        )
    return out


def seal_tip(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    argv: Iterable[str] = ("seal-tip",),
) -> dict:
    """The human seals a tip (`tip:<n>:<hash>`, as `forecast` and
    `seal_outcome` hand it out) by `Run.seal_tip`; the anchor keeps it beside
    the keys, outside the box. Without a sealed tip no outcome counts
    (`reverse.covered_through`). Before and after: the answer lists the
    forecast and outcome rows this tip newly covers (`covers`), so asking
    without a proof shows what a signature would vouch for, writing no seal."""
    run, info = _open(cfg, "seal-tip", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal_tip", subject=subject)) is not None:
        return refusal
    n, _, hsh = str(subject).removeprefix(record.TIP).partition(":")
    why = None
    newly: list[dict] = []
    if not str(subject).startswith(record.TIP) or not n.isdigit() or not hsh:
        why = "not a tip subject (tip:<n>:<hash>)"
    else:
        before = run._anchor()
        newly = covers(run.rec.rows(), before["n"] if before else None, int(n))
        if proof is None:
            why = "no seal given: the human's proof over the tip"
        elif run.keys.get(gate.HUMAN) is None:
            why = "no human key on this box to check the proof by"
    if why is not None:
        row = run.rec.append(
            "refused", run.sys, at="seal_tip", reason=why, subject=subject
        )
        return {"refused": why, "row": row, "sealed": False, "covers": newly, "code": 1}
    row = run.seal_tip(proof, run.keys[gate.HUMAN], {"n": int(n), "hash": hsh})  # type: ignore[arg-type]
    ok = row["kind"] == "seal"
    return {"row": row, "sealed": ok, "covers": newly, "code": 0 if ok else 1}


def read_human_key(path: Path | None, box: Path) -> tuple[bytes | None, str | None]:
    """The human's key from a file outside the box (SPEC H4, C6): hex on one
    line. (key, None), (None, None) with no file, or (None, why) refused: a
    file inside the box, readable beyond its owner, or not a key."""
    if path is None:
        return None, None
    if path.resolve().is_relative_to(box.resolve()):
        return None, "the human key can't live inside the box"
    try:
        if path.stat().st_mode & 0o077:
            return None, f"{path} is readable beyond its owner; chmod 600 it"
        key = bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError) as e:
        return None, f"the human key could not be read: {type(e).__name__}"
    return (key, None) if key else (None, "the human key file is empty")


def reconcile(
    cfg: Config,
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("reconcile",),
    basis=None,
) -> dict:
    """Every wager lined up against the human's sealed outcomes, scored, by
    `Run.reconciled` with the human's key from `human_key` (a file outside the
    box, 0600). Read-only, like `needs`: an `invocation` row and nothing else,
    no `reconcile` row, so it checks nothing out. Without the key no outcome
    counts, and the answer says so. `graded_by` stays None (SPEC R4). `basis`
    as `checkout` takes it: a table box goes through R3 here too."""
    run, info = _open(cfg, "reconcile", argv)
    if run is None:
        return info
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    rep = run.reconciled(human_key=key, basis=basis)
    tip = run.rec.tip()
    out = {
        "wagers": rep["wagers"],
        "calibration": rep["calibration"],
        "misses": rep["misses"],
        "tip": record.tip_subject(tip) if tip else None,
        # INVARIANTS §1: a broken record and an unchecked seal are said apart
        "record": {"breaks": rep["wagers"]["breaks"]},
        "unreachable": rep["wagers"]["unreachable"],
        "code": 0,
    }
    if key is None:
        out["why"] = "no human key handed over: no outcome counts"
    return out


def read_misses(
    cfg: Config,
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("read-misses",),
) -> dict:
    """`read_misses()` clusters the misses, not only counts them
    (forge-convergence.md:645). The misses of `reconcile`, read-only the same
    way."""
    res = reconcile(cfg, human_key=human_key, argv=argv)
    if "refused" in res:
        return res
    return {**res["misses"], "code": 0}


def read_proposals(path: Path) -> list[str]:
    """The lines of a proposal file, as Rat's build B wrote them."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()
