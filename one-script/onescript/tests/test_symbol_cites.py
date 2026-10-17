# ruff: noqa: F811
"""T-cbm-1 (R15): `symbol` claims, a qualified `file.py:name` or `file.py:line`
cite in a sentence read against the code graph.

SPEC-tools.md phase T-cbm-1; SPEC.md rulings R15 and R18. Stale or unreachable
is `unchecked` and says so; a fresh graph that holds no such symbol is
`unverified` (struck); a bare name is never read. Real-binary tests index a
throwaway repo into a throwaway cache and skip without cbm.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, graph, run  # noqa: E402
from onescript.tests.test_graph import git, name_of, repo  # noqa: E402,F401

needs_cbm = pytest.mark.skipif(
    not os.access(graph.cbm_path(), os.X_OK), reason="cbm is not here"
)

# lines 1-2 helper, 5-6 wager_total, 9-11 K
SRC = (
    "def helper(x):\n    return x + 1\n\n\n"
    "def wager_total(x):\n    return helper(x)\n\n\n"
    "class K:\n    def m(self):\n        return wager_total(2)\n"
)


def populated(names: dict, paths=("one-script/onescript/gate.py",)) -> dict:
    return {
        "state": "populated",
        "why": "",
        "stamp": {"head_sha": "abc123def4567", "project": "p"},
        "paths": list(paths),
        "names": names,
    }


GATE = {
    "check_claims": [[437, 510]],
    "Run": [[100, 300]],
    "say": [[200, 210]],
    "elsewhere": [[400, 420]],
}


def verdicts(text: str, symbols: dict | None) -> list[tuple[str, str]]:
    facts = {} if symbols is None else {"symbols": symbols}
    return [
        (r["claim"], r["verdict"])
        for r in gate.check_claims(text, facts)
        if r["kind"] == "symbol"
    ]


# ── the pure check, on hand-made facts ───────────────────────────────────────
def test_a_defined_name_and_a_line_inside_a_symbol_are_verified():
    got = verdicts(
        "see gate.py:check_claims and gate.py:480.", {"gate.py": populated(GATE)}
    )
    assert got == [
        ("gate.py:check_claims", "verified"),
        ("gate.py:480", "verified"),
    ]


def test_a_forged_symbol_cite_is_unverified():
    got = verdicts(
        "gate.py:check_claimz calls gate.py:900 and gate.py:Run.nope.",
        {"gate.py": populated(GATE)},
    )
    assert got == [
        ("gate.py:check_claimz", "unverified"),
        ("gate.py:900", "unverified"),
        ("gate.py:Run.nope", "unverified"),
    ]


def test_a_class_dot_method_cite_needs_the_method_inside_the_class():
    ok = verdicts("gate.py:Run.say", {"gate.py": populated(GATE)})
    assert ok == [("gate.py:Run.say", "verified")]
    outside = verdicts("gate.py:Run.elsewhere", {"gate.py": populated(GATE)})
    assert outside == [("gate.py:Run.elsewhere", "unverified")]


def test_a_line_range_must_sit_inside_one_symbol():
    s = {"gate.py": populated(GATE)}
    assert verdicts("gate.py:437-510", s) == [("gate.py:437-510", "verified")]
    assert verdicts("gate.py:430-510", s) == [("gate.py:430-510", "unverified")]
    assert verdicts("gate.py:510-437", s) == [("gate.py:510-437", "unverified")]


def test_bare_names_and_other_shapes_are_never_read():
    s = {"gate.py": populated(GATE)}
    text = "`check_claimz` and `gate.py` and gate.py: and see https://x.io/a/b.py"
    assert verdicts(text, s) == []
    assert verdicts("gate.py:check_claimz", None) == []  # no symbols key, no rows


def test_stale_and_unreachable_are_unchecked_and_say_so():
    for state in ("stale", "unreachable"):
        entry = {
            "state": state,
            "why": "because",
            "stamp": None,
            "paths": [],
            "names": {},
        }
        rows = gate.check_claims(
            "gate.py:check_claims", {"symbols": {"gate.py": entry}}
        )
        assert [(r["verdict"], r["record"]) for r in rows] == [
            ("unchecked", f"graph {state}: because")
        ]


def test_empty_is_struck_and_a_missing_entry_is_unchecked():
    empty = {
        "state": "empty",
        "why": "not in the graph at p",
        "stamp": {},
        "paths": [],
        "names": {},
    }
    r = gate.check_claims("gate.py:x", {"symbols": {"gate.py": empty}})
    assert [(x["verdict"], x["record"]) for x in r] == [
        ("unverified", "not in the graph at p")
    ]
    r = gate.check_claims("gate.py:x", {"symbols": {}})
    assert [x["verdict"] for x in r] == ["unchecked"]


def test_an_ambiguous_file_is_unchecked_not_guessed():
    two = populated(GATE, paths=("a/run.py", "b/run.py"))
    r = gate.check_claims("run.py:check_claims", {"symbols": {"run.py": two}})
    assert [x["verdict"] for x in r] == ["unchecked"] and "2 files" in r[0]["record"]


def test_other_kinds_are_unchanged_by_the_symbol_kind():
    facts = {"counts": {"test": 3}, "symbols": {"gate.py": populated(GATE)}}
    kinds = {
        r["kind"] for r in gate.check_claims("3 test, gate.py:check_claims", facts)
    }
    assert kinds == {"count", "symbol"}


def test_run_say_asks_the_graph_only_when_given_a_reader(tmp_path):
    seen = []

    def reader(text):
        seen.append(text)
        return {"gate.py": populated(GATE)}

    r = run.Run.__new__(run.Run)
    r.claims, r.sys = [], ("desk", "python")
    r.rec = type("R", (), {"append": lambda self, *a, **k: k})()
    rows = r.say("gate.py:check_claimz", {}, symbols=reader)
    assert seen == ["gate.py:check_claimz"] and rows[0]["verdict"] == "unverified"
    assert r.say("gate.py:check_claimz", {}) == []


def test_morning_counts_unchecked_claims():
    from onescript import view

    claims = [
        {"verdict": "verified", "kind": "symbol", "claim": "a", "record": ""},
        {"verdict": "unchecked", "kind": "symbol", "claim": "b", "record": ""},
    ]
    rep = {
        "open_turns": [],
        "pile": {"failing": [], "counts": {}},
        "awaiting": [],
        "offers": [],
        "surfaced": [],
        "witness": {"split": [], "agreed": []},
        "predictions": [],
        "grades": {},
        "chain_rows": 0,
        "version": "v",
    }
    assert "claims checked: 1/2 verified · 1 unchecked" in view.morning(
        rep, None, claims
    )


# ── symbols_of / symbols_for, with a fake runner ─────────────────────────────
def stored(cache: Path, root: Path, name="p"):
    cache.mkdir(exist_ok=True)
    db = sqlite3.connect(cache / f"{name}.db")
    db.execute(
        "create table projects (name text primary key, indexed_at text, root_path text)"
    )
    db.execute(
        "insert into projects values (?, '2099-01-01T00:00:00Z', ?)", (name, str(root))
    )
    db.commit()
    db.close()


def reply(rows: str):
    def run_(tool, args):
        if tool == "list_projects":
            return (
                '{"projects": [{"name": "p", "root_path": "%s", "branch": "main", "nodes": 3, "edges": 1}]}'
                % reply.root
            )
        return rows

    return run_


def test_symbols_for_reads_the_cypher_rows_into_names(repo):
    root, cache = repo
    stored(cache, root)
    reply.root = str(root)
    out = (
        "rows: 2  (cols: n.name n.file_path n.start_line n.end_line n.label)\n"
        '  helper m.py "1" "2" Function\n  K m.py "9" "11" Class\ntotal: 2\n'
    )
    got = graph.symbols_for(
        "see m.py:K and m.py:5", "p", runner=reply(out), cache=cache
    )
    assert list(got) == ["m.py"]
    e = got["m.py"]
    assert e["state"] == "populated" and e["paths"] == ["m.py"]
    assert e["names"] == {"helper": [[1, 2]], "K": [[9, 11]]}
    assert verdicts("see m.py:K and m.py:5", got) == [
        ("m.py:K", "verified"),
        ("m.py:5", "unverified"),
    ]


def test_cbm_unreachable_is_unchecked(repo):
    root, cache = repo

    def boom(tool, args):
        raise graph.Unreachable("cbm: gone")

    got = graph.symbols_for("m.py:helper", "p", runner=boom, cache=cache)
    assert got["m.py"]["state"] == "unreachable"
    assert verdicts("m.py:helper", got) == [("m.py:helper", "unchecked")]


def test_an_unreadable_row_is_unreachable_not_empty(repo):
    root, cache = repo
    stored(cache, root)
    reply.root = str(root)
    out = "rows: 1  (cols: x)\n  nonsense\ntotal: 1\n"
    got = graph.symbols_for("m.py:helper", "p", runner=reply(out), cache=cache)
    assert got["m.py"]["state"] == "unreachable"


def test_a_cite_cannot_put_a_quote_into_the_query(repo):
    root, cache = repo
    asked = []

    def spy(tool, args):
        asked.append(args)
        raise graph.Unreachable("no")

    graph.symbols_for('x.py:a" OR 1=1', "p", runner=spy, cache=cache)
    graph.symbols_of("p", 'a"b.py', runner=spy, cache=cache)
    assert all('"b' not in str(a) for a in asked)
    assert graph.qualified_cites('x.py:a" OR 1=1')[0]["ref"] == "a"


def test_only_max_files_are_asked_the_rest_are_unchecked(repo):
    root, cache = repo
    text = " ".join(f"f{i}.py:x" for i in range(graph.MAX_FILES + 3))

    def boom(tool, args):
        raise graph.Unreachable("no")

    got = graph.symbols_for(text, "p", runner=boom, cache=cache)
    assert len(got) == graph.MAX_FILES
    assert verdicts(text, got).count(("f10.py:x", "unchecked")) == 1


# ── the real binary ───────────────────────────────────────────────────────────
@needs_cbm
def test_real_graph_verified_forged_empty_and_stale(repo):
    root, cache = repo
    assert graph.reindex(root)["state"] == "populated"
    project = name_of(root)
    text = (
        "m.py:wager_total, m.py:K.m, m.py:6, m.py:wager_totl, m.py:99, nope.py:helper"
    )
    got = graph.symbols_for(text, project, cache=cache)
    assert dict(verdicts(text, got)) == {
        "m.py:wager_total": "verified",
        "m.py:K.m": "verified",
        "m.py:6": "verified",
        "m.py:wager_totl": "unverified",
        "m.py:99": "unverified",
        "nope.py:helper": "unverified",  # a fresh graph with no such file: struck
    }
    assert got["nope.py"]["state"] == "empty"
    # a commit after the index: stale -> unchecked, never verified from old rows
    (root / "m.py").write_text("def other():\n    return 2\n")
    git(
        root,
        "commit",
        "-qam",
        "two",
        date=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600)),
    )
    stale = graph.symbols_for(text, project, cache=cache)
    assert {v for _, v in verdicts(text, stale)} == {"unchecked"}
    assert stale["m.py"]["state"] == "stale"
