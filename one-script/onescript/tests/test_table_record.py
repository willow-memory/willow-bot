"""The table writes to this record (flatten F2 = SPEC-rival R3).

the-table's `proof.py` plays every registered game into a one-script box
through its `OneScriptSink` and the host shim (`the_table/host.py`), checks
the chain with this package's `verify_chain` and `verify_anchor`, and shows an
edited copy refused. This runs it against THIS checkout (`ONESCRIPT_HOME`), so
a change here that breaks the table's rows fails here.

Needs safe-app-store beside the bot (`ONESCRIPT_SAFE_APP_STORE` names it
otherwise) with a the-table that carries `host.py`, and willows-grove's law;
skipped, said, without them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ONESCRIPT = Path(__file__).resolve().parents[2]
SAFE = Path(
    os.environ.get("ONESCRIPT_SAFE_APP_STORE")
    or ONESCRIPT.parents[1] / "safe-app-store"
).expanduser()
TABLE_APP = SAFE / "apps" / "the-table"
GROVE = Path(
    os.environ.get("ONESCRIPT_GROVE") or ONESCRIPT.parents[1] / "willows-grove"
).expanduser()

pytestmark = [
    pytest.mark.skipif(
        not (TABLE_APP / "the_table" / "host.py").is_file(),
        reason=f"this the-table has no host.py yet (at {TABLE_APP})",
    ),
    pytest.mark.skipif(
        not (GROVE / "governance" / "CONSTITUTION.md").is_file(),
        reason=f"willows-grove's law is not at {GROVE}",
    ),
]


def _proof(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "the_table.proof"],
        cwd=TABLE_APP,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=900,
    )


def test_every_table_game_is_on_this_record_and_the_chain_holds():
    pytest.importorskip(
        "chess", reason="the-table's checkers and backgammon need python-chess"
    )
    got = _proof({"ONESCRIPT_HOME": str(ONESCRIPT), "ONESCRIPT_GROVE": str(GROVE)})
    assert got.returncode == 0, got.stdout[-2000:] + got.stderr[-2000:]
    assert "chain:        HOLDS" in got.stdout
    assert "tampered:     REFUSED" in got.stdout
    assert "PROOF PASSED" in got.stdout


def test_with_no_one_script_the_table_says_so_and_exits_2(tmp_path):
    got = _proof(
        {"ONESCRIPT_HOME": str(tmp_path / "nowhere"), "ONESCRIPT_GROVE": str(GROVE)}
    )
    assert got.returncode == 2
    assert "unreachable" in got.stderr and "PROOF PASSED" not in got.stdout
