"""The tick's act half is wired (2026-09-14): after the heartbeat, the loop
asks willow-mcp's ``gitsync_sweep`` to bring merges home; merge.py's host
``gh``/``pip -e`` sync steps aside when the broker path is on; and the bridge
finds org-layout clones so the flag the sweep consumes is written at all.

Nothing here spawns an MCP server or touches git: the client is a fake, the
clones are empty ``.git`` dirs with a fake ``git remote get-url``.
"""
from __future__ import annotations

import json
import subprocess

from integrations import fleet_bridge
from willow_bot.steward import config, tick


# ── run_sweep ────────────────────────────────────────────────────────────────

def _calls(monkeypatch, result):
    seen = []

    def call(name, inputs):
        seen.append((name, inputs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("willow_bot.steward.mcp_client.call", call)
    return seen


def test_sweep_is_absent_not_silent_when_mcp_is_off(monkeypatch, capsys):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    seen = _calls(monkeypatch, {"ok": True})
    r = tick.run_sweep()
    assert r["status"] == "absent" and "WILLOW_BOT_MCP" in r["detail"]
    assert seen == []
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "steward_sweep" and line["status"] == "absent"


def test_sweep_calls_gitsync_sweep_and_summarizes(monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "willow")
    seen = _calls(monkeypatch, {
        "ok": True, "present": True, "swept": [
            {"ok": True, "pulled": True, "repo": "willow-memory/willow-mcp", "flag": "a"},
            {"ok": True, "pulled": False, "repo": "forge-play/Forge", "flag": "b"},
            {"ok": False, "error": "EBUSY", "flag": "trigger-x-y.flag"},
        ],
    })
    r = tick.run_sweep()
    assert seen == [("gitsync_sweep", {"app_id": "willow", "project": "fleet"})]
    assert r["status"] == "ok" and r["present"] is True
    assert r["pulled"] == ["willow-memory/willow-mcp"]
    assert r["refused"] == [{"flag": "trigger-x-y.flag", "error": "EBUSY"}]


def test_sweep_reports_an_absent_trigger_dir_as_not_present(monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _calls(monkeypatch, {"ok": True, "present": False, "swept": []})
    r = tick.run_sweep()
    assert r["status"] == "ok" and r["present"] is False and r["pulled"] == []


def test_a_failed_sweep_is_a_line_not_a_dead_loop(monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    _calls(monkeypatch, RuntimeError("MCP server did not initialize within 90s"))
    r = tick.run_sweep()
    assert r["status"] == "could-not-run" and "90s" in r["detail"]


def test_the_cli_knows_sweep(monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    assert tick.main(["sweep"]) == 0


# ── host sync steps aside for the broker ─────────────────────────────────────

def test_host_sync_is_on_without_mcp_and_off_with_it(monkeypatch):
    monkeypatch.delenv("WILLOW_BOT_STEWARD_HOST_SYNC", raising=False)
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    assert config.host_sync_enabled() is True
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    assert config.host_sync_enabled() is False


def test_an_explicit_host_sync_setting_always_wins(monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_STEWARD_HOST_SYNC", "1")
    assert config.host_sync_enabled() is True
    monkeypatch.delenv("WILLOW_BOT_MCP", raising=False)
    monkeypatch.setenv("WILLOW_BOT_STEWARD_HOST_SYNC", "0")
    assert config.host_sync_enabled() is False


# ── the bridge finds org-layout clones ───────────────────────────────────────

def _fake_git(urls: dict[str, str]):
    def run(argv, **kw):
        path = argv[2]
        url = urls.get(path)
        if url is None:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")
        return subprocess.CompletedProcess(argv, 0, url + "\n", "")
    return run


def test_org_layout_clone_is_found_and_verified_by_origin(tmp_path, monkeypatch):
    root = tmp_path / "github"
    org = root / "forge-play" / "Forge"
    (org / ".git").mkdir(parents=True)
    monkeypatch.setattr(fleet_bridge, "_GITHUB_ROOT", root)
    monkeypatch.setattr(fleet_bridge.subprocess, "run",
                        _fake_git({str(org): "https://github.com/forge-play/Forge.git"}))
    assert fleet_bridge._local_clone_path("forge-play/Forge") == org


def test_org_layout_wins_over_a_flat_folder_with_the_wrong_origin(tmp_path, monkeypatch):
    root = tmp_path / "github"
    org = root / "willow-memory" / "willow-mcp"
    flat = root / "willow-mcp"
    (org / ".git").mkdir(parents=True)
    (flat / ".git").mkdir(parents=True)
    monkeypatch.setattr(fleet_bridge, "_GITHUB_ROOT", root)
    monkeypatch.setattr(fleet_bridge.subprocess, "run", _fake_git({
        str(org): "git@github.com:willow-memory/willow-mcp.git",
        str(flat): "https://github.com/rudi193-cmd/willow-mcp.git",
    }))
    assert fleet_bridge._local_clone_path("willow-memory/willow-mcp") == org


def test_push_to_master_on_an_org_layout_repo_writes_the_trigger(tmp_path, monkeypatch):
    """The whole reason: before this, forge-play/Forge merges wrote no flag,
    so nothing could sweep them home."""
    root = tmp_path / "github"
    org = root / "forge-play" / "Forge"
    (org / ".git").mkdir(parents=True)
    monkeypatch.setattr(fleet_bridge, "_GITHUB_ROOT", root)
    monkeypatch.setattr(fleet_bridge, "_GITSYNC_TRIGGERS", tmp_path / "gitsync")
    monkeypatch.setattr(fleet_bridge.subprocess, "run",
                        _fake_git({str(org): "https://github.com/forge-play/Forge.git"}))
    fleet_bridge._request_gitsync("forge-play/Forge")
    flag = tmp_path / "gitsync" / "trigger-forge-play-Forge.flag"
    assert flag.is_file()
    payload = json.loads(flag.read_text(encoding="utf-8"))
    assert payload["layout"] == "org"
    assert payload["repo"] == "forge-play/Forge"
    assert payload["clone"] == str(org)


def test_push_to_master_on_a_flat_layout_repo_reports_flat_in_the_trigger(tmp_path, monkeypatch):
    root = tmp_path / "github"
    flat = root / "willow-bot"
    (flat / ".git").mkdir(parents=True)
    monkeypatch.setattr(fleet_bridge, "_GITHUB_ROOT", root)
    monkeypatch.setattr(fleet_bridge, "_GITSYNC_TRIGGERS", tmp_path / "gitsync")
    monkeypatch.setattr(fleet_bridge.subprocess, "run",
                        _fake_git({str(flat): "https://github.com/rudi193-cmd/willow-bot.git"}))
    fleet_bridge._request_gitsync("rudi193-cmd/willow-bot")
    flag = tmp_path / "gitsync" / "trigger-rudi193-cmd-willow-bot.flag"
    payload = json.loads(flag.read_text(encoding="utf-8"))
    assert payload["layout"] == "flat"
    assert payload["clone"] == str(flat)


def test_local_clone_path_with_layout_reports_org_scan_or_none(tmp_path, monkeypatch):
    root = tmp_path / "github"
    monkeypatch.setattr(fleet_bridge, "_GITHUB_ROOT", root)
    monkeypatch.setattr(fleet_bridge.subprocess, "run", _fake_git({}))
    assert fleet_bridge._local_clone_path_with_layout("o/nope") == (None, None)

    org = root / "forge-play" / "Forge"
    (org / ".git").mkdir(parents=True)
    monkeypatch.setattr(fleet_bridge.subprocess, "run",
                        _fake_git({str(org): "https://github.com/forge-play/Forge.git"}))
    assert fleet_bridge._local_clone_path_with_layout("forge-play/Forge") == (org, "org")


# ── the unit template renders and says what the loop needs ──────────────────

def test_the_steward_unit_template_carries_the_wiring():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    text = (repo / "systemd" / "willow-bot-steward.service.template").read_text(encoding="utf-8")
    assert "ExecStart=@VENV_BIN@/willow-bot-steward loop" in text
    assert "Environment=WILLOW_BOT_MCP=1" in text
    # Sealed 163b9a70: the steward is its own principal (app_id=willow-bot),
    # never the human orchestrator seat — this template no longer sets
    # WILLOW_HUMAN_ORCHESTRATOR; see the header comment above this line in
    # the template itself for the willow-mcp-side dependency (packet
    # 4326FDFE) that must land before an installed unit can actually use
    # this identity.
    assert "Environment=WILLOW_BOT_MCP_APP_ID=willow-bot" in text
    assert "Environment=WILLOW_HUMAN_ORCHESTRATOR=1" not in text
    assert "@VAULT_BOX@/secrets/willow-bot.env" in text
    assert "WILLOW_BOT_MCP_COMMAND=" in text
    import re

    placeholders = set(re.findall(r"@([A-Z_]+)@", text))
    assert placeholders == {"VENV_BIN", "VAULT_BOX", "WORKDIR"}, (
        f"only the three install-time placeholders may appear; got {placeholders}"
    )
    # systemd splits an unquoted Environment= value on whitespace: the first
    # install kept only the python path of WILLOW_BOT_MCP_COMMAND and logged
    # "ignoring: -m" / "ignoring: willow_mcp", and the steward spawned a bare
    # REPL as its server. Any value with a space must be quoted whole.
    for line in text.splitlines():
        if not line.startswith("Environment="):
            continue
        value = line[len("Environment="):]
        if " " in value.strip():
            assert value.startswith('"') and value.rstrip().endswith('"'), (
                f"unquoted Environment= value with a space: {line!r}"
            )


def test_a_failed_start_tears_the_lifecycle_down(monkeypatch):
    """Planted: a server that never initializes. start() must raise AND leave
    no thread, loop or task behind — otherwise the hung child outlives the
    failure and the next call spawns another (five orphaned servers per tick
    on 2026-09-14, one per curated tool)."""
    import asyncio
    import threading

    from willow_bot.steward import mcp_client as mc

    async def hang(argv, ready):
        await asyncio.Event().wait()  # initialize() that never returns

    monkeypatch.setattr(mc, "_lifecycle", hang)
    try:
        mc.start(["/nonexistent/python"], timeout_s=0.2)
        raise AssertionError("start() must raise on a server that never initializes")
    except RuntimeError as exc:
        assert "did not initialize" in str(exc)
    assert mc._mcp_thread is None and mc._mcp_loop is None and mc._mcp_task is None
    assert not any(t.name == "willow-bot-mcp" and t.is_alive() for t in threading.enumerate())
