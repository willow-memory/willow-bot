"""xref: two hashes on arrival, every reference checked, same bytes every run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import xref  # noqa: E402

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "2026-10-06T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-06T00:00:00Z",
}


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make(tmp: Path, name: str, files: dict) -> Path:
    repo = tmp / name
    repo.mkdir()
    sh(repo, "init", "-q")
    sh(repo, "config", "core.autocrlf", "false")
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "one")
    return repo


def refs(idx, **match):
    return [r for r in idx["refs"] if all(r.get(k) == v for k, v in match.items())]


@pytest.fixture
def two(tmp_path):
    bot = make(tmp_path, "bot", {"lib.py": "a\nb\nc\n"})
    bot_sha = sh(bot, "rev-parse", "HEAD")
    grove = make(
        tmp_path,
        "grove",
        {
            "INVARIANTS.md": "# I\n\n## §1 — three states\n",
            "law.md": "## Article VI *(CONST-VI)*\n",
            "plan.md": (
                "| Id | What |\n|---|---|\n| **Q19** | serve |\n| Q19 | again |\n\n"
                "See Q19, Q99, CONST-VI, INVARIANTS §1 and PR #7.\n"
                "[ok](law.md) [gone](nope.md) [web](https://example.com)\n"
                "[cross](https://github.com/acme/bot/blob/main/lib.py#L2)\n"
                "`lib.py:2` `lib.py:9` `law.md:1@0000000`\n"
                f"bot commit {bot_sha[:12]} and stranger 1234abcd\n"
                "```\nQ77 inside a fence is not a citation\n```\n"
            ),
            "code.py": "# F401 is a lint code, not an id. Cites: CONST-VI\n",
        },
    )
    return {"grove": grove, "bot": bot, "bot_sha": bot_sha}


def test_each_file_arrives_with_both_hashes(two):
    a = xref.arrive(two["grove"], "grove")
    assert a["state"] == "populated" and len(a["commit"]) == 40
    f = next(f for f in a["files"] if f["path"] == "law.md")
    data = (two["grove"] / "law.md").read_bytes()
    assert f["git_blob"] == sh(two["grove"], "rev-parse", "HEAD:law.md")
    assert f["local_blob"] == f["git_blob"] and f["blob_match"] is True
    assert f["sha256"] == hashlib.sha256(data).hexdigest()
    assert a["trees"][0]["path"] == "" and len(a["trees"][0]["git_tree"]) == 40


def test_a_file_changed_on_disk_is_a_mismatch_and_left_out(two):
    (two["grove"] / "plan.md").write_text("tampered Q42\n", encoding="utf-8")
    idx = xref.build([("grove", two["grove"])])
    f = next(f for f in idx["arrival"]["grove"]["files"] if f["path"] == "plan.md")
    assert f["blob_match"] is False
    assert not refs(idx, path="plan.md")
    assert "mismatch  plan.md" in xref.report(idx)


def test_empty_and_unreachable_are_different_states(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "init", "-q")
    assert xref.arrive(empty, "e")["state"] == "empty"
    assert xref.arrive(tmp_path / "nowhere", "n")["state"] == "unreachable"
    idx = xref.build([("e", empty), ("n", tmp_path / "nowhere")])
    assert [i["state"] for i in idx["inputs"]] == ["empty", "unreachable"]


def test_ids_resolve_and_the_gaps_show(two):
    idx = xref.build([("grove", two["grove"]), ("bot", two["bot"])])
    assert refs(idx, kind="id", key="Q19")[0]["status"] == "ok"
    assert refs(idx, kind="id", key="Q99")[0]["status"] == "undefined"
    assert {r["status"] for r in refs(idx, key="CONST-VI")} == {"ok"}
    assert refs(idx, kind="id", key="§1")[0]["status"] == "ok"
    assert [d["key"] for d in idx["dupes"]] == ["Q19"]
    assert not refs(idx, key="Q77")  # fenced code
    assert not refs(idx, key="F401")  # ids are read from markdown only
    assert refs(idx, kind="pr", key="7")[0]["status"] == "not-in-history"


def test_links_and_lines(two):
    idx = xref.build([("grove", two["grove"]), ("bot", two["bot"])])
    by = {r["key"]: r for r in refs(idx, kind="link")}
    assert by["law.md"]["status"] == "ok"
    assert by["nope.md"]["status"] == "broken"
    assert by["https://example.com"]["status"] == "external"
    gh = by["https://github.com/acme/bot/blob/main/lib.py#L2"]
    assert gh["status"] == "ok" and gh["detail"] == "bot"
    lines = {(r["key"], r["start"]): r for r in refs(idx, kind="fileline")}
    # bare, and not beside plan.md
    assert lines[("lib.py", 2)]["status"] == "unanchored"
    assert lines[("law.md", 1)]["status"] == "moved"  # pinned to another blob
    idx = xref.build([("bot", two["bot"])])
    assert not refs(idx, kind="fileline")


def test_a_line_past_the_end_is_broken(tmp_path):
    repo = make(tmp_path, "r", {"a.py": "x\ny\n", "n.md": "`a.py:2` `a.py:3`\n"})
    idx = xref.build([("r", repo)])
    got = {r["start"]: r["status"] for r in refs(idx, kind="fileline")}
    assert got == {2: "ok", 3: "broken"}


def test_a_bare_filename_away_from_home_is_unanchored_not_broken(tmp_path):
    repo = make(
        tmp_path,
        "r",
        {
            "pkg/gate.py": "x\n",
            "docs/n.md": "`gate.py:840` `pkg/gate.py:9` `pkg/gate.py:1`\n",
        },
    )
    idx = xref.build([("r", repo)])
    got = {(r["key"], r["start"]): r["status"] for r in refs(idx, kind="fileline")}
    assert got == {
        ("gate.py", 840): "unanchored",
        ("pkg/gate.py", 9): "broken",
        ("pkg/gate.py", 1): "ok",
    }


def test_shas_are_found_where_they_live_or_called_external(two):
    idx = xref.build([("grove", two["grove"]), ("bot", two["bot"])])
    by = {r["key"]: r for r in refs(idx, kind="sha")}
    assert by[two["bot_sha"][:12]]["status"] == "cross-repo"
    assert by["1234abcd"]["status"] == "external"


def test_same_repos_same_bytes_and_the_cache_changes_nothing(two, tmp_path):
    repos = [("grove", two["grove"]), ("bot", two["bot"])]
    one = xref.canon(xref.build(repos))
    cache = tmp_path / "cache"
    two_ = xref.canon(xref.build(repos, cache=cache))
    three = xref.canon(xref.build(repos, cache=cache))
    assert one == two_ == three and any(cache.iterdir())
    assert xref.report(json.loads(one)) == xref.report(json.loads(three))


def test_outputs_go_through_the_record_at_full_width(two, tmp_path):
    from onescript.record import Record

    idx = xref.build([("grove", two["grove"])])
    ib, rb = xref.canon(idx).encode(), xref.report(idx).encode()
    row = xref.record_outputs(tmp_path / "box", ib, rb, idx["inputs"])
    assert row["kind"] == "xref" and row["standing"] == "unattested"
    assert row["index_sha256"] == hashlib.sha256(ib).hexdigest()
    assert len(row["hash"]) == 64
    rec = Record(tmp_path / "box", lambda: "t", "v", object())
    assert rec.verify_chain() == []
    assert [i["where"] for i in rec.pile()["items"]] == [
        "xref/index.json",
        "xref/report.txt",
    ]


def test_the_slice_carries_where_each_line_came_from(two):
    idx = xref.build([("grove", two["grove"])])
    text = xref.slice_id(idx, {"grove": two["grove"]}, "Q19")
    body = text.splitlines()[2:]
    assert body[0].startswith("def\tgrove@") and "| **Q19** | serve |" in body[0]
    assert any(b.startswith("cite\t") and "See Q19" in b for b in body)
    assert "waits on Q19" in text.splitlines()[0]
    assert text == xref.slice_id(idx, {"grove": two["grove"]}, "Q19")
    pr = xref.slice_id(idx, {"grove": two["grove"]}, "PR #7")
    assert "PR #7" in pr.splitlines()[2]


def test_the_cli_writes_index_and_report(two, tmp_path, capsys):
    out = tmp_path / "out"
    rc = xref.main(["index", "--repo", f"grove={two['grove']}", "--out", str(out)])
    assert rc == 0 and (out / "index.json").exists()
    assert "grove: populated" in (out / "report.txt").read_text()
    rc = xref.main(
        [
            "slice",
            "--index",
            str(out / "index.json"),
            "--repo",
            f"grove={two['grove']}",
            "--id",
            "Q19",
            "--out",
            str(tmp_path / "q.txt"),
        ]
    )
    assert rc == 0 and "Q19" in (tmp_path / "q.txt").read_text()
