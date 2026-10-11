"""willow_bot.venvs — which venvs are inside the bot (D2), and the plan in.

Read-only: inventory and plan only look; nothing here creates, moves or
removes a venv.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from willow_bot import paths, venvs


def _fake_venv(path: Path, version: str = "3.12.4", editable: Path | None = None) -> Path:
    path.mkdir(parents=True)
    (path / "pyvenv.cfg").write_text(f"home = /usr/bin\nversion_info = {version}\n")
    if editable is not None:
        dist = path / "lib" / "python3.12" / "site-packages" / "willow_bot-1.5.0.dist-info"
        dist.mkdir(parents=True)
        (dist / "direct_url.json").write_text(
            json.dumps({"url": editable.as_uri(), "dir_info": {"editable": True}})
        )
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    box = paths.willow_home()
    gh = tmp_path / "github"
    gh.mkdir()
    monkeypatch.setenv("GITHUB_ROOT", str(gh))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return box, gh


def test_venvs_dir_is_inside_the_bot():
    assert paths.venvs_dir() == paths.bot_dir() / "venvs"
    assert paths.legacy_venvs_dir() == paths.willow_home() / "venvs"


def test_inventory_splits_inside_and_outside(roots):
    box, gh = roots
    inside = _fake_venv(box / "willow-bot" / "venvs" / "willow-bot")
    old = _fake_venv(box / "venvs" / "willow-bot")
    elsewhere = _fake_venv(gh / "other-org" / "thing" / ".venv")

    inv = venvs.inventory()

    assert inv["status"] == "populated"
    by_path = {v["path"]: v for v in inv["venvs"]}
    assert by_path[str(inside.resolve())]["inside"] is True
    assert by_path[str(old.resolve())]["inside"] is False
    assert by_path[str(elsewhere.resolve())]["inside"] is False
    assert (inv["inside"], inv["outside"]) == (1, 2)
    assert by_path[str(old.resolve())]["python"] == "3.12.4"


def test_a_willow_bot_checkout_venv_counts_as_inside(roots):
    _, gh = roots
    venv = _fake_venv(gh / "willow-memory" / "willow-bot" / ".venv")
    inv = venvs.inventory()
    assert [v["inside"] for v in inv["venvs"] if v["path"] == str(venv.resolve())] == [True]


def test_empty_when_no_venvs(roots):
    inv = venvs.inventory()
    assert inv["status"] == "empty"
    assert inv["venvs"] == []


def test_plan_recreates_never_moves(roots, tmp_path):
    box, _ = roots
    checkout = tmp_path / "checkout" / "willow-bot"
    checkout.mkdir(parents=True)
    old = _fake_venv(box / "venvs" / "willow-bot", editable=checkout)

    steps = venvs.plan()

    assert len(steps) == 1
    step = steps[0]
    target = paths.venvs_dir() / "willow-bot"
    assert step["old"] == str(old.resolve())
    assert step["new"] == str(target)
    cmds = "\n".join(step["commands"])
    assert cmds.startswith(f"python3.12 -m venv {target}")
    assert "--exclude-editable" in cmds
    assert f"pip install -e '{checkout}'" in cmds
    assert "pip check" in cmds
    assert not any(c.startswith(("mv ", "rm ")) for c in step["commands"])
    assert "by hand" in step["then"]
    # Planning touched nothing.
    assert not target.exists()
    assert old.is_dir()


def test_plan_names_a_checkout_venv_for_its_checkout(roots):
    _, gh = roots
    _fake_venv(gh / "some-org" / "tool" / ".venv")
    [step] = venvs.plan()
    assert step["new"] == str(paths.venvs_dir() / "tool")


def test_plan_lists_units_that_name_the_old_path(roots, tmp_path):
    box, _ = roots
    old = _fake_venv(box / "venvs" / "willow-bot")
    units = tmp_path / "xdg" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "willow-bot.service").write_text(f"ExecStart={old.resolve()}/bin/willow-bot\n")
    (units / "other.service").write_text("ExecStart=/usr/bin/true\n")

    [step] = venvs.plan()

    assert len(step["repoint"]) == 1
    assert "willow-bot.service" in step["repoint"][0]
    assert "install-service.sh" in step["repoint"][0]


def test_no_box_is_said_not_guessed(monkeypatch, capsys):
    monkeypatch.delenv("WILLOW_HOME")
    monkeypatch.delenv("WILLOW_VAULT_BOX")
    assert venvs.inventory()["status"] == "no-box"
    assert venvs.plan() == []
    assert venvs.main([]) == 1
    assert "no box" in capsys.readouterr().out


def test_cli_text_and_json(roots, capsys):
    box, _ = roots
    _fake_venv(box / "venvs" / "willow-bot")

    assert venvs.main([]) == 0
    out = capsys.readouterr().out
    assert "0 inside the bot, 1 outside" in out
    assert "OUTSIDE" in out

    assert venvs.main(["--plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outside"] == 1 and len(data["plan"]) == 1

    assert venvs.main(["--bogus"]) == 2


def test_status_carries_the_counts(roots):
    from willow_bot import status

    box, _ = roots
    _fake_venv(box / "venvs" / "willow-bot")
    sec = status.report()["venvs"]
    assert (sec["inside"], sec["outside"]) == (0, 1)
    line = [ln for ln in status.brief().splitlines() if ln.strip().startswith("venvs")]
    assert line and "1 outside" in line[0] and "venvs --plan" in line[0]
