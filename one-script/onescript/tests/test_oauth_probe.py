"""The OAuth probe keeps its forecasts honest and never hides drift.

`scripts/oauth/oauth_probe.py` has two kinds of probe. A wager is blind and
goes into the ledger before it runs, so each distribution must pass
`reverse.check_distribution` and declare `drift`. A pin is an answer already
seen, so it is never a wager; a pin that moves is `changed`. Live servers
aren't needed here: `settle` and the tables are pure.
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


def _one(runs, name):
    return next(s for s in probe.settle(runs, {name}) if s.probe == name)


# ── the tables ───────────────────────────────────────────────────────────────
def test_every_wager_stands_and_declares_drift():
    for name, dist in probe.WAGERS.items():
        assert reverse.check_distribution(dist) is None, name
        assert probe.DRIFT in {e["path"] for e in dist}, name


def test_a_probe_is_a_wager_or_a_pin_never_both():
    """A pin's answer was seen; forecasting it after would not be a forecast."""
    assert not set(probe.WAGERS) & set(probe.PINNED)


def test_no_google_drops_every_google_probe_and_nothing_else():
    local = probe._probes(google=False)
    assert local and not any(p.startswith("google.") for p in local)
    every = probe._probes(google=True)
    assert every - local == {p for p in every if p.startswith("google.")}


# ── wagers ───────────────────────────────────────────────────────────────────
def test_a_wager_whose_runs_agree_settles_to_their_path():
    got = _one([{"g.pkce.plain": "refused"}] * 3, "g.pkce.plain")
    assert got.kind == "wager" and got.path == "refused" and got.hit


def test_a_wager_whose_runs_disagree_is_drift():
    runs = [{"g.pkce.plain": "refused"}, {"g.pkce.plain": "approval_page"}]
    got = _one(runs, "g.pkce.plain")
    assert got.path == probe.DRIFT and got.runs == ["refused", "approval_page"]


def test_an_undeclared_answer_is_drift_not_a_new_path():
    """`seal_outcome` takes only a declared path, so an answer nobody
    forecast settles to drift."""
    got = _one([{"g.pkce.plain": "http_500"}] * 2, "g.pkce.plain")
    assert got.path == probe.DRIFT and not got.hit


# ── pins ─────────────────────────────────────────────────────────────────────
def test_a_pin_that_holds_is_a_hit():
    got = _one([{"g.token.good": "issued"}] * 2, "g.token.good")
    assert got.kind == "pin" and got.path == "issued" and got.hit


def test_a_pin_that_moves_together_is_changed():
    got = _one([{"g.token.good": "invalid_grant"}] * 2, "g.token.good")
    assert got.path == probe.CHANGED and not got.hit


def test_a_pin_that_wobbles_is_drift_not_changed():
    runs = [{"g.token.good": "issued"}, {"g.token.good": "invalid_grant"}]
    assert _one(runs, "g.token.good").path == probe.DRIFT


def test_a_probe_that_never_ran_is_never_a_hit():
    assert not any(s.hit for s in probe.settle([{}]))


# ── the wire ─────────────────────────────────────────────────────────────────
def test_an_sse_body_reads_as_its_last_data_line():
    body = 'event: message\ndata: {"a": 1}\n\ndata: {"b": 2}\n'
    assert probe.Reply(200, {}, body).json() == {"b": 2}


def test_a_body_that_is_not_an_object_reads_as_empty():
    assert probe.Reply(200, {}, "[1, 2]").json() == {}
    assert probe.Reply(200, {}, "<html>").json() == {}
