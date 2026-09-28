"""No test may write into the live steward home.

Gap 9 (willow-bot/tests-write-into-the-live-steward-journal), 2026-09-21:
the full suite run from Kart with the operator's ``WILLOW_HOME`` inherited
wrote five fixture ``steward_sweep`` receipts and a fake red for
forge-play/Forge#4 (https://example.invalid/runs/11) into the LIVE
``steward_ticks.jsonl`` and CI deposit stream. Per-module ``home`` fixtures
covered the tests that asked for them; the ones that did not resolved
``willow_bot.paths.willow_home()`` to the box.

This autouse fixture is the floor under every test in the package:

* ``WILLOW_HOME`` and ``WILLOW_VAULT_BOX`` point at ``tmp_path``; the state
  and delivery overrides that could point elsewhere are cleared.
* ``willow_bot.paths.willow_home`` (and the name ``steward.config`` bound at
  import) is wrapped: a resolution outside ``tmp_path`` fails the test,
  naming the path — so a writer that reads its own env cannot reach the
  box even if a later fixture re-points the env.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from willow_bot import paths as _paths
from willow_bot.steward import config as _config


@pytest.fixture(autouse=True)
def _sandboxed_willow_home(tmp_path, monkeypatch):
    root = Path(tmp_path).resolve()
    monkeypatch.setenv("WILLOW_HOME", str(root))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(root))
    for name in ("WILLOW_BOT_STEWARD_STATE", "LOKI_PR_WATCH_STATE", "WILLOW_BOT_DELIVERY_STATE"):
        monkeypatch.delenv(name, raising=False)

    real_home = _paths.willow_home

    def _guarded_home() -> Path:
        resolved = real_home()
        try:
            Path(resolved).resolve().relative_to(root)
        except ValueError:
            pytest.fail(
                f"willow_home() resolved to {resolved}, outside the test's tmp_path {root} — "
                "a test reached the live steward home (gap 9); set WILLOW_HOME under tmp_path"
            )
        return resolved

    monkeypatch.setattr(_paths, "willow_home", _guarded_home)
    monkeypatch.setattr(_config, "_paths_willow_home", _guarded_home)

    real_state = _config.state_path

    def _guarded_state() -> Path:
        p = real_state()
        try:
            Path(p).resolve().relative_to(root)
        except ValueError:
            pytest.fail(f"state_path() resolved to {p}, outside tmp_path {root} (gap 9)")
        return p

    monkeypatch.setattr(_config, "state_path", _guarded_state)

    # The steward re-reads a PR live before any CI red goes out. No test
    # reaches GitHub for that: unreadable is "unknown", which alerts as
    # before. A test of the gate itself patches `_ci_fetch_pull` back in.
    from willow_bot.steward import tick as _tick

    def _no_network_pull(repo, pr):
        raise RuntimeError("no GitHub in tests")

    monkeypatch.setattr(_tick, "_ci_fetch_pull", _no_network_pull)

    # Same floor for the ingress read (GitHub's webhook delivery record).
    from willow_bot.steward import ingress as _ingress

    def _no_network_ingress(*a, **kw):
        raise RuntimeError("no GitHub in tests")

    monkeypatch.setattr(_ingress, "_fetch_deliveries", _no_network_ingress)
    monkeypatch.setattr(_ingress, "_fetch_hook_url", _no_network_ingress)
    # Modules that bound the name at import (`from ...config import state_path`).
    for modname in ("willow_bot.steward.tick", "willow_bot.steward.voice", "willow_bot.steward.inbox",
                    "willow_bot.steward.merge", "willow_bot.steward.heartbeat"):
        try:
            mod = __import__(modname, fromlist=["state_path"])
        except ImportError:
            continue
        if getattr(mod, "state_path", None) is real_state:
            monkeypatch.setattr(mod, "state_path", _guarded_state)
    yield root
