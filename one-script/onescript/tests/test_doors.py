"""doors: the seat doesn't start, and check-in doesn't open, without its doors."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import boot, doors  # noqa: E402

ONE = Path(__file__).resolve().parents[2]  # one-script/
PINS = json.loads((ONE / "seat" / "doors.json").read_text())
SETTINGS = json.loads((ONE / "seat" / "managed-settings.json").read_text())


def bad(rows):
    return [(r["where"], r["why"]) for r in doors.failing(rows)]


def test_the_committed_doors_match_their_pins():
    """Change a hook or the settings without re-pinning and this goes red."""
    assert bad(doors.check_repo(ONE, PINS)) == []


def copy_repo(tmp_path) -> Path:
    for src in doors.DOORS:
        (tmp_path / src).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ONE / src, tmp_path / src)
    return tmp_path


def test_a_hook_changed_without_a_new_pin_fails(tmp_path):
    repo = copy_repo(tmp_path)
    with (repo / "hook.py").open("a") as f:
        f.write('POLICY = {"Bash": "allow"}\n')
    assert bad(doors.check_repo(repo, PINS)) == [
        ("hook.py", "changed since it was pinned; re-pin")
    ]


def test_a_missing_pin_or_door_fails(tmp_path):
    repo = copy_repo(tmp_path)
    (repo / "prompt.py").unlink()
    rows = bad(doors.check_repo(repo, {"doors": {}}))
    assert ("prompt.py", "no pin") in rows and len(rows) == 3


def test_the_settings_say_what_the_seat_needs():
    assert bad(doors.settings_rows(SETTINGS, "s")) == []
    for change, why in [
        ({"allowManagedHooksOnly": False}, "other hooks could run"),
        (
            {"allowManagedPermissionRulesOnly": None},
            "other permission rules could apply",
        ),
        ({"env": {}}, "prompt.py isn't told where serve writes"),
        ({"hooks": {}}, "no PreToolUse hook on every tool"),
    ]:
        assert ("s", why) in bad(doors.settings_rows({**SETTINGS, **change}, "s"))


def test_denying_write_would_mean_the_human_is_never_asked():
    perms = {
        **SETTINGS["permissions"],
        "deny": [*SETTINGS["permissions"]["deny"], "Write"],
    }
    rows = bad(doors.settings_rows({**SETTINGS, "permissions": perms}, "s"))
    assert rows == [("s", "Write is denied, so the human is never asked")]


def test_a_short_deny_list_fails():
    perms = {**SETTINGS["permissions"], "deny": ["Bash"]}
    assert ("s", "the deny list is short") in bad(
        doors.settings_rows({**SETTINGS, "permissions": perms}, "s")
    )


def seat_root(tmp_path) -> Path:
    root = tmp_path / "root"
    for src, installed in doors.DOORS.items():
        dest = root / installed.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ONE / src, dest)
        dest.chmod(0o644)
    return root


def test_the_seat_check_reads_the_installed_doors(tmp_path):
    root = seat_root(tmp_path)
    rows = bad(doors.check_seat(PINS, root))
    if os.geteuid() == 0:  # the files are root's, as in the image
        assert rows == []
    else:  # on CI the copies are the runner's, and that alone fails
        assert {why for _, why in rows} == {"not root's"}


def test_a_swapped_door_in_the_seat_fails(tmp_path):
    root = seat_root(tmp_path)
    (root / "opt/onescript/prompt.py").write_text("print('hello')\n")
    assert ("/opt/onescript/prompt.py", "doesn't match its pin") in bad(
        doors.check_seat(PINS, root)
    )


def test_exec_refuses_when_a_door_is_open(tmp_path):
    root = seat_root(tmp_path)
    (root / "opt/onescript/hook.py").write_text("")
    pins = tmp_path / "doors.json"
    pins.write_text(json.dumps(PINS))
    out = subprocess.run(
        [
            sys.executable,
            str(ONE / "onescript" / "doors.py"),
            "exec",
            "--pins",
            str(pins),
            "--root",
            str(root),
            "--",
            "echo",
            "STARTED",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2
    assert "the seat does not start" in out.stdout and "STARTED" not in out.stdout


def test_check_in_runs_the_doors_gate():
    assert cli._gate_cfg(True, "", Path("/v"))["doors"] == str(ONE)
    assert bad(boot.doors_gate(str(ONE))) == []
    assert boot.doors_gate(None) == []


def test_no_pins_at_check_in_is_a_failing_door(tmp_path):
    rows = boot.doors_gate(str(tmp_path))
    assert [(r["verdict"], r["where"]) for r in rows] == [
        ("failing", "seat/doors.json")
    ]
