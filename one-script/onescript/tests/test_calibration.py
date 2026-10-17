"""The vendored calibration math stays the Forge's (SPEC R2).

`onescript/calibration.py` vendors `brier`, `log_score`, `BIN_EDGES` and `bins`
from `Forge/forge/calibration.py` so onescript keeps importing only the
standard library. A vendored copy is a second copy; this file is what keeps it
from drifting: where the Forge is in reach, both give the same numbers on a
grid. The Forge is found at $ONESCRIPT_FORGE, else beside willow-bot
(../Forge), and loaded from that file by path, never from whatever `forge`
happens to be installed.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import calibration  # noqa: E402

WILLOW_BOT = Path(__file__).resolve().parents[3]
FORGE = Path(
    os.environ.get("ONESCRIPT_FORGE") or WILLOW_BOT.parent / "Forge"
).expanduser()
FORGE_CAL = FORGE / "forge" / "calibration.py"


def _forge():
    if not FORGE_CAL.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_forge_calibration", FORGE_CAL)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


forge = _forge()
needs_forge = pytest.mark.skipif(
    forge is None, reason=f"the Forge is not in reach at {FORGE_CAL}"
)

GRID = [i / 100 for i in range(101)] + [1e-12, 1 - 1e-12, 0.12, 0.88, 1 / 3]


@needs_forge
def test_brier_and_log_score_match_the_forges_on_a_grid():
    for p in GRID:
        for o in (True, False):
            assert calibration.brier(p, o) == forge.brier(p, o), (p, o)
            assert calibration.log_score(p, o) == forge.log_score(p, o), (p, o)


@needs_forge
def test_bins_match_the_forges():
    assert calibration.BIN_EDGES == forge.BIN_EDGES
    pairs = [(p, i % 3 == 0) for i, p in enumerate(GRID) if p >= 0.5]
    assert calibration.bins(pairs) == forge.bins(pairs)
    assert calibration.bins([]) == forge.bins([])


def test_the_vendored_copy_names_its_source():
    head = Path(calibration.__file__).read_text()[:1200]
    assert "Forge/forge/calibration.py" in head


def test_fold_keeps_brier_and_log_score():
    """SPEC R5 / C2: restating to the believed side moves the pair into the
    bins and changes neither score."""
    for p in GRID:
        for o in (True, False):
            q, k = calibration.fold((p, o))
            assert q >= 0.5
            # abs_tol: 1 - (1 - 1e-12) is not 1e-12 in floating point
            assert math.isclose(
                calibration.brier(q, k), calibration.brier(p, o), abs_tol=1e-12
            )
            assert math.isclose(
                calibration.log_score(q, k), calibration.log_score(p, o), abs_tol=1e-9
            )


# ── N1: a fingerprint that never skips ───────────────────────────────────────
#: sha256 of each function's source as it stands in Forge/forge/calibration.py
#: (box build J1, 2026-10-17). onescript vendors the first three; `summary`
#: is not vendored and is pinned so a change on the Forge's side shows too.
FORGE_SOURCES = {
    "brier": "71aff868508156cd3ce17a3263ffca986828251fcbe9eba2cc58abcdcfec3a8d",
    "log_score": "11934d8d0dd289729affedb4557d5ed08c6e9c4a534334f871ed02864f2e1ada",
    "bins": "d8977a7a5798487deeb52586d838fa401beb6286ae043f70858bea18b425db52",
    "summary": "727dab89e8cf4686b0574c37df4350eea8f23298357207be127027348246cf04",
}
VENDORED = ("brier", "log_score", "bins")


def _source_sha(fn) -> str:
    import hashlib
    import inspect

    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()


def test_the_vendored_sources_are_the_pinned_forge_sources():
    """Runs with or without the Forge: the copy is the Forge's text, byte for
    byte, as pinned; the constants beside it too."""
    for name in VENDORED:
        assert _source_sha(getattr(calibration, name)) == FORGE_SOURCES[name], name
    assert calibration.BIN_EDGES == (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
    assert calibration._EPS == 1e-9


@needs_forge
def test_the_forges_sources_are_still_the_pinned_ones():
    """If this fails the Forge moved: diff, reconcile the copy, re-pin."""
    for name, sha in FORGE_SOURCES.items():
        assert _source_sha(getattr(forge, name)) == sha, name
    assert forge.BIN_EDGES == calibration.BIN_EDGES and forge._EPS == calibration._EPS
