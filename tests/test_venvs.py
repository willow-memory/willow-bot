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


# ── the installed set, and drift from the human's lock (IDEAS 54, 57) ─────


def _install(venv: Path, name: str, version: str, editable: bool = False) -> None:
    site = venv / "lib" / "python3.12" / "site-packages"
    dist = site / f"{name}-{version}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    if editable:
        (dist / "direct_url.json").write_text(json.dumps({"url": "file:///x", "dir_info": {"editable": True}}))


def _lock(venv: Path, text: str) -> Path:
    path = venvs.lock_path(venv)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _bot_venv(name: str = "willow-bot") -> Path:
    return _fake_venv(paths.venvs_dir() / name)


def test_installed_set_is_hashed_and_dated(roots):
    v = _bot_venv()
    _install(v, "Requests", "2.32.3")
    _install(v, "typing_extensions", "4.12.2")
    have = venvs.installed(v)
    assert have["status"] == "populated" and have["count"] == 2
    assert have["packages"] == {"requests": "2.32.3", "typing-extensions": "4.12.2"}
    assert have["modified"].endswith("Z")
    first = have["hash"]
    _install(v, "idna", "3.7")
    assert venvs.installed(v)["hash"] != first  # the set changed, the hash moved


def test_installed_empty_is_not_unreachable(roots):
    v = _bot_venv()
    (v / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    assert venvs.installed(v)["status"] == "empty"


def test_a_venv_with_no_site_packages_is_unknown_not_empty(roots):
    have = venvs.installed(_bot_venv())  # pyvenv.cfg only
    assert have["status"] == "unreachable" and "hash" not in have


def test_unlistable_site_packages_is_unreachable(roots, monkeypatch):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")

    def boom(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "iterdir", boom)
    have = venvs.installed(v)
    assert have["status"] == "unreachable" and "Permission denied" in have["detail"]


def test_no_lock_is_empty_never_no_drift(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    [row] = venvs.inventory()["venvs"]
    assert row["lock"]["status"] == "empty"
    assert row["lock"]["drift"] is None
    assert "no lock" in row["lock"]["detail"]


def test_lock_that_matches_is_populated_without_drift(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _install(v, "pip", "24.0")  # tooling: not drift when unpinned
    _install(v, "willow_bot", "1.7.0", editable=True)  # editable: cannot be pinned
    _lock(v, "requests==2.32.3 \\\n    --hash=sha256:abc\n# comment\n")
    lock = venvs.drift(v, venvs.installed(v))
    assert lock["status"] == "populated" and lock["drift"] is False


def test_drift_names_missing_changed_and_extra(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _install(v, "urllib3", "2.2.1")
    _install(v, "rich", "13.0")
    _lock(v, "requests==2.31.0 \\\n    --hash=sha256:abc\nurllib3==2.2.1\nidna==3.7\n")
    lock = venvs.drift(v, venvs.installed(v))
    assert lock["status"] == "populated" and lock["drift"] is True
    assert lock["missing"] == ["idna"]
    assert lock["extra"] == ["rich"]
    assert lock["changed"] == [{"name": "requests", "installed": "2.32.3", "locked": "2.31.0"}]


def test_exact_match_has_no_drift(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _lock(v, "requests==2.32.3 \\\n    --hash=sha256:abc\n")
    lock = venvs.drift(v, venvs.installed(v))
    assert lock["drift"] is False and not (lock["missing"] or lock["changed"] or lock["extra"])


def test_unreadable_lock_is_unreachable_not_empty(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _lock(v, "")
    venvs.lock_path(v).write_bytes(b"\xff\xfe\x00 not utf8")
    lock = venvs.drift(v, venvs.installed(v))
    assert lock["status"] == "unreachable" and lock["drift"] is None


def test_lock_that_pins_nothing_is_empty(roots):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _lock(v, "# nothing here\n")
    assert venvs.drift(v, venvs.installed(v))["status"] == "empty"


def test_lock_with_unknown_install_is_unreachable_not_drifted(roots):
    v = _bot_venv()  # no site-packages
    _lock(v, "requests==2.32.3\n")
    lock = venvs.drift(v, venvs.installed(v))
    assert lock["status"] == "unreachable" and lock["drift"] is None


def test_outside_venv_is_not_checked_against_a_lock(roots):
    box, _ = roots
    old = _fake_venv(box / "venvs" / "willow-mcp")
    _install(old, "requests", "2.32.3")
    [row] = venvs.inventory()["venvs"]
    assert row["inside"] is False and row["lock"]["status"] == "unchecked"
    assert row["lock"]["drift"] is None


def test_status_carries_per_venv_detail_and_drift(roots):
    from willow_bot import status

    v = _bot_venv("willow-bot")
    _install(v, "requests", "2.32.3")
    _lock(v, "requests==2.31.0\n")
    sec = status.report()["venvs"]
    [row] = sec["venvs"]
    assert row["python"] == "3.12.4" and row["installed"]["count"] == 1
    assert row["installed"]["hash"] and "packages" not in row["installed"]
    assert row["lock"]["drift"] is True
    assert sec["drifted"] == [row["path"]] and sec["unlocked"] == []
    [line] = [ln for ln in status.brief().splitlines() if ln.strip().startswith("venvs")]
    assert "1 drifted from the lock" in line


def test_status_names_a_venv_without_a_lock(roots):
    from willow_bot import status

    v = _bot_venv("willow-bot")
    _install(v, "requests", "2.32.3")
    sec = status.report()["venvs"]
    assert sec["drifted"] == [] and sec["unlocked"] == [sec["venvs"][0]["path"]]
    [line] = [ln for ln in status.brief().splitlines() if ln.strip().startswith("venvs")]
    assert "1 without a lock" in line


def test_cli_text_shows_hash_and_lock_word(roots, capsys):
    v = _bot_venv()
    _install(v, "requests", "2.32.3")
    _lock(v, "requests==2.32.3\n")
    assert venvs.main([]) == 0
    out = capsys.readouterr().out
    assert "1 pkgs #" in out and "lock: matches" in out
