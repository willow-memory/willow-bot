"""opening.py -- the box's front door: the config, the keys, the law, and the
one opening every verb goes through (its `invocation` and `pin` rows), with
nothing behind it. A verb that only reads and looks up (`prompt`) opens here
and never loads the check-in, the gates, the ladder or the turn (`run.py`);
`api._open` opens here too and wraps what it gets in a `Run`. Moved verbatim
out of `api.py` and `boot.py`, which re-export every name.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from . import clock as clock_mod
from . import record, stamp

PKG = Path(__file__).resolve().parent  # the version every row carries (run.PKG)


def bot_paths():
    """The bot's box rule, one copy (flatten F-17): ``willow_bot.paths`` from
    the willow-bot checkout the one script lives in."""
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from willow_bot import paths  # noqa: PLC0415

    return paths


def _run(
    argv: list[str],
    cwd: str,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run a command. `env` is added to a copy of this process's environment for
    the child only; the calling process's own environment is never changed."""
    child = {**os.environ, **env} if env else None
    p = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child,
    )
    lines = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode, lines[-1] if lines else ""


def _git(repo: str, *args: str) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "LC_ALL": "C"},  # F17: parsed output, one locale
        )
    except OSError:  # no git on this machine
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


DESK = ("desk", "claude")


ANCHOR = "anchor.json"  # the sealed tip, beside the keys: outside the box


SERVE_KEY = "serve.key"  # this session's id key, beside the keys: outside the box


#: Set in the tests gate's child. A checkin started under it never runs the
#: tests gate again: the gate runs the suite, the suite runs checkin, and an
#: unguarded loop forked until the box ran out of memory (2026-10-06, Kart
#: Z7X2TJQX, a mutant with the keys refusal removed).
NESTED = "ONESCRIPT_IN_TESTS_GATE"


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
        _, last = _run([str(exe), "--version"], ".", timeout=30)
        m = re.search(r"\d+\.\d+(?:\.\d+)?", last)
        return m.group(0) if m else last

    return version_of


def _read(path: Path) -> tuple[str, str | None]:
    """Text and its hash, or ("", None) when the input is missing: recorded, not guessed."""
    if not path.exists():
        return "", None
    data = path.read_bytes()
    return data.decode("utf-8"), record.h256(data)


def _refused(why: str, code: int) -> dict:
    return {"refused": why, "code": code}


def closed_why(rows: list[dict]) -> str | None:
    """Why no motion is allowed, from the record alone (None when the box is
    open): no check-in, a hard close, or a check-out after the last check-in.
    The one copy of the rule; `api._closed_why` adds the boot report beside it."""
    i = next(
        (k for k in range(len(rows) - 1, -1, -1) if rows[k]["kind"] == "boot"), None
    )
    if i is None:
        return "no check-in on record"
    if rows[i]["hard_close"]:
        return "the last check-in hard-closed"
    if any(r["kind"] == "reconcile" for r in rows[i + 1 :]):
        return "the run checked out after its last check-in"
    return None


def need_open(run, at: str, **fields) -> dict | None:
    """A refusal, recorded, unless the box is open. `run` is anything with a
    record and its own stamp (`rec`, `sys`): a `Run`, or an `Opened`."""
    why = closed_why(run.rec.rows())
    if why is None:
        return None
    run.rec.append("refused", run.sys, at=at, reason=why, **fields)
    return _refused(f"{why}; nothing moves until a check-in opens", 1)


_NOW = object()


def anchor_of(rec: record.Record) -> dict | None:
    try:
        return rec.anchor()
    except (ValueError, OSError):
        return None


def pin(rec: record.Record, who, anchor: dict | None | object = _NOW) -> dict | None:
    """The `pin` row, once (`Run.pin` says why): the tip the human had sealed
    when this code first wrote to the box."""
    if any(r["kind"] == "pin" for r in rec.rows()):
        return None
    a = anchor_of(rec) if anchor is _NOW else anchor
    if not isinstance(a, dict):
        return rec.append("pin", who, tip_n=None)
    return rec.append("pin", who, tip_n=a["n"], tip_hash=a["hash"], proof=a["proof"])


class Opened:
    """An opened box with nothing behind it: the record, the run's own stamp,
    the keys and the law, and this check-in's serve key."""

    def __init__(self, cfg: Config, keys: dict[str, bytes], law: dict, clock):
        self.keys, self.law = keys, law
        self.version = record.version_of(PKG)
        self.rec = record.Record(
            cfg.box, clock, self.version, stamp.token(), cfg.keys.parent / ANCHOR
        )
        self.sys = stamp.system()
        self.serve_key = _load_serve_key(cfg)


def open_box(
    cfg: Config, cmd: str, argv: Iterable[str], *, write: bool = True
) -> tuple[Opened | None, dict]:
    """Open the box and write the `invocation` row: the argv, the root, the
    box, and the hash of every input read. Returns (opened, info), or (None, a
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
    opened = Opened(cfg, keys, law, clock)
    if not write:  # `peek`: a box to read through; no invocation, no pin, no row
        return opened, {"ci_text": ci_text, "nested": False}
    opened.rec.append(
        "invocation",
        opened.sys,
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
            "onescript": opened.version,
            "git_head": _git(str(cfg.root), "rev-parse", "HEAD") or None,
            "grove_head": _git(str(cfg.grove), "rev-parse", "HEAD") or None,
            "python": f"{sys.executable} {sys.version.split()[0]}",
            "venv_ruff": _in_venv(cfg.venv)("ruff"),
        },
        trace_ids=len(law["trace_ids"]),
        nested=nested,  # the tests gate is skipped, and says so
    )
    pin(opened.rec, opened.sys)  # once per box: the first verb pins the old tip
    return opened, {"ci_text": ci_text, "nested": nested}
