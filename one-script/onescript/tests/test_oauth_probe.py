"""The OAuth probe's wagers stand, and its settling never hides drift.

`scripts/oauth/oauth_probe.py` forecasts every probe into the ledger before it
runs. Each distribution has to pass `reverse.check_distribution`, and each
must declare `drift`, so runs that disagree can still be sealed as an
outcome. Live servers aren't needed here: `settle` is pure.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import reverse  # noqa: E402

_PROBE = Path(__file__).resolve().parents[2] / "scripts" / "oauth" / "oauth_probe.py"
_spec = importlib.util.spec_from_file_location("oauth_probe", _PROBE)
probe = importlib.util.module_from_spec(_spec)
sys.modules["oauth_probe"] = probe  # its dataclasses look themselves up here
_spec.loader.exec_module(probe)


def test_every_wager_stands_and_declares_drift():
    for name, dist in probe.FORECASTS.items():
        assert reverse.check_distribution(dist) is None, name
        assert probe.DRIFT in {e["path"] for e in dist}, name


def test_runs_that_agree_settle_to_their_path():
    runs = [{"g.token.good": "issued"}] * 3
    assert probe.settle(runs)["g.token.good"]["path"] == "issued"


def test_runs_that_disagree_settle_to_drift():
    runs = [{"g.token.good": "issued"}, {"g.token.good": "invalid_grant"}]
    got = probe.settle(runs)["g.token.good"]
    assert got["path"] == probe.DRIFT
    assert got["runs"] == ["issued", "invalid_grant"]


def test_an_undeclared_answer_is_drift_not_a_new_path():
    """An outcome must name a path the wager declared (`seal_outcome` refuses
    any other), so an answer nobody forecast settles to drift."""
    got = probe.settle([{"g.token.good": "http_500"}] * 2)["g.token.good"]
    assert got["path"] == probe.DRIFT and got["declared"] is False


def test_a_probe_that_never_ran_is_not_a_hit():
    got = probe.settle([{}])
    assert all(v["path"] == probe.DRIFT for v in got.values())
