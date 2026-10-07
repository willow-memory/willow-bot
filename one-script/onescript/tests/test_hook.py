"""The hook: Read only the served file, Write asks, everything else denied,
never fails open."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "hook.py"


def run(stdin: str, served: str | None = None) -> tuple[int, str | None]:
    env = {k: v for k, v in os.environ.items() if k != "ONESCRIPT_SERVED"}
    if served is not None:
        env["ONESCRIPT_SERVED"] = served
    out = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    if out.returncode != 0:
        return out.returncode, None
    return 0, json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


def tool(name, **args) -> str:
    return json.dumps({"tool_name": name, "tool_input": args})


def test_the_served_file_can_be_read(tmp_path):
    served = tmp_path / "served.json"
    served.write_text("{}")
    assert run(tool("Read", file_path=str(served)), str(served)) == (0, "allow")


def test_the_same_file_by_another_spelling_is_the_same_file(tmp_path):
    served = tmp_path / "served.json"
    served.write_text("{}")
    other = str(tmp_path / "." / "served.json")
    assert run(tool("Read", file_path=other), str(served)) == (0, "allow")


def test_any_other_file_is_denied(tmp_path):
    served = tmp_path / "served.json"
    for path in [
        tmp_path / "record.jsonl",
        tmp_path / "pile.json",
        Path("/etc/passwd"),
    ]:
        assert run(tool("Read", file_path=str(path)), str(served)) == (0, "deny")


def test_a_link_to_another_file_is_denied(tmp_path):
    served, record = tmp_path / "served.json", tmp_path / "record.jsonl"
    record.write_text("{}")
    link = tmp_path / "looks-served.json"
    link.symlink_to(record)
    assert run(tool("Read", file_path=str(link)), str(served)) == (0, "deny")


def test_nothing_served_means_nothing_read(tmp_path):
    path = str(tmp_path / "served.json")
    assert run(tool("Read", file_path=path)) == (0, "deny")
    assert run(tool("Read", file_path=path), "") == (0, "deny")


def test_a_read_without_a_path_is_denied(tmp_path):
    served = str(tmp_path / "served.json")
    assert run(tool("Read"), served) == (0, "deny")
    assert run(json.dumps({"tool_name": "Read", "tool_input": "x"}), served) == (
        0,
        "deny",
    )


def test_write_asks_the_user():
    assert run(tool("Write")) == (0, "ask")


def test_everything_else_is_denied():
    for name in ["Bash", "Edit", "Glob", "WebFetch", "Agent", "mcp__x__y"]:
        assert run(tool(name)) == (0, "deny"), name


def test_a_tool_that_does_not_exist_yet_is_denied():
    assert run(tool("SomethingNew")) == (0, "deny")


def test_no_tool_name_is_denied():
    assert run("{}") == (0, "deny")
    assert run("[]") == (0, "deny")
    assert run(tool(None)) == (0, "deny")


def test_unreadable_input_blocks():
    assert run("not json") == (2, None)
    assert run("") == (2, None)
