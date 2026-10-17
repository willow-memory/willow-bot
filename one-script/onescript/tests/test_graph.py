"""T-cbm-4 (R15): a stamped, three-state read of the code graph.

SPEC-tools.md phase T-cbm-4. The real-binary tests index a throwaway repo into
a throwaway cache (CBM_CACHE_DIR) and are skipped when cbm is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import graph  # noqa: E402

CBM = graph.cbm_path()
SRC = (
    "def helper(x):\n    return x + 1\n\n\n"
    "def wager_total(x):\n    return helper(x)\n\n\n"
    "class K:\n    def m(self):\n        return wager_total(2)\n"
)
needs_cbm = pytest.mark.skipif(not os.access(CBM, os.X_OK), reason="cbm is not here")


def git(root, *args, date=None):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    if date:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, env=env, capture_output=True
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("CBM_CACHE_DIR", str(cache))
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "m.py").write_text(SRC)
    git(root, "add", ".")
    git(root, "commit", "-qm", "one")
    return root, cache


def name_of(root):
    got = json.loads(
        subprocess.run(
            [CBM, "list_projects", "{}"], capture_output=True, text=True
        ).stdout
    )
    return next(p["name"] for p in got["projects"] if p["root_path"] == str(root))


# ── fake runner: the states, apart ──────────────────────────────────────────
def fake(root, branch="main", total=2, nodes=5, edges=7, name="p"):
    proj = {
        "name": name,
        "root_path": str(root),
        "branch": branch,
        "nodes": nodes,
        "edges": edges,
    }

    def run(tool, args):
        if tool == "list_projects":
            return json.dumps({"projects": [proj]})
        return f"total: {total}\nresults: {total}  (rows: name)\n  a Function\n  b Function\nhas_more: false\n"[
            : None if total else None
        ]

    return run


def test_unreachable_bad_binary_path():
    r = graph.graph_read(
        "p", "search_graph", {}, runner=graph.cbm_runner("/nonexistent/cbm")
    )
    assert r["state"] == "unreachable" and r["stamp"] is None and r["rows"] == []


def test_unreachable_when_the_runner_raises():
    def boom(tool, args):
        raise graph.Unreachable("down")

    assert graph.graph_read("p", "search_graph", runner=boom)["state"] == "unreachable"


def test_unreachable_on_unreadable_output(repo):
    root, cache = repo
    r = graph.graph_read(
        "p", "search_graph", runner=lambda t, a: "garbage", cache=cache
    )
    assert r["state"] == "unreachable"


def test_a_tool_that_installs_or_updates_is_not_a_read():
    for tool in ("install", "update", "--ui", "index_repository", "delete_project"):
        r = graph.graph_read("p", tool, runner=lambda t, a: pytest.fail("ran " + t))
        assert r["state"] == "unreachable"
    with pytest.raises(graph.Unreachable):
        graph.cbm_runner("/bin/true")("install", {})
    with pytest.raises(graph.Unreachable):
        graph.cbm_runner("/bin/true")("update", {})


@needs_cbm
def test_empty_for_an_unknown_project():
    r = graph.graph_read("no-such-project-zzz", "search_graph", {"name_pattern": "x"})
    assert (
        r["state"] == "empty" and r["stamp"] is None and "not in the graph" in r["why"]
    )


@needs_cbm
def test_populated_then_stale_after_a_commit_then_fresh_again(repo):
    root, cache = repo
    assert graph.reindex(root)["state"] == "populated"
    project = name_of(root)
    r = graph.graph_read(
        project, "search_graph", {"name_pattern": "wager"}, cache=cache
    )
    assert r["state"] == "populated", r
    s = r["stamp"]
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert (s["project"], s["head_sha"], s["dirty"]) == (project, head, False)
    assert s["nodes"] > 0 and s["edges"] >= 0 and r["total"] >= 1 and r["rows"]
    # two reads are byte-identical (E2)
    again = graph.graph_read(
        project, "search_graph", {"name_pattern": "wager"}, cache=cache
    )
    assert json.dumps(again["rows"]) == json.dumps(r["rows"])
    # empty is said as "not in the graph at <stamp>"
    e = graph.graph_read(project, "search_graph", {"name_pattern": "zzqq"}, cache=cache)
    assert e["state"] == "empty" and "not in the graph at" in e["why"] and e["stamp"]
    # a commit after the index: stale, rows withheld
    (root / "m.py").write_text("def wager_total():\n    return 2\n")
    git(
        root,
        "commit",
        "-qam",
        "two",
        date=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600)),
    )
    st = graph.graph_read(
        project, "search_graph", {"name_pattern": "wager"}, cache=cache
    )
    assert (
        st["state"] == "stale" and st["rows"] == [] and st["stamp"]["head_sha"] != head
    )
    assert "HEAD moved" in st["why"]


@needs_cbm
def test_a_dirty_tracked_file_edited_after_the_index_is_stale(repo):
    root, cache = repo
    graph.reindex(root)
    project = name_of(root)
    time.sleep(1.2)  # indexed_at has a one-second resolution
    (root / "m.py").write_text("def wager_total():\n    return 9\n")
    r = graph.graph_read(
        project, "search_graph", {"name_pattern": "wager"}, cache=cache
    )
    assert r["state"] == "stale" and r["stamp"]["dirty"] is True and "m.py" in r["why"]


@needs_cbm
def test_checking_out_another_branch_is_stale(repo):
    root, cache = repo
    graph.reindex(root)
    project = name_of(root)
    time.sleep(1.2)  # HEAD's reflog has a one-second resolution
    git(root, "checkout", "-q", "-b", "other")
    r = graph.graph_read(
        project, "search_graph", {"name_pattern": "wager"}, cache=cache
    )
    assert r["state"] == "stale" and "HEAD moved" in r["why"]


def test_fake_runner_populated_and_empty_are_stamped(repo):
    root, cache = repo
    # a hand-made cbm store row stands in for the index time
    import sqlite3

    cache.mkdir(exist_ok=True)
    db = sqlite3.connect(cache / "p.db")
    db.execute(
        "create table projects (name text primary key, indexed_at text, root_path text)"
    )
    db.execute(
        "insert into projects values ('p', '2099-01-01T00:00:00Z', ?)", (str(root),)
    )
    db.commit()
    db.close()
    ok = graph.graph_read("p", "search_graph", {}, runner=fake(root), cache=cache)
    assert ok["state"] == "populated" and ok["total"] == 2 and len(ok["rows"]) == 2
    assert (ok["stamp"]["nodes"], ok["stamp"]["edges"]) == (5, 7)
    none = graph.graph_read(
        "p", "search_graph", {}, runner=fake(root, total=0), cache=cache
    )
    assert none["state"] == "empty" and none["rows"] == []
    # the index time cannot be read: said, never passed
    r = graph.graph_read(
        "p", "search_graph", {}, runner=fake(root), cache=cache / "elsewhere"
    )
    assert r["state"] == "unreachable" and "cannot tell when" in r["why"]


# ── revision round (Opus audit of 48b3408) ──────────────────────────────────
def indexed(repo):
    root, cache = repo
    assert graph.reindex(root)["state"] == "populated"
    return name_of(root)


@needs_cbm
@pytest.mark.parametrize(
    "tool,hit,miss",
    [
        ("search_graph", {"name_pattern": "wager"}, {"name_pattern": "zzqq"}),
        (
            "query_graph",
            {"query": "MATCH (n:Function) RETURN n.name LIMIT 3"},
            {"query": 'MATCH (n:Function {name:"zz"}) RETURN n.name'},
        ),
        (
            "trace_path",
            {"function_name": "helper", "direction": "inbound"},
            {"function_name": "nonesuch", "direction": "inbound"},
        ),
        ("search_code", {"pattern": "helper"}, {"pattern": "zzzqq"}),
        ("get_architecture", {}, None),
        ("detect_changes", None, {}),
        ("get_code_snippet", "SNIPPET", {"qualified_name": "nope"}),
    ],
)
def test_S11_every_read_tool_is_parsed_from_its_real_output(repo, tool, hit, miss):
    project = indexed(repo)
    if hit == "SNIPPET":
        hit = {"qualified_name": f"{project}.m.helper"}
    if hit is not None:
        r = graph.graph_read(project, tool, hit)
        assert r["state"] == "populated", (tool, r)
        assert r["total"] >= 1 and r["rows"], (tool, r)
    if miss is not None:
        e = graph.graph_read(project, tool, miss)
        assert e["state"] == "empty" and e["rows"] == [], (tool, e)
        assert "not in the graph at" in e["why"]


def test_S11_parse_refuses_output_that_did_not_say_its_count():
    for tool, out in (
        ("search_graph", "hello"),
        ("query_graph", "rows: 2\n  a\n"),
        ("search_code", "elapsed_ms: 3"),
        ("get_architecture", "project: x"),
        ("detect_changes", "base: main"),
        ("search_graph", ""),
        ("search_graph", '{"error": "no such project"}'),
        ("get_code_snippet", '{"error": "x"}'),
        ("search_graph", '{"total": 5, "items": [1]}'),
    ):
        with pytest.raises(graph.Unreachable):
            graph.parse(tool, out)
    assert graph.parse("trace_path", "") == (0, [])


@needs_cbm
def test_S9_an_untracked_file_added_after_the_index_is_stale(repo):
    root, cache = repo
    project = indexed(repo)
    time.sleep(1.2)
    (root / "new.py").write_text("def brand_new():\n    pass\n")
    r = graph.graph_read(project, "search_graph", {"name_pattern": "wager"})
    assert r["state"] == "stale" and "new.py" in r["why"] and r["stamp"]["dirty"]


@needs_cbm
def test_S9_a_file_edited_then_reverted_after_the_index_is_stale(repo):
    root, cache = repo
    project = indexed(repo)
    time.sleep(1.2)
    (root / "m.py").write_text(SRC + "# x\n")
    git(root, "checkout", "--", "m.py")  # clean again, but its mtime is new
    r = graph.graph_read(project, "search_graph", {"name_pattern": "wager"})
    assert r["state"] == "stale"  # by m.py's mtime or the rewritten staging area


@needs_cbm
def test_a_fresh_clean_index_stays_populated_and_reads_do_not_touch_git(repo):
    root, cache = repo
    project = indexed(repo)
    before = (root / ".git" / "index").stat().st_mtime_ns
    time.sleep(1.1)
    r = graph.graph_read(project, "search_graph", {"name_pattern": "wager"})
    assert r["state"] == "populated" and r["stamp"]["dirty"] is False, r["why"]
    assert (root / ".git" / "index").stat().st_mtime_ns == before  # N1


def test_S12_a_missing_default_binary_is_unreachable_not_empty(monkeypatch):
    monkeypatch.setenv("ONESCRIPT_CBM", "/nonexistent/cbm")
    assert graph.cbm_path() == "/nonexistent/cbm"
    r = graph.graph_read("anything", "search_graph", {"name_pattern": "x"})
    assert r["state"] == "unreachable" and r["rows"] == [] and r["stamp"] is None
    assert graph.reindex("/tmp")["state"] == "unreachable"


def test_N2_cbm_cache_dir_is_honoured(tmp_path, monkeypatch):
    monkeypatch.delenv("ONESCRIPT_CBM_CACHE", raising=False)
    monkeypatch.setenv("CBM_CACHE_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(graph.Unreachable) as e:
        graph._indexed_at("p", None)
    assert str(tmp_path / "nowhere") in str(e.value) or "cannot tell" in str(e.value)


# ── round 3 ─────────────────────────────────────────────────────────────────
def _stale_after(repo, act):
    root, cache = repo
    project = indexed(repo)
    time.sleep(1.2)
    act(root)
    return graph.graph_read(project, "search_graph", {"name_pattern": "wager"})


@needs_cbm
@pytest.mark.parametrize("how", ["git-rm", "git-mv", "rm-then-add-A"])
def test_NEW1_a_removed_or_renamed_path_reads_stale(repo, how):
    acts = {
        "git-rm": lambda r: git(r, "rm", "-q", "m.py"),
        "git-mv": lambda r: git(r, "mv", "m.py", "n.py"),
        "rm-then-add-A": lambda r: (os.remove(r / "m.py"), git(r, "add", "-A")),
    }
    r = _stale_after(repo, acts[how])
    assert r["state"] == "stale" and r["rows"] == [], r["why"]


FAKE = """#!/bin/sh
case "$1" in
 list_projects) exec %(cbm)s list_projects '{}';;
 trace_path|get_code_snippet)
   [ -n "$FAKE_ERR" ] && echo "$FAKE_ERR" >&2
   exit ${FAKE_RC:-1};;
