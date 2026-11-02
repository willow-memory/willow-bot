"""foundation's cases file is read as plain data only: a planted pickle that
names a function is refused before anything runs, and the cases gen.py makes
still load whole, through probe.py as an interpreter runs it."""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

FOUNDATION = Path(__file__).resolve().parents[2] / "foundation"
sys.path.insert(0, str(FOUNDATION))
import plain  # noqa: E402


class Planted:
    def __reduce__(self):
        return (os.system, ("touch planted-ran",))


def test_a_planted_pickle_is_refused_and_nothing_runs(tmp_path):
    bad = tmp_path / "cases.pkl"
    bad.write_bytes(pickle.dumps({"objects": [Planted()], "lines": []}))
    got = subprocess.run([sys.executable, str(FOUNDATION / "probe.py"), str(bad)],
                         cwd=tmp_path, capture_output=True, text=True)  # fmt: skip
    assert got.returncode != 0 and "only plain data loads" in got.stderr
    assert not (tmp_path / "planted-ran").exists()


def test_the_cases_gen_makes_load_whole(tmp_path):
    cases = tmp_path / "cases.pkl"
    subprocess.run([sys.executable, str(FOUNDATION / "gen.py"), str(cases)],
                   cwd=tmp_path, check=True, capture_output=True)  # fmt: skip
    with open(cases, "rb") as f:
        want = pickle.load(f)  # gen.py's own file, made just now
    # byte for byte, since the cases hold NaN, which never equals itself
    assert pickle.dumps(plain.load(cases), 4) == pickle.dumps(want, 4)
    got = subprocess.run([sys.executable, str(FOUNDATION / "probe.py"), str(cases)],
                         cwd=tmp_path, check=True, capture_output=True, text=True)  # fmt: skip
    assert len(json.loads(got.stdout)["objects"]) == len(want["objects"])
