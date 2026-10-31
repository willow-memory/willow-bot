"""willow_bot.venvs — which venvs are inside the bot (D2), and the plan in.

Read-only: inventory and plan only look; nothing here creates, moves or
removes a venv.
"""
from __future__ import annotations

import json
import subprocess
import sys
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
    # No real systemctl: what runs is this machine's, never a test's.
    monkeypatch.setattr(venvs, "_main_pids", dict)
    monkeypatch.setattr(venvs, "_children", lambda pid: [])
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
    held = target / "constraints.from-old.txt"
    assert f"pip install -c {held} -e '{checkout}'" in cmds
    assert "pip install -r" not in cmds, "a move asks for what was asked for, never the whole freeze"
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


def _dist(venv: Path, name: str, version: str, *, requested: bool = False, meta: str = "",
          editable: Path | None = None) -> None:
    dist = venv / "lib" / "python3.12" / "site-packages" / f"{name}-{version}.dist-info"
    dist.mkdir(parents=True)
    if requested:
        (dist / "REQUESTED").write_text("")
    if meta:
        (dist / "METADATA").write_text(meta)
    if editable is not None:
        (dist / "direct_url.json").write_text(json.dumps({"url": editable.as_uri(), "dir_info": {"editable": True}}))


def test_a_venv_behind_a_symlink_is_found_once_under_its_real_path(roots, tmp_path):
    _, gh = roots
    real = _fake_venv(gh / "willow-memory" / ".willow" / "venvs" / "willow-mcp")  # deeper than MAX_DEPTH
    link = gh / "willow-memory" / "willow-mcp" / ".venv"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    inv = venvs.inventory()

    found = [v for v in inv["venvs"] if v["path"] == str(real.resolve())]
    assert len(found) == 1, "the real venv, counted once"
    assert found[0]["aliases"] == [str(link)]
    assert inv["outside"] == 1


def test_a_unit_running_through_a_link_or_a_launcher_is_named(roots, monkeypatch):
    box, gh = roots
    real = _fake_venv(gh / "org" / ".willow" / "venvs" / "svc")
    link = gh / "org" / "svc" / ".venv"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    other = _fake_venv(box / "venvs" / "willow-bot")
    pids = {"svc-serve.service": 11, "launcher.service": 12, "bot.service": 13, "plain.service": 14}
    lines = {
        11: [f"{link}/bin/python3", "-m", "svc"],  # through the symlink
        12: [f"{real}/bin/python3", "-m", "svc"],  # a launcher exec'd the real one
        13: [f"{other}/bin/python", f"{other}/bin/willow-bot"],
        14: ["/usr/bin/sleep", "60"],  # no venv
    }
    monkeypatch.setattr(venvs, "_main_pids", lambda: pids)
    monkeypatch.setattr(venvs, "_cmdline", lambda pid: lines[pid])

    by_path = {v["path"]: v for v in venvs.inventory()["venvs"]}

    assert by_path[str(real.resolve())]["runs"] == {
        "status": "populated", "units": ["launcher.service", "svc-serve.service"]}
    assert by_path[str(other.resolve())]["runs"]["units"] == ["bot.service"]


def test_a_unit_runs_the_venv_its_child_process_runs_from(roots, monkeypatch):
    """2026-10-31: the steward runs from the bot's venv and keeps a willow-mcp
    child from the box's; willow-mcp moved, and keep-current owed the steward
    no restart because only the main process was read."""
    box, _ = roots
    bot = _fake_venv(box / "willow-bot" / "venvs" / "willow-bot")
    mcp = _fake_venv(box / "venvs" / "willow-mcp")
    lines = {
        21: [f"{bot}/bin/python", f"{bot}/bin/willow-bot-steward", "loop"],
        22: ["/bin/sh", "-c", "x"],
        23: [f"{mcp}/bin/python", "-m", "willow_mcp"],
    }
    monkeypatch.setattr(venvs, "_main_pids", lambda: {"willow-bot-steward.service": 21})
    monkeypatch.setattr(venvs, "_children", lambda pid: [22, 23] if pid == 21 else [])
    monkeypatch.setattr(venvs, "_cmdline", lambda pid: lines[pid])

    by_path = {v["path"]: v for v in venvs.inventory()["venvs"]}

    assert by_path[str(bot.resolve())]["runs"]["units"] == ["willow-bot-steward.service"]
    assert by_path[str(mcp.resolve())]["runs"]["units"] == ["willow-bot-steward.service"]


