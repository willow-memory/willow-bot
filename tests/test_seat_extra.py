"""The `seat` extra: a provisioned bot venv ships the grove seat's runtime.

The grove CLI seat needs `ratatosk` on the venv bin and `willow_mcp` importable
(its close-out deposit lazy-imports `willow_mcp.onescript_deposit` and
`willow_mcp.db.Store`). These tests pin the declaration, that the names resolve
where the extra is installed, and that the arrows point one way: willow-mcp must
never depend on willow-bot, or `willow-bot[seat]` would be a cycle.

Declaration tests always run (Python >=3.11 for tomllib); resolution tests run
wherever the extra is installed (CI installs it on 3.11+; willow-mcp needs 3.11).
"""

from __future__ import annotations

import importlib
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _seat_extra() -> list[str]:
    tomllib = pytest.importorskip("tomllib", reason="tomllib is Python >=3.11")
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["seat"]


def _name(req: str) -> str:
    return re.split(r"[<>=!~ \[;@]", req, maxsplit=1)[0].lower().replace("_", "-")


def test_seat_extra_declares_the_seat_runtime():
    reqs = {_name(r): r for r in _seat_extra()}
    assert set(reqs) == {"willow-mcp", "willow-ratatosk"}
    for r in reqs.values():
        assert "@" not in r and "git+" not in r, (
            f"direct-URL dep is forbidden on PyPI: {r}"
        )
        assert "<" in r, f"Rule 1: every pin is capped: {r}"


def test_ratatosk_floor_carries_the_flowering_class_flag():
    # `ratatosk --onescript --class flowering` (grove seat flowering turns):
    # --onescript ships in 1.13.0, --class in 1.16.0; 1.7.x has neither.
    req = next(r for r in _seat_extra() if _name(r) == "willow-ratatosk")
    floor = re.search(r">=\s*([0-9.]+)", req)
    assert floor, f"willow-ratatosk needs an explicit floor: {req}"
    assert tuple(int(x) for x in floor.group(1).split(".")) >= (1, 16, 0), req


def test_seat_extra_does_not_name_willow_bot():
    assert "willow-bot" not in {_name(r) for r in _seat_extra()}


def test_willow_mcp_does_not_depend_on_willow_bot():
    try:
        reqs = metadata.requires("willow-mcp") or []
    except metadata.PackageNotFoundError:
        pytest.skip("willow-mcp not installed (seat extra absent)")
    assert not [r for r in reqs if _name(r) == "willow-bot"], (
        "cycle: willow-mcp -> willow-bot"
    )


def test_seat_names_resolve():
    pytest.importorskip("willow_mcp", reason="seat extra not installed")
    importlib.import_module("willow_mcp")
    deposit = importlib.import_module("willow_mcp.onescript_deposit")
    db = importlib.import_module("willow_mcp.db")
    assert deposit is not None
    assert hasattr(db, "Store")
    assert importlib.import_module("ratatosk") is not None


def test_seat_extra_pulls_the_nestor_engine():
    # The deposit's draft goes through decision_propose, which returns
    # `nestor_unavailable` when the Nestor engine is not importable.
    assert any(
        _name(r) == "willow-mcp" and "[nestor]" in r.replace(" ", "")
        for r in _seat_extra()
    ), "seat must name willow-mcp[nestor]"
    pytest.importorskip("willow_mcp", reason="seat extra not installed")
    assert importlib.import_module("nestor") is not None


def test_ratatosk_console_script_is_on_the_venv_bin():
    pytest.importorskip("ratatosk", reason="seat extra not installed")
    venv_bin = Path(sys.executable).parent
    found = (venv_bin / "ratatosk").exists() or shutil.which(
        "ratatosk", path=str(venv_bin)
    )
    assert found, f"no ratatosk console script beside {sys.executable}"