esac
"""


@needs_cbm
@pytest.mark.parametrize(
    "rc,err,state",
    [
        ("1", "", "empty"),  # cbm's own answer for a name it lacks
        ("1", "fatal: database is locked", "unreachable"),
        ("2", "", "unreachable"),
        ("139", "", "unreachable"),
    ],
)
def test_NEW2_a_miss_is_empty_only_on_rc1_with_no_stderr(
    repo, tmp_path, monkeypatch, rc, err, state
):
    project = indexed(repo)
    fake = tmp_path / "fakecbm.sh"
    fake.write_text(FAKE % {"cbm": CBM})
    fake.chmod(0o755)
    monkeypatch.setenv("FAKE_RC", rc)
    monkeypatch.setenv("FAKE_ERR", err)
    run = graph.cbm_runner(str(fake))
    for tool, args in (
        ("trace_path", {"function_name": "nonesuch", "direction": "inbound"}),
        ("get_code_snippet", {"qualified_name": "nope"}),
    ):
        r = graph.graph_read(project, tool, args, runner=run)
        assert r["state"] == state, (tool, rc, err, r["why"])


def test_NEW2_a_wrong_argument_key_is_refused_not_empty():
    def boom(tool, args):
        raise AssertionError("must not run")

    for tool, args in (
        ("trace_path", {"name": "helper"}),
        ("get_code_snippet", {"function_name": "x"}),
        ("trace_path", None),
    ):
        r = graph.graph_read("p", tool, args, runner=boom)
        assert r["state"] == "unreachable" and "refused" in r["why"]
