#!/usr/bin/env python3
"""live_flow: grow the session map at the end of every turn.

Called from a Stop hook (and --flush from SessionEnd). It is a view, never a
gate:
  - the hook process takes only session_id and transcript_path from its
    payload, validates them, hands them to a detached child, and exits 0 at
    once with no output, so it can never block or slow the stop
  - the child reads only the transcript bytes added since its last run (a
    saved offset, complete lines only); the transcript is never re-read
  - before reading on, it checks the file is still the one it was reading:
    the bytes just before the offset must hash as they did. A truncated or
    rewritten transcript stops the map, loudly, in errors.log
  - each turn closed since the last run goes through side_runner.on_turn:
    the record first, then the .drawio view. State is saved after every
    turn, so a failure part way never draws a turn twice
  - the open turn is never drawn: a stop is not always the end of a turn (a
    task wake or a hook push-back continues it). It waits in state until the
    next turn starts, or until --flush. The map runs one turn behind
  - any failure goes to errors.log and is never raised

What it does not do yet (replay covers both):
  - subagent work. Builders finish after the turn that sent them; live
    draws the desk's own turns. `flow.py all` at check-out draws everything
  - a Kart task's final outcome when its status arrives in a later turn

Replay is the judge: the same transcript through `flow.py all` should agree
with the live record; if they differ, replay wins, loudly.

Loki pre-install audit BD1B3052 (FAIL) found: session id path escape,
silent truncation/rewrite, secrets in the verb field, duplicate rows on a
partial failure, payload-in-argv over 128 KiB, unbounded lock queue. Each
is closed below; run_flow.py closes the verb leak.

  hook:   python3 live_flow.py [--flush]   (payload on stdin)
  child:  python3 live_flow.py --work SESSION_ID TRANSCRIPT_PATH [--flush]
State and output: <repo>/.flow/live/<session_id>/
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE.parents[4] / ".flow" / "live"  # willows-grove/.flow/live
SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")  # no '/', no '..'
ANCHOR = 4096  # bytes before the offset that must still hash the same
LOCK_WAIT_S = 20  # a stuck child can't make the queue grow without bound
PENDING_WARN = 5000


def _log(path: Path, msg: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg.rstrip() + "\n")
    except OSError:
        pass


def hook(flush: bool = False) -> None:
    try:
        p = json.loads(sys.stdin.read() or "{}")
        sid, transcript = (
            str(p.get("session_id", "")),
            str(p.get("transcript_path", "")),
        )
        if not SID.match(sid) or not transcript.startswith("/"):
            _log(
                LIVE / "hook-errors.log",
                f"refused payload: session_id={sid[:80]!r} transcript={transcript[:200]!r}",
            )
        else:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--work",
                    sid,
                    transcript,
                ]
                + (["--flush"] if flush else []),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=dict(
                    os.environ,
                    PYTHONDONTWRITEBYTECODE="1",
                    FLOW_ROOT=os.environ.get("FLOW_ROOT", str(Path.home()) + "/"),
                ),
            )
    except Exception:  # a view must never block the stop
        _log(LIVE / "hook-errors.log", traceback.format_exc())
    sys.exit(0)


def work(sid: str, transcript: str, flush: bool = False) -> None:
    if not SID.match(sid):  # checked again: the child can be run by hand
        _log(LIVE / "hook-errors.log", f"refused session_id {sid[:80]!r}")
        return
    out = LIVE / sid
    errors = out / "errors.log"
    try:
        out.mkdir(parents=True, exist_ok=True)
        if out.resolve().parent != LIVE.resolve():
            raise RuntimeError(f"session dir escapes .flow/live: {out.resolve()}")
        with (out / ".lock").open("w") as lock:
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        _log(
                            errors,
                            f"skipped: lock busy {LOCK_WAIT_S}s; the next stop catches up from the saved offset",
                        )
                        return
                    time.sleep(0.2)
            advance(Path(transcript), out, sid, flush)
    except Exception:
        _log(errors, traceback.format_exc())


def _save(path: Path, st: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, sort_keys=True))
    os.replace(tmp, path)


def advance(transcript: Path, out: Path, sid: str, flush: bool = False) -> None:
    sys.path.insert(0, str(HERE))
    import run_flow  # noqa: F401  (patches make_flow for this box, incl. the verb scrub)
    import make_flow
    import side_runner
    from flow import turns_from

    state_path = out / "state.json"
    st = json.loads(state_path.read_text()) if state_path.exists() else {}
    st = {
        "offset": 0,
        "turn": 0,
        "drawn": 0,
        "pending": [],
        "anchor": "",
        "transcript": str(transcript),
        **st,
    }
    if st["transcript"] != str(transcript):
        raise RuntimeError(
            f"transcript path changed: {st['transcript']} -> {transcript}; map stopped"
        )
    with transcript.open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        if size < st["offset"]:
            raise RuntimeError(
                f"transcript truncated: size {size} < offset {st['offset']}; map stopped"
            )
        if st["offset"]:
            f.seek(max(0, st["offset"] - ANCHOR))
            got = hashlib.sha256(f.read(min(ANCHOR, st["offset"]))).hexdigest()
            if got != st["anchor"]:
                raise RuntimeError(
                    "transcript rewritten before the saved offset; map stopped"
                )
        f.seek(st["offset"])
        chunk = f.read()
    end = chunk.rfind(b"\n")
    rows = []
    for line in (
        chunk[: end + 1].decode("utf-8", "replace").splitlines() if end >= 0 else []
    ):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    ev = make_flow.events_from(rows, "desk")
    base = st["turn"]  # turn numbers are local to the chunk; local 0 is the open turn
    for e in ev:
        e["turn"] = base + e["turn"]
    ev = st["pending"] + ev
    late = [e for e in ev if e["turn"] <= st["drawn"]]
    if late:  # events for a turn already drawn (e.g. after a flush): counted, not lost silently
        _log(
            out / "errors.log",
            f"{len(late)} late events for turns already drawn; replay has them",
        )
    ev = [e for e in ev if e["turn"] > st["drawn"]]
    top = max([e["turn"] for e in ev] + [base])
    last = top if flush else top - 1
    turns, _ = turns_from({"events": [e for e in ev if e["turn"] <= last]})
    for t in sorted(turns, key=lambda t: t["turn"]):
        side_runner.on_turn(out, sid, t, {})
        st["drawn"] = t["turn"]
        _save(state_path, st)  # per turn: a failure part way never redraws a turn
    new_offset = st["offset"] + end + 1 if end >= 0 else st["offset"]
    with transcript.open("rb") as f:
        f.seek(max(0, new_offset - ANCHOR))
        anchor = hashlib.sha256(f.read(min(ANCHOR, new_offset))).hexdigest()
    pending = [e for e in ev if e["turn"] > last]
    if len(pending) > PENDING_WARN:
        _log(
            out / "errors.log",
            f"open turn holds {len(pending)} events; it draws when the turn closes",
        )
    st.update(offset=new_offset, turn=top, pending=pending, anchor=anchor)
    _save(state_path, st)


if __name__ == "__main__":
    flush = "--flush" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--flush"]
    if len(args) == 3 and args[0] == "--work":
        work(args[1], args[2], flush)
    else:
        hook(flush)
