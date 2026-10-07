#!/usr/bin/env python3
"""Map every Claude Code session on the box: run_flow.py over each transcript.

Idempotent and resumable. A session whose map already carries the
transcript's current sha prefix is skipped, so a rerun picks up where a
deadline stopped it (Kart kills a task at 300 s and retries it, so every run
here must be safe to repeat). Live transcripts (written in the last
LIVE_S seconds) are skipped, not mapped half-written.

Writes OUT/<project>/<uuid>/session-flow.{json,md,mmd} and OUT/index.json,
with one row per transcript: mapped / skipped-current / skipped-live /
failed (with why) / not-reached (deadline). Nothing is ever folded.

  python3 batch_flow.py PROJECTS_DIR OUT_DIR [DEADLINE_S]
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

LIVE_S = 600
HERE = Path(__file__).resolve().parent
# Same stamp run_flow writes: a map from older code is re-made, not kept.
FLOW_VERSION = hashlib.sha256(
    b"".join((HERE / n).read_bytes() for n in ("make_flow.py", "run_flow.py"))
).hexdigest()[:16]


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> None:
    projects, out = Path(sys.argv[1]), Path(sys.argv[2])
    deadline = time.monotonic() + (float(sys.argv[3]) if len(sys.argv) > 3 else 240)
    out.mkdir(parents=True, exist_ok=True)
    idx_path = out / "index.json"
    index = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    now = time.time()
    for t in sorted(projects.glob("*/*.jsonl")):
        key = f"{t.parent.name}/{t.stem}"
        if time.monotonic() > deadline:
            index.setdefault(key, {})["state"] = index.get(key, {}).get(
                "state", "not-reached"
            )
            continue
        if now - t.stat().st_mtime < LIVE_S:
            index[key] = {"state": "skipped-live"}
            continue
        dest = out / t.parent.name / t.stem
        sha = sha16(t)
        prev = dest / "session-flow.json"
        if prev.exists():
            try:
                old = json.loads(prev.read_text())
                if (
                    old.get("transcript_sha256_16") == sha
                    and old.get("flow_version") == FLOW_VERSION
                ):
                    index[key] = dict(index.get(key, {}), state="mapped", sha=sha)
                    continue
            except ValueError:
                pass
        sub = t.parent / t.stem / "subagents"
        args = [sys.executable, str(HERE / "run_flow.py"), str(t)]
        if sub.is_dir():
            args.append(str(sub))
        args.append(str(dest))
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=max(5, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired:
            index[key] = {"state": "not-reached", "why": "deadline mid-run"}
            continue
        if r.returncode == 0:
            index[key] = {
                "state": "mapped",
                "sha": sha,
                "summary": r.stdout.strip().split(" -> ")[0],
            }
        else:
            index[key] = {
                "state": "failed",
                "sha": sha,
                "why": (r.stderr.strip().splitlines() or ["?"])[-1][:200],
            }
    idx_path.write_text(
        json.dumps(dict(sorted(index.items())), indent=1, sort_keys=True) + "\n"
    )
    states: dict[str, int] = {}
    for v in index.values():
        states[v["state"]] = states.get(v["state"], 0) + 1
    print(json.dumps(states, sort_keys=True))


if __name__ == "__main__":
    main()
