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
    writeback_seal(cfg, subject, ...) # the human's seal on one write-back
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
session's ids, is a thing the record must not hold; it is kept beside the
keys (`serve.key`, 0600), made fresh at each check-in, and is never the seal
key. So is each box's store key (`pile.key_name`, 0600), made once, which keys
the hashes in that box's store.

Seals without a terminal: a seal is either the human's HMAC proof over the
subject (needs a key in the keys file) or a sealed Nestor pair whose conclusion
is the exact subject (`nestor_seal`). The app uses the second. The pair must be
`sealed` in Nestor's store right now (`Config.nestor_db`, opened read-only), and
its signature is checked against the operator's keyring (`Config.keyring`, ed25519
public keys only), both fixed in `Config` and never taken from the caller.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from . import beacon, boot, gate, nestor_seal, record, reverse, view
from . import clock as clock_mod
from . import escalate as esc
from . import jeles_store as jeles_mod
from . import ladder
from . import pile as pile_mod
from . import seed as seed_mod
from . import serve as served
from . import session as session_mod
from . import writeback as wb_mod
from .run import PKG, Run
from .states import (
    EMPTY,
    ESCALATED_COLD_START,
    POPULATED,
    RANKED,
    UNREACHABLE,
)

DESK = ("desk", "claude")
ANCHOR = "anchor.json"  # the sealed tip, beside the keys: outside the box
SERVE_KEY = "serve.key"  # this session's id key, beside the keys: outside the box

#: Set in the tests gate's child. A checkin started under it never runs the
#: tests gate again: the gate runs the suite, the suite runs checkin, and an
#: unguarded loop forked until the box ran out of memory (2026-10-06, Kart
#: Z7X2TJQX, a mutant with the keys refusal removed).
NESTED = "ONESCRIPT_IN_TESTS_GATE"


def bot_paths():
    """The bot's box rule, one copy (flatten F-17): ``willow_bot.paths`` from
    the willow-bot checkout the one script lives in."""
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from willow_bot import paths  # noqa: PLC0415

    return paths


_STAMP = {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}


@dataclass(frozen=True)
class Config:
    box: Path
    keys: Path
    constitution: Path
    ci: Path
    root: Path
    grove: Path
    venv: Path | None  # None: no box configured (F-17); the gate says why
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
    #: The operator's drand reader (`beacon.drand_reader`, or a stand-in):
    #: round -> {"randomness", ...}. A table outcome counts only once the
    #: session's randomness is the one this says its round is (W4 break 1).
    #: Fixed here, never taken from a caller; None: offline, and no table
    #: outcome can be proposed (`unreachable`, said).
    beacon: Callable[[int], dict] | None = None
    #: The operator's price reader (`market.stooq_reader`, or a stand-in):
    #: (symbol, YYYY-MM-DD) -> {"close", "prev_close"}. A market call's
    #: outcome counts only once the closes on its row are the ones this gives.
    #: Fixed here, never taken from a caller; None: no market call resolves
    #: (`unreachable`, said).
    market: Callable[[str, str], dict] | None = None
    #: Where an indexed repo is read from, by name; any it leaves out are looked
    #: for beside willow-bot. The session's store and serve read through it.
    repos: dict | None = None
    #: Attach the session's store (the pile hashed once, `pile.py`) at check-in.
    store: bool = True
    #: Build a store that needs building in the background, so check-in never
    #: waits on it (the operator: "loaded everything up in the background").
    #: The CLI turns it on; it attaches at the first verb after it is ready.
    store_background: bool = False
    #: A Forge checkout (the repo root, holding `forge/entry.py`): put first on
    #: the import path for `forge_bite`, so the Forge needs a clone, not an
    #: install. None, or no `forge/entry.py` there: whatever is importable.
    forge: Path | None = None
    #: When the pile holds no xref index at check-in, build one over these
    #: repos (name -> path), through the record, before the store attaches.
    #: None: no index is `unreachable`, said, as before. An index that is there
    #: but doesn't hash to its pointer is never rebuilt over.
    index_repos: dict | None = None
    #: One seal per session, at its end (`onescript.session`; the operator,
    #: 2026-10-19). Serve hands out an unsealed scope, the Forge's card answers
    #: are drafts, check-out proposes the session's bundle to Nestor, and
    #: check-in reads the person's seal back. Off: a scope serves only once
    #: sealed, as before. The CLI turns it on.
    seal_at_end: bool = False
    #: The pile's rung 3 for the per-prompt hook: WordLlama's local file dir.
    #: Set, `prompt` fuses the words' rank with the embedder's by RRF over the
    #: top EMBED_POOL candidates; None, or a rung that won't load: the words'
    #: rank alone, and the served document says which.
    embed: Path | None = None
    #: Jeles's verified corpus, the pile's escalation rung (`jeles_store`): a
    #: `JelesStore`, asked at seed only when the pile ranks nothing for the
    #: bite or there is no pile, and read back at serve. None: Jeles is never
    #: asked, and a seed row is what it was before. The CLI turns it on when
    #: `jeles` is installed.
    jeles: Any = None


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


_now = clock_mod.seconds_z  # the record's clock when there is no --now (flatten-2 F8)


class KeysExposed(Exception):
    """A keys file anyone but the owner can read is refused, not used."""


def _keys(path: Path) -> dict[str, bytes]:
    """The signing secrets live beside the box, never in it: the record's own
    three-way check flags any file in the box the run didn't write."""
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({DESK[0]: secrets.token_hex(32)}, f)
    if path.stat().st_mode & 0o077:
        raise KeysExposed(f"{path} is readable beyond its owner; chmod 600 it")
    return {
        k: bytes.fromhex(v)
        for k, v in json.loads(path.read_text(encoding="utf-8")).items()
    }


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
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.hex())
    path.chmod(0o600)


def _load_serve_key(cfg: Config) -> bytes | None:
    path = cfg.keys.parent / SERVE_KEY
    try:
        if path.stat().st_mode & 0o077:
            return None  # a key others can read isn't a key
        return bytes.fromhex(path.read_text(encoding="utf-8").strip()) or None
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


def _in_venv(venv: Path | None):
    """The toolchain gate reads the bot's venv, where the one script's tools
    live (D2), never whatever happens to be first on PATH. No venv (no box
    configured, F-17): every tool reads as missing, and the gate says where it
    looked."""

    def version_of(tool: str) -> str | None:
        if venv is None:
            return None
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
    venv: Path | None,
    root: Path,
    grove: Path,
    phone: bool = False,
) -> dict:
    pin = re.search(r"ruff==([\d.]+)", ci_text)
    cfg: dict = {
        "pins": {"tools": {"ruff": pin.group(1)}} if pin else {},
        "version_of": _in_venv(venv),
        "found_in": (
            f"in {venv}"
            if venv is not None
            else f"in no venv: {bot_paths().BOX_NOT_CONFIGURED}"
        ),
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
        # The suite runs in the bot's venv when it has one (D2), and its needs
        # are asked of that interpreter; else this one, as before.
        # No box (F-17) has no venv at all: the suite runs on this interpreter
        # and the row says so, never a guessed path.
        venv_py = venv / "bin" / "python" if venv is not None else None
        py = str(venv_py) if venv_py is not None and venv_py.exists() else None
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
                    py or sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "onescript/tests",
                ],
                "cwd": str(PKG.parent),
                "needs": ["pytest"],
                **({"python": py} if py else {}),
                **(
                    {
                        "ran_on": f"{sys.executable}, in no venv: "
                        f"{bot_paths().BOX_NOT_CONFIGURED}"
                    }
                    if venv is None
                    else {}
                ),
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


