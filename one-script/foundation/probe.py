"""Run under one interpreter: what the hook's canonical bytes, record-line parse
and realpath give for the shared cases. Prints ASCII-only JSON.

    pythonX probe.py [cases.pkl] > result.json

`canon` and VAULT are the record-lookup hook's (hook.py at aea0c64); the bare
hook that replaced it hashes nothing. Stdlib only.
"""

import hashlib
import json
import os
import pickle
import sys
import tempfile
from pathlib import Path

VAULT = "sean-data-vault"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def outcome(fn):
    try:
        return fn()
    except Exception as e:
        return "!" + type(e).__name__


def parse(line):
    """The hook's lookup: a line is a JSON object or the record is garbled."""
    row = json.loads(line)
    if not isinstance(row, dict):
        return "not-an-object"
    return hashlib.sha256(canon(row).encode()).hexdigest()


def realpaths():
    """Symlinks into the vault, chains, loops, dangling, relative `..`."""
    paths = {}
    with tempfile.TemporaryDirectory() as d:
        root = Path(os.path.realpath(d))
        (root / VAULT / "sub").mkdir(parents=True)
        (root / "work").mkdir()
        links = {
            "into": root / VAULT,
            "chain1": root / "chain2",
            "chain2": root / "into",
            "loopA": root / "loopB",
            "loopB": root / "loopA",
            "dangling": root / "nowhere",
            "work/up": Path(".."),
            "rel": Path(VAULT) / "sub",
        }
        for name, target in links.items():
            (root / name).symlink_to(target)
        probes = [
            "into/k",
            "chain1/k",
            "loopA/k",
            "dangling/k",
            "work/up/" + VAULT + "/k",
            "work/up/into/../work",
            "rel/../k",
            "work/./../" + VAULT,
            "work//k",
        ]
        for p in probes:
            real = outcome(lambda p=p: os.path.realpath(os.path.join(str(root), p)))
            if real.startswith("!"):
                paths[p] = real
            else:
                rel = os.path.relpath(real, str(root))
                paths[p] = [rel, VAULT in Path(real).parts]
    return paths


if __name__ == "__main__":
    with open(sys.argv[1] if len(sys.argv) > 1 else "cases.pkl", "rb") as f:
        cases = pickle.load(f)
    result = {
        "version": sys.version.split()[0],
        "objects": [
            outcome(lambda o=o: hashlib.sha256(canon(o).encode()).hexdigest())
            for o in cases["objects"]
        ],
        "lines": [outcome(lambda s=s: parse(s)) for s in cases["lines"]],
        "paths": realpaths(),
    }
    print(json.dumps(result))
