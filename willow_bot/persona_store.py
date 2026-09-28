"""Where the voice keeps its counters: inside the box, carried over once.

``quips`` (merged PRs per login → contributor titles), ``sigh`` (CI failure
streaks per PR) and ``rebase_shame`` (force-pushes per branch) each keep a
small SQLite file. They used to hardcode ``~/.willow/``, the retired home:
outside the box, outside ``WILLOW_HOME``, and outside the test sandbox.

They now live in ``$WILLOW_HOME/willow-bot/persona/``. The operator chose to
carry the counts over (2026-09-28): the first time a counter is opened and
its box file does not exist yet, the ``~/.willow`` file of the same name —
if there is one — is copied in with SQLite's online backup (a consistent
copy even if an old process still has it open), and one line is appended to
``persona/migrations.jsonl`` saying what was copied from where. The
``~/.willow`` file is never modified or deleted; removing it is the
operator's call. Once the box file exists, ``~/.willow`` is never read again.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from willow_bot.paths import persona_dir

#: The three counters, by the file name each has always had.
FILES = {
    "contributors": "willow-bot-contributors.db",
    "sigh": "willow-bot-sigh.db",
    "rebase_shame": "willow-bot-rebase-shame.db",
}


def _legacy_dir() -> Path:
    """The retired home the counters used to live in. A function so the
    test floor (tests/conftest.py) can point it away from the real one."""
    return Path.home() / ".willow"


def _record(line: dict) -> None:
    path = persona_dir() / "migrations.jsonl"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _carry_over(legacy: Path, target: Path) -> None:
    """Copy ``legacy`` into ``target`` with SQLite's backup API, via a temp
    file renamed into place so a half-written copy is never mistaken for
    the real one."""
    tmp = target.with_name(f".{target.name}.carry")
    src = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    tmp.replace(target)


def db_path(name: str) -> Path:
    """The box path of counter ``name`` (a key of ``FILES``), created
    parent-first, carried over from ``~/.willow`` on first use. Raises
    ``BoxNotConfigured`` when there is no box — never falls back."""
    filename = FILES[name]
    target = persona_dir() / filename
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_dir() / filename
    if legacy.is_file():
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            _carry_over(legacy, target)
        except (OSError, sqlite3.Error) as exc:
            # Nothing half-copied is left behind; the counter starts in the
            # box and the refusal is on record. The legacy file is untouched.
            target.with_name(f".{target.name}.carry").unlink(missing_ok=True)
            _record({"event": "persona_carry_over", "at": at, "counter": name, "status": "failed",
                     "from": str(legacy), "to": str(target), "error": f"{type(exc).__name__}: {exc}"[:300]})
        else:
            _record({"event": "persona_carry_over", "at": at, "counter": name, "status": "copied",
                     "from": str(legacy), "to": str(target), "bytes": target.stat().st_size})
    return target