def test_children_are_read_from_proc_all_the_way_down():
    import os

    me = os.getpid()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert child.pid in venvs._children(me)
    finally:
        child.kill()
        child.wait()


def test_who_runs_a_venv_is_unknown_never_nobody_when_units_cant_be_listed(roots, monkeypatch):
    box, _ = roots
    venv = _fake_venv(box / "venvs" / "willow-bot")

    def no_session():
        raise OSError("Failed to connect to bus")

    monkeypatch.setattr(venvs, "_main_pids", no_session)
    [v] = [v for v in venvs.inventory()["venvs"] if v["path"] == str(venv.resolve())]
    assert v["runs"]["status"] == "unreachable" and v["runs"]["units"] == []


def test_the_plan_asks_again_for_what_was_installed_by_name_and_its_extras(roots, tmp_path):
    box, _ = roots
    checkout = tmp_path / "checkout" / "tool"
    checkout.mkdir(parents=True)
    old = _fake_venv(box / "venvs" / "tool")
    meta = ("Name: tool\n"
            "Requires-Dist: pytest; extra == 'dev'\n"
            "Requires-Dist: mcp>=2; extra == 'mcp'\n"
            "Requires-Dist: textual; extra == 'tui'\n")  # fmt: skip
    _dist(old, "tool", "1.0", requested=True, meta=meta, editable=checkout)
    _dist(old, "pytest", "9.1.1")  # dev's, pulled in
    _dist(old, "mcp", "2.2.0", requested=True)  # mcp's, and asked for by name too
    _dist(old, "requests", "2.32.3", requested=True)
    _dist(old, "urllib3", "2.7.0")  # a dependency: never asked for
    _dist(old, "pip", "26.2.1", requested=True)  # the venv's own tooling

    [step] = venvs.plan()

    held = paths.venvs_dir() / "tool" / "constraints.from-old.txt"
    cmds = step["commands"]
    assert f"{paths.venvs_dir() / 'tool'}/bin/pip install -c {held} mcp requests" in cmds
    assert f"{paths.venvs_dir() / 'tool'}/bin/pip install -c {held} -e '{checkout}[dev,mcp]'" in cmds
    joined = "\n".join(cmds)
    assert "urllib3" not in joined and " pip " not in joined.replace("bin/pip ", "")
    assert "tui" not in joined, "textual isn't installed, so [tui] wasn't asked for"


def test_two_venvs_that_would_land_on_one_name_are_said_before_either_moves(roots, tmp_path):
    box, gh = roots
    _fake_venv(box / "venvs" / "willow-mcp")
    _fake_venv(gh / "willow-memory" / "willow-mcp" / ".venv")

    steps = venvs.plan()

    assert len(steps) == 2
    for step in steps:
        assert any("would land at" in n and "become one" in n for n in step["notes"]), step["notes"]


def test_a_unit_naming_a_symlink_to_the_venv_is_listed_to_repoint(roots, tmp_path):
    _, gh = roots
    real = _fake_venv(gh / "org" / ".willow" / "venvs" / "svc")
    link = gh / "org" / "svc" / ".venv"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    units = tmp_path / "xdg" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "svc.service").write_text(f"ExecStart={link}/bin/python3 -m svc\n")

    [step] = venvs.plan()

    assert len(step["repoint"]) == 1 and "svc.service" in step["repoint"][0]
