"""md_split: harness, human and model stay separate classes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "scan"))

import md_split  # noqa: E402


def user(content, **extra) -> dict:
    return {"type": "user", "message": {"content": content}, **extra}


ROWS = [
    user("go ahead"),
    user([{"type": "text", "text": "boot in"}]),
    user("<task-notification>\n<task-id>a1</task-id>\n</task-notification>"),
    user("Tool loaded."),
    user("Stop hook feedback: the word was banned"),
    user("[Request interrupted by user]"),
    user("<system-reminder>only a reminder</system-reminder>"),
    user("a meta row", isMeta=True),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    {
        "type": "attachment",
        "attachment": {"type": "queued_command", "humanTurn": True, "prompt": "queued"},
    },
    {
        "type": "attachment",
        "attachment": {"type": "file", "content": {"file": {"content": "# a file"}}},
    },
]


def classes(tmp_path: Path) -> list[tuple[str, str, str]]:
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
    return list(md_split.texts(str(path)))


def test_task_notification_is_harness_not_typed(tmp_path):
    rows = classes(tmp_path)
    notes = [s for _, s, t in rows if t.startswith("<task-notification>")]
    assert notes == ["harness"]


def test_only_the_operators_prompts_are_typed(tmp_path):
    typed = [t for r, s, t in classes(tmp_path) if (r, s) == ("user", "typed")]
    assert typed == ["go ahead", "boot in", "queued"]


def test_every_harness_opening_is_classed(tmp_path):
    harness = [t.split("\n")[0][:20] for _, s, t in classes(tmp_path) if s == "harness"]
    assert harness == [
        "<task-notification>",
        "Tool loaded.",
        "Stop hook feedback: ",
        "[Request interrupted",
    ]


def test_reminders_and_meta_rows_are_dropped(tmp_path):
    texts = [t for _, _, t in classes(tmp_path)]
    assert "a meta row" not in texts
    assert not any("only a reminder" in t for t in texts)


def test_model_and_attached_keep_their_class(tmp_path):
    rows = classes(tmp_path)
    assert ("assistant", "model", "ok") in rows
    assert ("user", "attached", "# a file") in rows
