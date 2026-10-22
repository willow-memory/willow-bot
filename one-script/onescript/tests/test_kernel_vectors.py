"""kernel.py's vectors are current, and the ports answer them.

`kernel_vectors/gen.py` reads its cases out of kernel.py. If kernel.py
changes and cases.json isn't regenerated, every port is being held to the old
kernel, so the first test fails, naming the command that fixes it. The second
runs `compare.py` (vectors, a whole session, each box verified by every
implementation) for every port whose toolchain is here: Java, TypeScript, C#.
With none it skips and says so, since a toolchain is the machine's, not the
code's.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

VEC = Path(__file__).resolve().parents[2] / "kernel_vectors"
KERNEL = VEC.parent / "kernel.py"


def test_the_vectors_are_kernel_pys_as_it_is_now(tmp_path):
    pinned = json.loads((VEC / "cases.json").read_text())
    assert pinned["kernel_sha256"] == hashlib.sha256(KERNEL.read_bytes()).hexdigest(), (
        "kernel.py changed since the vectors were made: run"
        " `python3 kernel_vectors/gen.py` and commit cases.json and kernel_tables.json"
    )
    got = subprocess.run(
        [sys.executable, str(VEC / "gen.py"), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    for name in ("cases.json", "kernel_tables.json"):
        assert (tmp_path / name).read_bytes() == (VEC / name).read_bytes(), (
            f"{name} isn't what gen.py makes from this Python: regenerate it"
        )


@pytest.mark.skipif(
    not any(shutil.which(t) for t in ("javac", "node", "dotnet")),
    reason="no JDK, Node or .NET on this machine: no port is checked here",
)
def test_every_port_here_answers_every_vector_and_each_box_every_way():
    """compare.py skips a port whose toolchain isn't here, and says so."""
    got = subprocess.run(
        [sys.executable, str(VEC / "compare.py")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert got.returncode == 0, got.stdout[-3000:] + got.stderr[-2000:]
    assert "unexplained differences: 0" in got.stdout