def _open(
    cfg: Config, cmd: str, argv: Iterable[str], *, write: bool = True
) -> tuple[Run | None, dict]:
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
    run.beacon, run.market = cfg.beacon, cfg.market
    if not write:  # `peek`: a Run to read through; no invocation, no pin, no row
        return run, {"ci_text": ci_text, "nested": False}
    run.rec.append(
        "invocation",
        run.sys,
        cmd=cmd,
        argv=list(argv),
        root=str(cfg.root),
        grove=str(cfg.grove),
        box=str(cfg.box),
        venv=str(cfg.venv) if cfg.venv is not None else None,
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
    if cfg.store and not rep["hard_close"]:
        rep["store"] = _attach_store(cfg, run)
    if cfg.seal_at_end and not rep["hard_close"]:
        rep["sessions"] = _read_session_seals(cfg, run)
    return {
        "report": rep,
        "nested": info["nested"],
        "code": 1 if rep["hard_close"] else 0,
    }


def _repos(cfg: Config, index: dict) -> dict:
    where = {n: cfg.root.parent / n for n in index.get("arrival", {})}
    where.update({n: Path(p) for n, p in (cfg.repos or {}).items()})
    return where


def _store_key(cfg: Config, box: Path, make: bool = False):
    """(key, None) for a box's store key, or (None, why). The key lives in
    the operator's keys dir (`pile.key_name`, 0600), never in a box or beside
    its store, so the keyed hashes in a store are no dictionary to whoever
    holds the store alone. Made at check-in when absent, like `serve.key`;
    another box's key is only ever read, never made."""
    path = cfg.keys.parent / pile_mod.key_name(box)
    for inside in (box, pile_mod.store_path(box).parent):
        if path.resolve().is_relative_to(Path(inside).resolve()):
            return None, "the store key can't live inside a box or its store"
    if make:
        try:
            pile_mod.make_key(path)
        except OSError as e:
            return None, f"the store key can't be made: {type(e).__name__}"
    return pile_mod.read_key(path)


def _attach_store(cfg: Config, run: Run) -> dict:
    """B1: this session's store, attached at check-in. The pile hashed once
    (`pile.py`), keyed with this box's store key, for the xref index the
    record vouches for; built when it isn't there or was built for another
    index, key or set of repos. One `store` row says which, with the store
    file's own SHA-256, so a later verb uses it only while it still hashes to
    that, and what the build reached: the repos read, the files, the skips by
    reason and each unreachable repo. No index or no key is `unreachable`,
    said, and the box opens."""
    index, sha, why = seed_mod.read_index(run.rec)
    indexed = False
    if index is None and why == seed_mod.NO_INDEX and cfg.index_repos:
        _index_box(cfg, run)
        index, sha, why = seed_mod.read_index(run.rec)
        indexed = index is not None
    key, kwhy = _store_key(cfg, cfg.box, make=True)
    if index is None or key is None:
        row = {"state": UNREACHABLE, "why": why or kwhy}
    elif cfg.store_background and not pile_mod.cache_ready(
        index, _repos(cfg, index), pile_mod.store_path(cfg.box), sha, key
    ):
        path = pile_mod.store_path(cfg.box)
        if not pile_mod.building(cfg.box):
            spec = pile_mod.build_spec(
                cfg.box,
                sha,
                _repos(cfg, index),
                cfg.keys.parent / pile_mod.key_name(cfg.box),
            )
            _spawn_build(spec)
        row = {
            "state": "building",
            "why": "the store builds in the background; it attaches when ready",
            "index_sha256": sha,
            "store": path.name,
        }
    else:
        path = pile_mod.store_path(cfg.box)
        try:
            db, how = pile_mod.open_cache(index, _repos(cfg, index), path, sha, key)
            try:
                tally = pile_mod.Cached(db, key).tally()
            finally:
                db.close()
            row = {
                "state": "attached",
                "why": "",
                "how": how,
                "index_sha256": sha,
                "store": path.name,
                "store_sha256": pile_mod.file_sha256(path),
                **tally,
            }
        except Exception as e:  # noqa: BLE001 — a store that can't be built is a state
            row = {"state": UNREACHABLE, "why": f"{type(e).__name__}: {str(e)[:120]}"}
    if indexed:
        row["indexed"] = sorted(cfg.index_repos)
    run.rec.append("store", run.sys, where=row.get("store"), **row)
    return row


def _index_box(cfg: Config, run: Run) -> None:
    """The xref index over `cfg.index_repos`, written through this run's record
    as `onescript.xref index --record` writes it: both outputs, then one `xref`
    row saying which repos at which commits (`at: checkin`)."""
    from . import xref

    idx = xref.build(sorted((n, Path(p)) for n, p in cfg.index_repos.items()))
    ib = (xref.canon(idx) + "\n").encode()
    rb = xref.report(idx).encode()
    run.rec.write_file(run.sys, seed_mod.INDEX, ib, provenance="authored")
    run.rec.write_file(run.sys, "xref/report.txt", rb, provenance="authored")
    run.rec.append(
        "xref",
        run.sys,
        at="checkin",
        inputs=[
            {k: i.get(k) for k in ("repo", "state", "commit", "on_remote")}
            for i in idx["inputs"]
        ],
        index_sha256=record.h256(ib),
        report_sha256=record.h256(rb),
    )


def _spawn_build(spec: Path) -> None:
    """Start the background builder, detached: it outlives this verb."""
    import subprocess

    subprocess.Popen(
        [sys.executable, "-m", "onescript.pile", "--build-store", str(spec)],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _finish_building(cfg: Config, run: Run, att: dict) -> dict:
    """A `building` store, attached once its file is ready for this index and
    key: the `attached` row this verb writes (the builder writes no rows).
    Not ready yet: the `building` row as it stands."""
    index, sha, _ = seed_mod.read_index(run.rec)
    key, _ = _store_key(cfg, cfg.box)
    path = pile_mod.store_path(cfg.box)
    if index is None or key is None or sha != att.get("index_sha256"):
        return att
    if not pile_mod.cache_ready(index, _repos(cfg, index), path, sha, key):
        return att
    db = pile_mod.connect_ro(path)
    try:
        tally = pile_mod.Cached(db, key).tally()
    finally:
        db.close()
    row = {
        "state": "attached",
        "why": "",
        "how": "built in the background",
        "index_sha256": sha,
        "store": path.name,
        "store_sha256": pile_mod.file_sha256(path),
        **tally,
    }
    return run.rec.append("store", run.sys, where=path.name, **row)


def _open_store(cfg: Config, box: Path, run: Run | None = None):
    """The one store opener, for this check-in's store (`run`) or another
    box's: (Cached, None), or (None, why), never an exception.

    The store counts only while its box's last `store` row says `attached`,
    the row's `index_sha256` is the box's pile pointer now and the store's own
    meta says the same, and the file hashes to the row (`pile.open_store`:
    read once, read-only, capped). Another box is read through a record that
    can't write, and is trusted only when its chain verifies; what comes back
    of it is a fixed reason, never its own `why`. Its store key must be in
    this operator's keys dir, or it is `unreachable`, said. Nothing in
    another box is written or created."""
    box = Path(box)
    if run is not None:
        rec = run.rec
        rows = seed_mod.since_boot(rec.rows())
    else:
        if not (box / "record.jsonl").is_file():
            return None, "no record in that box"
        rec = record.Record(box, lambda: "", "", object())
        if rec.verify_chain():
            return None, "its record's chain doesn't verify"
        rows = rec.rows()
    att = next((r for r in reversed(rows) if r.get("kind") == "store"), None)
    if att is not None and att.get("state") == "building":
        if run is None:
            return None, "that session's store is still building"
        att = _finish_building(cfg, run, att)
        if att.get("state") == "building":
            return None, "the store is still building; it attaches when ready"
    if att is None:
        if run is not None:
            return None, "no store attached at this check-in"
        return None, "that session attached no store"
    if att.get("state") != "attached":
        if run is not None:
            return None, att.get("why") or "the store is unreachable"
        return None, "that session's store is unreachable"
    index, sha, why = seed_mod.read_index(rec)
    if index is None:
        return None, why if run is not None else "its index isn't vouched for"
    if att.get("index_sha256") != sha:
        return None, "the store was built for another index; check in again"
    key, why = _store_key(cfg, box)
    if key is None:
        return None, why
    return pile_mod.open_store(
        pile_mod.store_path(box), att.get("store_sha256"), key, sha
    )


def _breaks_before(cfg: Config, key: bytes | None) -> list[str] | None:
    """The chain's and the anchor's breaks as the record stands BEFORE a verb
    appends its own rows (W4 break 7). Read through a reader with no write
    token; None when there is no key to check the anchor with, or the keys
    file sits where `_open` will refuse it anyway."""
    if key is None or cfg.keys.resolve().is_relative_to(cfg.box.resolve()):
        return None
    try:
        rec = record.Record(
            cfg.box, lambda: "", "", object(), anchor=cfg.keys.parent / ANCHOR
        )
    except (PermissionError, OSError):
        return None
    return rec.verify_chain() + rec.verify_anchor(key)


def checkout(
    cfg: Config,
    argv: Iterable[str] = ("checkout",),
    *,
    human_key: Path | None = None,
    basis=None,
    read_head=None,
) -> dict:
    """Reverse, then the morning screen. `human_key` is the human's key in a
    file outside the box (0600, `read_human_key`): with it, wager outcomes the
    sealed tip covers count, and GRADES shows their scores. `basis` is the R3
    hook; on a box holding table rows or table wagers `Run.reconciled` binds
    its own (`table.basis` on `Config.beacon`) and ignores this one (R23a).
    `read_head` is the table's ledger reader
    (`table.check_agm_head`); the screen's TABLE section shows its state."""
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    before = _breaks_before(cfg, key)
    run, info = _open(cfg, "checkout", argv)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    rep, screen = run.checkout(
        boot_report=last,
        human_key=key,
        basis=basis,
        read_head=read_head,
        breaks=before,
    )
    out = {"report": rep, "screen": screen, "code": 0}
    if cfg.seal_at_end:
        out["session"] = _close_session(cfg, run)
    return out


def _close_session(cfg: Config, run: Run) -> dict:
    """The session, from its check-in to now, as one bundle: written to the box
    and proposed to Nestor as one draft pair over its hash. One `session` row
    says which, and why when Nestor can't take it; the bundle is on the record
    either way, so the proposal can be made again."""
    rows = run.rec.rows()
    start = session_mod.current(rows)
    if start is None:
        return {"state": EMPTY, "why": "no check-in to close"}
    b = session_mod.bundle(rows, start, record.h256(str(cfg.box.resolve()))[:12])
    data = session_mod.canonical(b)
    subj = session_mod.subject(data)
    where = f"{session_mod.DIR}/{start}.json"
    run.rec.write_file(run.sys, where, data, provenance="authored")
    pair = _propose_session(cfg, b, subj)
    run.rec.append(
        session_mod.KIND,
        run.sys,
        where=where,
        subject=subj,
        bundle_sha256=record.h256(data),
        actions=len(b["actions"]),
        pair=pair,
        **{"from": start},
    )
    return {"subject": subj, "actions": len(b["actions"]), "pair": pair}


def _propose_session(cfg: Config, bundle: dict, subj: str) -> dict:
    """Nestor's draft pair for the bundle (`nestor.session_seal.propose`), or
    `unreachable` and why. A draft is a proposal: Nestor lets a machine make
    one, and only the person seals or denies it."""
    if cfg.nestor_db is None:
        return {"state": UNREACHABLE, "why": NO_STORE}
    try:
        from nestor import cascade, home_paths  # type: ignore[import-not-found]
        from nestor import session_seal as ns  # type: ignore[import-not-found]
        from nestor.sqlite_store import SqliteStore  # type: ignore[import-not-found]
    except ImportError as e:
        return {"state": UNREACHABLE, "why": f"Nestor isn't importable: {e}"}
    try:
        cascade.set_ledger_path(home_paths.ledger_for(cfg.nestor_db))
        store = SqliteStore(str(cfg.nestor_db))
        store.init_db()
        got = ns.propose(bundle, origin="one-script", store=store)
    except Exception as e:  # noqa: BLE001 — Nestor's refusal is a state
        return {"state": UNREACHABLE, "why": f"{type(e).__name__}: {str(e)[:160]}"}
    if got.get("target_text") != subj:
        return {"state": UNREACHABLE, "why": "Nestor hashed the bundle differently"}
    return {"state": "proposed", "id": got["id"], "status": got["status"]}


def _read_session_seals(cfg: Config, run: Run) -> dict:
    """Each checked-out session the person has sealed in Nestor since, read
    back as a `seal` row (`_seal`, checked like any Nestor seal). Denied and
    unanswered sessions stay as they are: on the record, never served."""
    rows = run.rec.rows()
    held = run.held(rows)[0]
    waiting = [x for x in session_mod.subjects(rows).values() if x not in held]
    out = {"sealed": len(session_mod.subjects(rows)) - len(waiting), "waiting": 0}
    if not waiting:
        return out
    if cfg.nestor_db is None or cfg.keyring is None:
        out["waiting"] = len(waiting)
        out["why"] = "no Nestor store or keyring to read seals from"
        return out
    for subj in waiting:
        try:
            found = nestor_seal.pairs_for(cfg.nestor_db, subj)
        except nestor_seal.Unverifiable:
            found = []
        if found and _seal(run, cfg, subj, None, None, None)["kind"] == "seal":
            out["sealed"] += 1
        else:
            out["waiting"] += 1
    return out


def needs(
    cfg: Config,
    argv: Iterable[str] = ("needs",),
    *,
    basis=None,
    human_key: Path | None = None,
    read_head=None,
) -> dict:
    """What the morning screen's NEEDS YOU section would list right now, as
    data: `{"needs": [line, ...], "count": n}` (an empty list is the screen's
    "nothing"). The lines are `view.needs_you`, the same ones `checkout` prints,
    drawn from the record by the same reverse report. Read-only, like `pooled`
    and `resume`: it writes the `invocation` row every verb writes and nothing
    else, in particular no `reconcile` row, so it does not check the run out and
    it needs no open box.

    SPEC R13: the lines also carry the wagers (`wager <id> <state> ...`: a
    forecast waiting, an outcome waiting on a seal or its tip, a score code
    made, an unreachable seal) and, on a table box, the table's (`table verdict
    session ...`, `table agm_head <state> ...`; `view.table_lines`). `human_key`
    is a file outside the box, as `checkout` takes it: with it a sealed tip is
    checked and scores show; without, a wager reads as waiting and a session's
    tip as not checked here. `read_head` is the ledger reader for the agm_head
    check."""
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    before = _breaks_before(cfg, key)
    run, info = _open(cfg, "needs", argv)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    rep = run.reconciled(basis=basis, human_key=key, read_head=read_head, breaks=before)
    lines = view.needs_you(rep, last, run.claims, run.acts, surface=True)
    out = {"needs": lines, "count": len(lines), "code": 0}
    rows = run.rec.rows()
    if any(r["kind"] in (wb_mod.KIND, wb_mod.MISS) for r in rows):
        out["writeback"] = wb_mod.needs(rows, run.held(rows)[0])
    return out


def peek(
    cfg: Config,
    *,
    human_key: Path | None = None,
    read_head=None,
    basis=None,
) -> dict:
    """`needs` that writes NOTHING (Q1): no `invocation` row, no `pin`, no
    directory, so the record is byte-identical after it and its tip does not move
    under a human who is about to sign it. The same lines as `needs` (the wagers,
    the table's sessions and agm_head, a broken chain as `table unreachable`),
    for a second box a seat only watches. A box with no record is
    `{"needs": [], "count": 0, "empty": True}`; a refusal is as `needs`'s. The
    law, the keys file and the human key are checked as any verb checks them."""
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    if not (cfg.box / "record.jsonl").is_file():
        return {"needs": [], "count": 0, "empty": True, "code": 0}
    if not cfg.keys.is_file():
        # R23a: `_keys` would mint a keys file; peek writes nothing, ever
        why = f"unreachable: no keys file at {cfg.keys}; nothing was read or made"
        return {"needs": [], "count": 0, "state": UNREACHABLE, "why": why, "code": 1}
    before = _breaks_before(cfg, key)
    run, info = _open(cfg, "peek", ("peek",), write=False)
    if run is None:
        return info
    last = _last_boot(run.rec.rows())
    _from_record(run)
    rep = run.reconciled(basis=basis, human_key=key, read_head=read_head, breaks=before)
    lines = view.needs_you(rep, last, run.claims, run.acts, surface=True)
    return {"needs": lines, "count": len(lines), "peek": True, "code": 0}


SEED_TOP = (
    12  # baseline (D12): the ranked chunks a seed row keeps; scope draws from these
)


def _seed_from_store(cfg: Config, run: Run, bite: str, ws: list[str]) -> dict:
    """The store's half of a seed: the words' counts (D6), the ranked chunks
    (by their own SHA-256; the store holds keyed word hashes, never a chunk's
    text), the kept pairs, and the bite's sentences said before. `store` is
    `{state, why}`; `counts` is `Cached.counts`, read only while attached."""
    c, why = _open_store(cfg, cfg.box, run)
    if c is None:
        return {"store": {"state": UNREACHABLE, "why": why}}
    try:
        fix = c.repair(bite)  # rung 2 first: the rank reads the repaired bite
        r, q = c.rank(fix["bite"]), c.quotes(bite)  # quotes are your words, exact
        n = c.counts(ws)
    finally:
        c.db.close()
    if UNREACHABLE in (r["state"], q["state"], fix["state"], n["state"]):
        bad = next(x for x in (fix, r, q, n) if x["state"] == UNREACHABLE)
        return {"store": {"state": UNREACHABLE, "why": bad.get("why", "")}}
    return {
        "counts": n,
        "store": {"state": "attached", "why": ""},
        "repair": {"state": fix["state"], "repairs": fix["repairs"]},
        "rank_state": r["state"],
        "rank": [
            {k: h[k] for k in ("repo", "path", "line", "chunk", "score")}
            for h in r["hits"][:SEED_TOP]
        ],
        "pairs": r.get("pairs", {}),
        "quotes": q["quotes"],
    }


def _hidden(cfg: Config, ws: list[str], out: dict) -> dict:
    """The seed row's half of `out`: every word of the bite keyed with the
    box's store key (`writeback.word_hash`), never written as itself ("a
    baseline holds hashes only, never content"; "the plaintext is never
    written", next-pile.md). The caller still gets its words back in the
    answer; only the record is hashed. With no store key the words are left
    out of the row, and the row says so."""
    key, _ = _store_key(cfg, cfg.box)
    if key is None:
        h = None
    else:

        def h(w: str) -> str:
            return wb_mod.word_hash(w, key)

    row = dict(out)
    if h is None:
        row.update(words=[], hidden="no store key: the bite's words are left out")
        for k in ("keys", "missed", "pairs"):
            row.pop(k, None)
        if "repair" in row:
            row["repair"] = {"state": row["repair"]["state"], "repairs": []}
        return row
    row["words"] = sorted(h(w) for w in ws)
    if "keys" in row:
        row["keys"] = [{**k, "word": h(k["word"])} for k in row["keys"]]
    if "missed" in row:
        row["missed"] = sorted(h(w) for w in row["missed"])
    if "pairs" in row:
        row["pairs"] = {
            " ".join(h(t) for t in p.split(" ")): v for p, v in row["pairs"].items()
        }
    if "repair" in row:
        row["repair"] = {
            "state": row["repair"]["state"],
            "repairs": [[h(a), h(b), d] for a, b, d in row["repair"]["repairs"]],
        }
    return row


# ── seed ────────────────────────────────────────────────────────────────────
def seed(
    cfg: Config,
    bite: str,
    repos: dict[str, Path] | None = None,
    argv: Iterable[str] = ("seed",),
    *,
    exact: bool = False,
) -> dict:
    """The first bite's words against the box. Once per check-in: the first
    input seeds, and a second is refused with the first's row. `repos` names
    where an indexed repo is read from; any it leaves out are looked for
    beside willow-bot. Writes one `seed` row, the bite as its hash and its
    words; reads the box and writes nothing else.

    D6 (the operator, 2026-10-18: "C"): the counts come from the session's
    store (chunks and files per word, `counted_by: store`). The exact files
    and lines are the scan (`seed.py`), read only on request (`exact`) or
    when no store is attached, and then `counted_by: scan` says why."""
    run, info = _open(cfg, "seed", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seed")) is not None:
        return refusal
    first = next(
        (r for r in seed_mod.since_boot(run.rec.rows()) if r["kind"] == "seed"), None
    )
    if first is not None:
        why = f"this check-in is already seeded (row {first['n']}); the first bite seeds once"
        run.rec.append("refused", run.sys, at="seed", reason=why)
        return _refused(why, 1)
    ws = seed_mod.words(bite)
    if not ws:
        run.rec.append("refused", run.sys, at="seed", reason="no words in the bite")
        return _refused("a bite with no words seeds nothing", 1)
    bite_hash = record.h256(bite)
    # rung 1: a held write-back for this exact bite answers it, before ranking
    back = wb_mod.read_back(run, bite_hash)
    index, sha, why = seed_mod.read_index(run.rec)
    if index is None:
        out = {"state": UNREACHABLE, "why": why, "keys": [], "missed": ws}
    else:
        out = _seed_from_store(cfg, run, bite, ws)
        counts = out.pop("counts", None)
        if counts is not None and not exact:
            out.update(counts, counted_by="store")
        else:
            where = _repos(cfg, index)
            where.update({n: Path(p) for n, p in (repos or {}).items()})
            out.update(seed_mod.scan(index, where, ws), counted_by="scan")
            if not exact:
                out["counted_why"] = f"no store attached: {out['store']['why']}"
        out["index_sha256"] = sha
    asked = jeles_mod.escalate(cfg.jeles, bite, out)
    if asked is not None:
        out["jeles"] = asked
    rung = ladder.step(run.rec.rows(), ladder.bite_key(bite))
    out["writeback"] = back
    if back["state"] == wb_mod.HELD:
        out["answered_by"] = back["answered_by"]
    lost = (
        wb_mod.at_seed(run, bite, ws, lambda: _open_store(cfg, cfg.box, run))
        if index is not None and back["state"] != wb_mod.HELD
        else {"state": wb_mod.NONE}
    )
    row = run.rec.append(
        "seed",
        run.sys,
        where=seed_mod.INDEX,
        bite_hash=bite_hash,
        **_hidden(cfg, ws, out),
        **rung,
    )
    if lost["state"] == wb_mod.MISS:
        out["miss"] = wb_mod.log_miss(run, row, lost["lost"])
    if rung["ladder"] == "offer":  # offered, never applied (§0.3)
        rung["offer_row"] = _offer(run, rung, row["n"])["n"]
    return {**out, **rung, "row": row["n"], "code": 0}


def _offer(run: Run, rung: dict, seed_row: int) -> dict:
    """The third ask's `offer` row: the key and the count, never the bite."""
    return run.rec.append(
        "offer",
        run.sys,
        key=rung["bite_key"],
        asked=rung["asked"],
        seed_row=seed_row,
        offer=ladder.OFFER,
    )


def decline_offer(
    cfg: Config,
    key: str,
    argv: Iterable[str] = ("decline",),
    *,
    proof: str | None = None,
) -> dict:
    """A No to an offer: one `offer_declined` row with its key, so the offer is
    not made again for that key. Only a key the record has offered.

    D5 (the operator, 2026-10-18: "C"): a No only narrows, so it takes effect
    unsigned, but it says who said it. Without a proof it is `said_by: caller`,
    unsealed, and the morning screen counts it; with the human's proof over
    `decline:<key>` it is `said_by: human`, sealed, and it stands over an
    unsealed No for the same key."""
    run, info = _open(cfg, "decline", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "decline", key=key)) is not None:
        return refusal
    rows = run.rec.rows()
    if not any(r["kind"] == "offer" and r.get("key") == key for r in rows):
        why = "no offer with that key on record"
        run.rec.append("refused", run.sys, at="decline", reason=why, key=key)
        return _refused(why, 1)
    sealed = False
    if proof is not None:
        human = run.keys.get(gate.HUMAN)
        if not human or not gate.verify_seal(ladder.decline_subject(key), proof, human):
            why = "the proof doesn't seal this No (decline:<key>, the human's key)"
            run.rec.append("refused", run.sys, at="decline", reason=why, key=key)
            return _refused(why, 1)
        sealed = True
    if ladder.declined(rows, key) and not (sealed and not ladder.sealed_no(rows, key)):
        return {"declined": key, "already": True, "code": 0}
    row = run.rec.append(
        "offer_declined",
        run.sys,
        key=key,
        said="no",
        said_by="human" if sealed else "caller",
        sealed=sealed,
        **({"proof": proof} if sealed else {}),
    )
    return {"declined": key, "row": row["n"], "sealed": sealed, "code": 0}


# ── lookup: across sessions, merged by hash ─────────────────────────────────
# What a session's lookup answer may say and keep: the ranker's variant, the
# named cold-start step (NOT a state; see states.ESCALATED_COLD_START), and two
# of the three states. Anything else is recorded as unreachable.
STATE_WORDS = (RANKED, ESCALATED_COLD_START, EMPTY, UNREACHABLE)


def _session_tag(key: bytes, box: Path) -> str:
    """A session's name in this box's `lookup` row: a short keyed hash of its
    box's path, so the row never holds another box's absolute path."""
    h = hashlib.blake2b(str(Path(box).resolve()).encode(), digest_size=6, key=key)
    return "box-" + h.hexdigest()


def lookup(
    cfg: Config,
    bite: str,
    boxes: Iterable[Path] = (),
    argv: Iterable[str] = ("lookup",),
) -> dict:
    """The bite against this session's store and other sessions' stores,
    merged by chunk hash (`pile.merge`): a view, rebuilt each time, never a
    shared store. Each other store counts only through `_open_store` (its
    chain verified, its row, its index, its key here); one that doesn't is
    `unreachable`, said, and the rest still answer. Each session is
    `{state, why}`. One `lookup` row here: the bite's hash, each session's
    state word only (another box's `why` is never written here), sessions
    named by a keyed hash of their box, and the top chunks."""
    run, info = _open(cfg, "lookup", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "lookup")) is not None:
        return refusal
    return {**_lookup(cfg, run, bite, boxes), "code": 0}


def _lookup(
    cfg: Config, run: Run, bite: str, boxes: Iterable[Path] = (), keep: int = 0
) -> dict:
    """`lookup`'s work on an open run: the merge, and its one `lookup` row.
    The row keeps SEED_TOP chunks; the answer keeps `keep` (default SEED_TOP),
    so a caller fusing a second rank has candidates past the row's."""
    stores, states, tags = {}, {}, {"this": "this"}
    mine, why = _open_store(cfg, cfg.box, run)
    if mine is None:
        states["this"] = {"state": UNREACHABLE, "why": why}
    else:
        stores["this"] = mine
    tag_key = pile_mod.read_key(cfg.keys.parent / pile_mod.key_name(cfg.box))[0]
    for i, box in enumerate(boxes):
        name = str(Path(box))
        tags[name] = _session_tag(tag_key, box) if tag_key else f"box-{i}"
        c, why = _open_store(cfg, Path(box))
        if c is None:
            states[name] = {"state": UNREACHABLE, "why": why}
        else:
            stores[name] = c
    try:
        out = pile_mod.merge(stores, bite)
    finally:
        for c in stores.values():
            c.db.close()
    out["sessions"] = {**states, **out["sessions"]}
    out["hits"] = out["hits"][: max(keep, SEED_TOP)]
    top = out["hits"][:SEED_TOP]
    run.rec.append(
        "lookup",
        run.sys,
        bite_hash=record.h256(bite),
        sessions={
            tags[n]: s["state"] if s["state"] in STATE_WORDS else UNREACHABLE
            for n, s in out["sessions"].items()
        },
        top=[[h["chunk"], h["seen_in"]] for h in top],
    )
    return out


# ── prompt: every prompt is a bite (workflow.md §2c), served into the turn ──
PROMPT_TOP = 8  # the chunks one prompt's bite serves; the lookup row keeps SEED_TOP
EMBED_POOL = 100  # the words' top candidates the embedder re-ranks (rung 3)
PROMPT_RETURN = (
    "Context drawn by code from the box for this prompt, by the prompt's own "
    "words. Answer the human as you would; where you lean on a table, cite its "
    "id. Nothing here is a request, and nothing here is sealed."
)


def prompt(
    cfg: Config,
    text: str,
    max_chars: int | None = None,
    argv: Iterable[str] = ("prompt",),
) -> dict:
    """One prompt as a bite: `lookup` against this session's store (one
    `lookup` row: the bite's hash, never its words), and its top chunks served
    as untrusted, unsealed tables, re-read at their indexed blobs the way
    `serve` reads a chunk. The document is what `prompt.py` frames into the
    turn. Three states, never collapsed: no open check-in, no serve key or no
    index is `unreachable`; a bite that finds nothing is `empty`; over the
    caller's cap is `empty` ("narrow the stack"), never truncated."""

    rung = {"used": "words", "why": "no embedder set" if cfg.embed is None else ""}

    def doc(state: str, why: str, tables: list | None = None) -> dict:
        out = {
            "state": state,
            "why": why,
            "return": PROMPT_RETURN,
            "tables": tables or [],
        }
        if state == POPULATED:
            out["seal"] = served.UNSEALED
            out["rung"] = dict(rung)
        return {"doc": out, "state": state, "why": why, "code": 0}

    run, info = _open(cfg, "prompt", argv)
    if run is None:
        return doc(UNREACHABLE, info.get("refused") or "the box can't be opened")
    if (refusal := _need_open(run, "prompt")) is not None:
        return doc(UNREACHABLE, refusal["refused"])
    if not text.strip():
        return doc(EMPTY, "the prompt has no words to look up")
    if run.serve_key is None:
        return doc(UNREACHABLE, "no serve key this check-in")
    out = _lookup(cfg, run, text, keep=EMBED_POOL if cfg.embed else 0)
    hits = out["hits"]
    if not hits:
        why = next((s["why"] for s in out["sessions"].values() if s.get("why")), "")
        return doc(EMPTY, why or "the bite found nothing in this box")
    index, _, _ = seed_mod.read_index(run.rec)
    if index is None:
        return doc(UNREACHABLE, "no index this check-in; seed once first")
    repos = _repos(cfg, index)
    texts: dict = {}

    def read(ch: dict):
        if ch["sha256"] not in texts:
            texts[ch["sha256"]] = pile_mod.read_chunk(index, repos, ch)
        return texts[ch["sha256"]]

    def chunk_of(h: dict) -> dict:
        repo, path, line = h["where"][0]
        return {"repo": repo, "path": path, "line": line, "sha256": h["chunk"]}

    if cfg.embed is not None:
        embed = pile_mod.wordllama_embed(cfg.embed)
        if embed is None:
            rung["why"] = "the embedder can't be loaded from its local files"
        else:
            cands = []  # the pile's candidate shape: ((repo, path, line, sha), text)
            for h in hits:
                ch = chunk_of(h)
                t, _ = read(ch)
                if t is not None:  # a chunk that can't be read can't be embedded
                    cands.append(((ch["repo"], ch["path"], ch["line"], h["chunk"]), t))
            by = {h["chunk"]: h for h in hits}
            ranked = [w[3] for w in embed(text, cands)]
            hits = [by[k] for k in pile_mod.rrf([h["chunk"] for h in hits], ranked)]
            rung["used"] = "fused"
    tables = []
    for h in hits[:PROMPT_TOP]:
        t = {"rows": [], "source": {"chunk": chunk_of(h)}}
        tables.append(served._chunk_entry(t, h["chunk"], run.serve_key, set(), read))
    tables.sort(key=lambda t: t["id"])
    res = doc(POPULATED, "", tables)
    if max_chars is not None and len(record.canon(res["doc"])) > max_chars:
        return doc(EMPTY, "the scope is too large for this model; narrow the stack")
    return res


# ── forge_bite: the Forge's first bite, with this session's pile as its box ──
class _NeedsYou(Exception):
    """The Forge asked something only the maker answers."""

    def __init__(self, card: dict):
        super().__init__(card.get("surface", ""))
        self.card = card


class _CardResponder:
    """The Forge's `Responder`, never answering for the maker: a choice or a
    confirm comes back as a card, before anything is sealed (the Forge's
    `_full_socratic` asks the responder first and seals only what it returns).
    No positional default (forge docs/design/the-positional-default.md)."""

    def confirm(self, prompt: str) -> bool:
        raise _NeedsYou({"ask": "confirm", "surface": prompt, "options": []})

    def choose(self, decision):
        raise _NeedsYou(
            {
                "ask": "choose",
                "surface": decision.surface,
                "options": [
                    {"label": o.label, "tradeoff": o.tradeoff} for o in decision.options
                ],
            }
        )


def _forge_on_path(where: Path | None) -> None:
    """Put a Forge checkout first on the import path, once, when it holds the
    engine; anything else changes nothing and the import says what it finds."""
    if where is None or not (Path(where) / "forge" / "entry.py").is_file():
        return
    root = str(Path(where).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def forge_bite(
    cfg: Config,
    bite: str,
    *,
    project_id: str = "one-script",
    builder_id: str = "willow",
    root: Path | None = None,
    argv: Iterable[str] = ("forge",),
) -> dict:
    """The Forge's `open_bite` with `PileBox` over this session's store as its
    tier 2 (D9, the operator, 2026-10-18: "A": live in willow-bot only; the
    Forge never imports willow). The Forge is imported here, softly: not
    importable is `unreachable`, said, and so is its own refusal (Nestor
    absent). A question for the maker is `needs_you` with the card; the
    responder never chooses. `root` is the Forge's checkpoint root (default:
    the Forge's own). One `forge_bite` row: the bite's hash, the state, the
    major, and the box's state word."""
    run, info = _open(cfg, "forge", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "forge")) is not None:
        return refusal
    from .box_lookup import PileBox

    out: dict = {}
    box = None
    _forge_on_path(cfg.forge)
    try:
        from forge import entry  # type: ignore[import-not-found]
    except ImportError as e:
        out = {"state": UNREACHABLE, "why": f"the Forge isn't importable: {e}"}
    else:
        c, why = _open_store(cfg, cfg.box, run)
        box, store_why = PileBox(c), why
        kw = {} if root is None else {"root": Path(root)}
        if (
            cfg.seal_at_end
            and "defer_seal" in inspect.signature(entry.open_bite).parameters
        ):
            kw["defer_seal"] = True  # card answers are drafts; one seal, at the end
        try:
            e = entry.open_bite(
                bite,
                project_id=project_id,
                builder_id=builder_id,
                responder=_CardResponder(),
                box=box,
                **kw,
            )
        except _NeedsYou as n:
            out = {"state": "needs_you", "card": n.card}
        except entry.EntryError as err:
            out = {"state": UNREACHABLE, "why": f"the Forge refused: {err}"}
        else:
            d = e.to_dict()
            found = e.major or e.answer or e.candidates
            out = {"state": POPULATED if found else EMPTY, "entry": d}
        finally:
            if c is not None:
                c.db.close()
    if box is not None:
        out["box"] = {"state": box.last_state, "why": box.last_why}
        if store_why and box.last_state == UNREACHABLE:
            out["box"]["why"] = f"no store attached: {store_why}"
    major = (out.get("entry") or {}).get("major")
    run.rec.append(
        "forge_bite",
        run.sys,
        bite_hash=record.h256(bite),
        state=out["state"],
        major=major,
        box=(out.get("box") or {}).get("state", UNREACHABLE),
    )
    return {**out, "code": 0}


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
    by, match, upto = spec.get("by") or [], spec.get("match", {}), spec["upto"]
    rows = run.rec.rows()[: upto + 1]
    seed = spec.get("seed")
    if not by and not seed:
        raise ValueError("name a stack: W's to group by, or the seed's top chunks")
    tables, card = [], {"subject": None, "ids": [], "stack": [], "joins": {}}
    if by:
        stack = served.piles(rows, *by)
        tables, card = served.choose(stack, **match), served.propose(stack, **match)
    if seed:
        n = seed["row"]
        if (
            isinstance(n, bool)
            or not isinstance(n, int)
            or not (0 <= n < len(rows))
            or rows[n].get("n") != n
            or rows[n].get("kind") != "seed"
        ):
            raise ValueError(f"row {n} is not a seed row at or before row {upto}")
        chunks = served.chunk_tables(rows[n], seed["top"])
        more = served.propose_chunks(chunks)
        tables = tables + chunks
        card = {
            **card,
            "ids": card["ids"] + more["ids"],
            "stack": card["stack"] + more["stack"],
            "joins": {
                **card["joins"],
                **({"file": more["joins"]} if more["joins"] else {}),
            },
        }
        card["subject"] = served.scope_subject(card["ids"]) if card["ids"] else None
    return tables, card


def scope(
    cfg: Config,
    by: Iterable[str] = ("who",),
    match: dict | None = None,
    upto: int | None = None,
    argv: Iterable[str] = ("scope",),
    seed_top: int | None = None,
    max_chars: int | None = None,
) -> dict:
    """Code proposes the stack. Returns the card for the human and the exact
    `subject` whose seal makes it a scope, plus the `spec` that names the stack:
    hand it back to `serve` unchanged. With `seed_top`, the stack also holds
    this check-in's seed's top chunks of the repos (by=() for those alone).
    The card gives each chunk's size in characters, read at its blob (never
    its text), and the total; with `max_chars`, whether the stack fits
    (`_sizes`)."""
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
    if seed_top is not None:
        sd = next((r for r in seed_mod.since_boot(rows) if r["kind"] == "seed"), None)
        if sd is None:
            return _refused("no seed in this check-in; the first bite seeds", 1)
        spec["seed"] = {"row": sd["n"], "top": seed_top}
    try:
        tables, card = _spec_tables(run, spec)
    except ValueError as e:
        return _refused(str(e), 1)
    if max_chars is not None or any(served.is_chunk(t) for t in tables):
        card = _sizes(cfg, run, tables, card, max_chars)
    run.rec.append("scope", run.sys, where=card["subject"], spec=spec, card=card)
    return {"spec": spec, **card, "code": 0}


def _sizes(cfg: Config, run: Run, tables: list[dict], card: dict, max_chars) -> dict:
    """The card, with each chunk's `chars` read at its blob (or `unreadable`
    and why) and a `size`: the chunks' total, and `served_chars`, the length
    of the document serve would write for this stack once the scope is sealed,
    measured by serve's own builder (no file, no row). With `max_chars`, `fits`
    says whether that is within the cap; serve over the cap is `empty`."""
    reads: dict[str, tuple] = {}
    index, _, _ = seed_mod.read_index(run.rec)
    repos = _repos(cfg, index) if index is not None else {}

    pile_read = (
        (lambda ch: pile_mod.read_chunk(index, repos, ch))
        if index is not None
        else None
    )
    read_any = jeles_mod.chunk_reader(cfg.jeles, pile_read)

    def reader(ch):
        k = served.table_id({"rows": [], "source": {"chunk": ch}})
        if k not in reads:
            reads[k] = read_any(ch)
        return reads[k]

    stack = []
    for entry in card["stack"]:
        t = next((t for t in tables if served.table_id(t) == entry["id"]), None)
        if t is not None and served.is_chunk(t):
            text, why = reader(t["source"]["chunk"])
            entry = {**entry, "chars": None if text is None else len(text)}
            if text is None:
                entry["unreadable"] = why
        stack.append(entry)
    size: dict = {
        "chunk_chars": sum(e.get("chars") or 0 for e in stack),
        "unreadable": sum(1 for e in stack if "unreadable" in e),
        "max_chars": max_chars,
    }
    try:
        held, unchecked = run.held()
        subj = card["subject"]
        doc = served._serve(
            run.rec.rows(),
            tables,
            card["ids"],
            run.serve_key,
            sys.maxsize,
            ({*held, subj}, {k: v for k, v in unchecked.items() if k != subj}),
            reader,
        )
        if doc["state"] != POPULATED:
            raise gate.Refused(doc["why"])
        size["served_chars"] = len(record.canon(doc))
    except Exception as e:  # noqa: BLE001 — a size that can't be measured is said
        size["served_chars"] = None
        size["why"] = f"not measured: {str(e)[:120] or type(e).__name__}"
    if max_chars is not None:
        n = size["served_chars"]
        size["fits"] = None if n is None else n <= max_chars
    return {**card, "stack": stack, "size": size}


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
        state = {"state": UNREACHABLE} if why.startswith("unreachable") else {}
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


def seal_session(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    pair: object = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The person's seal over one session's bundle (`session:<sha256>`), as
    `seal_scope` takes a scope's: the check-in reads a Nestor seal back on its
    own, and this is the same seal by hand."""
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal", subject=subject)) is not None:
        return refusal
    if subject not in session_mod.subjects(run.rec.rows()).values():
        row = run.rec.append(
            "refused",
            run.sys,
            at="seal",
            reason="not a session of this box",
            subject=subject,
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
    reader = None
    failed: list[tuple[dict, str]] = []
    if any(served.is_chunk(t) for t in tables):
        index, _, _ = seed_mod.read_index(run.rec)
        pile_read = None
        if index is not None:
            repos = _repos(cfg, index)

            def pile_read(ch):
                text, why = pile_mod.read_chunk(index, repos, ch)
                if text is None:
                    failed.append((ch, why))  # a pile chunk gone is tombstoned
                return text, why

        if pile_read is not None or cfg.jeles is not None:
            # A Jeles chunk that fails is served `unreachable` with its why,
            # never tombstoned: it is Jeles's record, not a file at a blob.
            reader = jeles_mod.chunk_reader(cfg.jeles, pile_read)

    session = None
    if cfg.seal_at_end:
        session = session_mod.servable(run.rec.rows(), run.held()[0])
    doc = run.serve(
        tables, card["ids"], max_chars=max_chars, read_chunk=reader, session=session
    )
    if failed:
        _tombstones(cfg, run, index, repos, failed)
    return {"doc": doc, "state": doc["state"], "why": doc["why"], "code": 0}


def _tombstones(cfg: Config, run: Run, index: dict, repos: dict, failed) -> None:
    """A `tombstone` row for each chunk serve found gone (`ladder.reason`),
    once per chunk per check-in, with where the same SHA-256 sits now
    (`forward`) when this check-in's store holds it and it reads back there."""
    store, looked = None, None
    for ch, why in failed:
        sha = ch.get("sha256")
        root = repos.get(ch.get("repo"))
        blob = next(
            (
                f.get("git_blob")
                for f in index.get("arrival", {})
                .get(ch.get("repo"), {})
                .get("files", [])
                if f.get("path") == ch.get("path")
            ),
            None,
        )

        def blob_there(root=root, blob=blob):
            try:
                return blob in seed_mod._blobs(Path(root), [blob])
            except (seed_mod.GitError, OSError, TypeError):
                return None

        r = ladder.reason(why, blob_there)
        if r is None or ladder.tombstoned(run.rec.rows(), sha):
            continue
        if looked is None:
            store, looked = _open_store(cfg, cfg.box, run)
            looked = looked or "store"
        fwd = None
        if store is not None:
            fwd = ladder.forward(
                ch,
                store.where.values(),
                lambda c: pile_mod.read_chunk(index, repos, c),
            )
        run.rec.append(
            "tombstone",
            run.sys,
            where=f"{ch.get('repo')}:{ch.get('path')}:{ch.get('line')}",
            chunk=sha,
            repo=ch.get("repo"),
            path=ch.get("path"),
            line=ch.get("line"),
            reason=r,
            forward=fwd,
            looked=looked,
        )
    if store is not None:
        store.db.close()


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
    if why is None and _held(run, REJECT + subject):
        why = "the human rejected this proposal; a rejection stands (seal a new one)"
    if why is not None:
        row = run.rec.append("refused", run.sys, at="seal", reason=why, subject=subject)
        return {"row": row, "sealed": False, "written": None, "code": 1}
    row = _seal(run, cfg, subject, proof, pair, verifiers)
    if row["kind"] != "seal":
        return {"row": row, "sealed": False, "written": None, "code": 1}
    wrote = run.write_proposal(subject)
    back = None
    if wrote["kind"] == "write":
        prop, _ = run.find_proposal(subject)
        back = wb_mod.from_proposal(run, prop, _spec_tables) if prop else None
    return {
        "row": row,
        "sealed": True,
        "written": wrote if wrote["kind"] == "write" else None,
        "write": wrote,
        "writeback": back,
        "code": 0 if wrote["kind"] == "write" else 1,
    }


#: A rejection is the human's seal over this prefix and a proposal's subject
#: (`reject:proposal:<hash>`): the same row kind, proof and checks as a seal,
#: over a subject a seal over the proposal can never be (flatten F3, "1 a").
REJECT = "reject:"


def _held(run: Run, subject: str) -> bool:
    """A seal row over `subject` whose own proof (or pair) re-checks now."""
    rows = run.rec.rows()
    good, _ = run.seal_rows(rows)
    return any(
        r["kind"] == "seal" and r.get("subject") == subject and r["n"] in good
        for r in rows
    )


def reject_proposal(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    pair: object = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's rejection of one proposal (`subject` is `proposal:<hash>`,
    or `reject:proposal:<hash>` as the CLI takes it): their proof, or a sealed
    Nestor pair, over `reject:<subject>`. Recorded as durably as a seal, as a
    seal row over that subject; nothing is written. Refused for a proposal not
    on record, or one the human already sealed (a seal stands)."""
    if subject.startswith(REJECT):
        subject = subject[len(REJECT) :]
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "reject", subject=subject)) is not None:
        return refusal
    _, why = run.find_proposal(subject)
    if why is None and _held(run, subject):
        why = "the human sealed this proposal; a seal stands"
    if why is not None:
        row = run.rec.append(
            "refused", run.sys, at="reject", reason=why, subject=subject
        )
        return {"row": row, "rejected": False, "code": 1}
    row = _seal(run, cfg, REJECT + subject, proof, pair, verifiers)
    if row["kind"] != "seal":
        return {"row": row, "rejected": False, "code": 1}
    return {"row": row, "rejected": True, "code": 0}


def writeback_seal(
    cfg: Config,
    subject: str,
    *,
    proof: str | None = None,
    pair: object = None,
    verifiers: Iterable[str] | None = None,
    argv: Iterable[str] = ("seal",),
) -> dict:
    """The human's seal over one write-back's exact subject (`writeback:<hash>`),
    by the human's proof or a sealed Nestor pair whose conclusion is the subject
    (read from `cfg.nestor_db`, never written). Only a candidate on record whose
    row hashes to the subject is sealable. Held, the next seed of that exact
    bite stops at rung 1 (`writeback.read_back`)."""
    run, info = _open(cfg, "seal", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "seal", subject=subject)) is not None:
        return refusal
    if not isinstance(subject, str) or not subject.startswith(wb_mod.PREFIX):
        row = run.rec.append(
            "refused",
            run.sys,
            at="seal",
            reason="not a write-back subject",
            subject=subject,
        )
        return {"row": row, "sealed": False, "state": None, "code": 1}
    if subject not in wb_mod.subjects(run.rec.rows()):
        row = run.rec.append(
            "refused",
            run.sys,
            at="seal",
            reason="no such write-back on record",
            subject=subject,
        )
        return {"row": row, "sealed": False, "state": None, "code": 1}
    row = _seal(run, cfg, subject, proof, pair, verifiers)
    st = wb_mod.state_of(run, subject)
    ok = row["kind"] == "seal" and st["state"] == wb_mod.HELD
    return {"row": row, "sealed": ok, **st, "code": 0 if ok else 1}


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


def rules(cfg: Config, subject: str, argv: Iterable[str] = ("rules",)) -> dict:
    """Turn a human-sealed `rules/*.md` `@markdownai` proposal into `rules` rows,
    one `@phase` each (SPEC R15, T-mai-2; `rules.from_sealed`). Refused, recorded,
    when the proposal is not sealed, not a pass, an own idea, not under rules/ or
    not an `@markdownai` document. Writes no seal and not the file
    (`seal_proposal` does). Ends with the rescope step, as `lore` does."""
    from . import rules as rules_rows

    run, info = _open(cfg, "rules", argv)
    if run is None:
        return info
    if (refusal := _need_open(run, "rules", subject=subject)) is not None:
        return refusal
    made = rules_rows.from_sealed(run, subject)
    if made and made[0]["kind"] == "refused":
        return {"refused": made[0]["reason"], "row": made[0], "code": 1}
    steps = [
        "python3 -m onescript scope --by what --match what=rules",
        "python3 -m onescript seal <the new subject> --proof P",
        "python3 -m onescript serve --by what --match what=rules"
        " --upto <its upto> --max-chars N",
    ]
    prop, _ = run.find_proposal(subject)
    return {
        "rules": [{"n": r["n"], "phase": r["where"]} for r in made],
        "dropped": rules_rows.dropped(prop["data"]) if prop else {},
        "count": len(made),
        "rescope": {
            "why": "the new rules rows moved the what=rules pile: a scope drawn at "
            "the new tip serves empty until the human seals it"
            if made
            else "no new rows: every phase was already rules; no pile moved",
            "steps": steps if made else [],
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
                "state": UNREACHABLE,
                "why": unchecked[r["n"]],
            }
    if seal is None:
        if newer is not None:
            return {
                "resume": None,
                "state": UNREACHABLE,
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
    out["writeback"] = wb_mod.from_escalate(run, out, task, _spec_tables)
    return {**out, "code": 0}


# ── the forecast lane (SPEC R1: forge-convergence.md:643-645, plus seal_tip
# and reconcile, without which no outcome counts) ────────────────────────────
OUTCOME_KEYS = ("path_index", "path", "winner")  # reverse._match_distribution
COMMITTED_KEYS = {"sha256", "beacon_then", "target"}
TABLE_KEYS = {"world", "sheets_sha"}  # a table forecast's world (SPEC W2 S2)
#: ... and the drand round whose randomness seeds its session (W4 break 1)
TABLE_FORECAST_KEYS = TABLE_KEYS | {"beacon_round"}


def _deck_problem(
    deck_: object, distribution: list[dict], by: str, run: Run
) -> tuple[str | None, int | None]:
    """(why a deck forecast does not stand, None) or (None, the reader's
    latest round): the question and paths (`deck.check_forecast`), a round
    still ahead (`_future_round`), and one forecast per round, question and
    role."""
    from . import deck  # deck imports api

    why = deck.check_forecast(deck_, distribution)
    if why is not None:
        return why, None
    why, latest = _future_round(deck_["beacon_round"], run)
    if why is not None:
        return why, None
    for r in run.rec.rows():
        d = r.get("deck") if r["kind"] == "wager" else None
        if isinstance(d, dict) and d == deck_ and r.get("by") == by:
            return (
                f"the {by} already forecast {deck_['question']!r} for round "
                f"{deck_['beacon_round']}: one forecast per round, question and role"
            ), None
    return None, latest


def _market_problem(
    market_: object, distribution: list[dict], by: str, run: Run
) -> tuple[str | None, int | None]:
    """(why a market call does not stand, None) or (None, the drand reader's
    latest round): the call's shape (`market.check_forecast`), made before
    the deadline on its close date by the reader's rounds (`market.in_time`),
    and one call per question and role."""
    from . import market  # market imports api

    why = market.check_forecast(market_, distribution)
    if why is not None:
        return why, None
    newest, why = beacon.latest(run.beacon)
    if why is not None:
        return f"a market call is timed by the drand reader: {why}", None
    if not market.in_time(newest, market_["close_date"]):
        return (
            f"too late: a call on {market_['close_date']} is committed before "
            f"{market.DEADLINE_UTC}:00 UTC that day, before the market opens "
            f"(the drand reader is at round {newest})"
        ), None
    for r in run.rec.rows():
        m = r.get("market") if r["kind"] == "wager" else None
        if isinstance(m, dict) and m == market_ and r.get("by") == by:
            return (
                f"the {by} already called this ({market_['symbol']} "
                f"{market_['kind']} on {market_['close_date']}): one call per "
                "question and role"
            ), None
    return None, newest


def _future_round(rnd: object, run: Run) -> tuple[str | None, int | None]:
    """(why `rnd` is not a drand round still ahead, None) or (None, the
    reader's latest round). Ahead means the operator's reader can NOT read it
    yet: `rnd` > the newest round it says it reads, AND `read(rnd)` does not
    answer (R23a: a reader whose `latest` lags must not reopen grinding). No
    box clock is asked: a clock set back (`--now`) would let a round already
    out pass. A round already out is a seed anyone may know; with no reader,
    nothing can be shown to be ahead, and the forecast is refused."""
    if isinstance(rnd, bool) or not isinstance(rnd, int) or rnd < 1:
        return "beacon_round is a drand round number", None
    newest, why = beacon.latest(run.beacon)
    if newest is None:
        return f"{why}: no round can be shown to be still ahead", None
    if rnd <= newest:
        return (
            f"beacon round {rnd} is out already (the reader reads up to {newest}): "
            "its randomness may be known; name a round still ahead"
        ), None
    try:  # a reader whose `latest` lags may still serve the round: ask it
        served = run.beacon(rnd)
    except Exception:  # not served: not out, as far as this reader knows
        served = None
    if isinstance(served, dict) and beacon.is_randomness(served.get("randomness")):
        return (
            f"beacon round {rnd} is out already: the reader serves its randomness "
            f"(though it says its latest is {newest}); name a round still ahead"
        ), None
    return None, newest


def forecast(
    cfg: Config,
    wid: str,
    distribution: list[dict],
    *,
    by: str = "desk",
    committed: dict | None = None,
    table: dict | None = None,
    deck: dict | None = None,
    market: dict | None = None,
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
    what a table forecast is about, exactly {world, sheets_sha, beacon_round}:
    the world and its odds' inputs (`peaks.world_key`), and a drand round
    that has not fired yet by this box's clock, whose randomness must seed
    the session that answers it (rival R1, H7; W4 break 1). A table session
    on another world, other sheets or another seed can't supply its outcome
    (`table.propose_outcome`). `deck` names a deck forecast instead, exactly
    {question, beacon_round} (flatten F6, `deck.py`): one of the predictions
    game's questions, over the paths yes and no, and a round still ahead; one
    forecast per round, question and role, so no role holds both sides.
    `market` names a market call instead (`market.py`): {symbol, kind,
    close_date[, threshold]}, over yes and no, made before the market opens
    on its close date by the drand reader's rounds; one call per question
    and role. A forecast is about one of table, deck or market."""
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
        or set(table) != TABLE_FORECAST_KEYS
        or not all(isinstance(table[k], str) and table[k] for k in TABLE_KEYS)
    ):
        why = (
            f"table names exactly {sorted(TABLE_FORECAST_KEYS)}: world and "
            "sheets_sha as text, beacon_round a future drand round"
        )
    elif table is not None and (
        (got := _future_round(table["beacon_round"], run))[0] is not None
    ):
        why = got[0]
    elif table is not None and any(
        r["kind"] == "wager"
        and (r.get("table") or {}).get("beacon_round") == table["beacon_round"]
        for r in run.rec.rows()
    ):
        # R23a: one table forecast per round, or a hedge's loser is never graded
        why = (
            f"a table forecast already names beacon round {table['beacon_round']}: "
            "one forecast per round"
        )
    elif sum(x is not None for x in (table, deck, market)) > 1:
        why = (
            "a forecast is about one of a table world, a deck question or a market call"
        )
    elif deck is not None and (got := _deck_problem(deck, distribution, by, run))[0]:
        why = got[0]
    elif (
        market is not None
        and (got := _market_problem(market, distribution, by, run))[0]
    ):
        why = got[0]
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
        deck=deck,
        market=market,
        # R23a: the reader's newest round as the forecast was made, a fact
        # the reader checked; the round named must be after it (a market
        # call: the deadline must be after its next round)
        beacon_latest=got[1] if (table, deck, market) != (None, None, None) else None,
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
    # R23a: the reader's newest round as the human seals, so "the round fired
    # after the tip seal" rests on the reader, never on this box's clock
    newest, _ = beacon.latest(cfg.beacon)
    row = run.seal_tip(
        proof,
        run.keys[gate.HUMAN],  # type: ignore[arg-type]
        {"n": int(n), "hash": hsh},
        beacon_latest=newest,
    )
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
        key = bytes.fromhex(path.read_text(encoding="utf-8").strip())
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
    as `checkout` takes it: on a table box the Run's own hook, never this
    one (R23a)."""
    # W4 break 7: the anchor as it stood before this verb's own rows
    before = _breaks_before(cfg, read_human_key(human_key, cfg.box)[0])
    run, info = _open(cfg, "reconcile", argv)
    if run is None:
        return info
    key, why = read_human_key(human_key, cfg.box)
    if why is not None:
        return _refused(why, 2)
    rep = run.reconciled(human_key=key, basis=basis, breaks=before)
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
