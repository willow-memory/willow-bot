"""The four gates and layers 5-7, each held to something that happened on 2026-10-02."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import boot, gate  # noqa: E402
from onescript.run import Run  # noqa: E402

KEYS = {"hanuman": b"k-hanuman"}
LAW = {
    "trace_ids": ["CONST-III", "CONST-VI"],
    "grants": [
        {"who": "run", "where": "github:willows-grove", "classes": ["authored"]},
    ],
}


def clock():
    n = iter(range(10**6))
    return lambda: f"2026-10-02T00:00:{next(n):06d}Z"


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path / "box", KEYS, LAW, clock())


# ── the four gates ───────────────────────────────────────────────────────────
def test_tests_gate_pass_fail_and_cant_run(tmp_path):
    calls = iter([(0, "29 passed"), (1, "1 failed")])
    rows = boot.tests_gate(
        [
            {"name": "a-onescript", "argv": ["x"], "cwd": str(tmp_path)},
            {"name": "b-sketch", "argv": ["x"], "cwd": str(tmp_path)},
            {
                "name": "c-reliability",
                "argv": ["x"],
                "cwd": str(tmp_path),
                "needs": ["aggregate_not_installed_here"],
            },
        ],
        runner=lambda argv, cwd: next(calls),
    )
    assert [(r["where"], r["verdict"]) for r in rows] == [
        ("a-onescript", "satisfied"),
        ("b-sketch", "failing"),
        ("c-reliability", "failing"),
    ]
    assert rows[2]["why"].startswith("can't run: needs")


def test_toolchain_gate_catches_the_ruff_that_bit_twice():
    rows = boot.toolchain_gate(
        {"tools": {"ruff": "0.16.7"}, "python": ["3.11", "3.12", "3.13"]},
        version_of=lambda tool: "0.15.20",
        py="3.11",
    )
    assert rows[0]["verdict"] == "failing"
    assert rows[0]["why"] == "0.15.20 on PATH; pinned 0.16.7"
    assert rows[1]["verdict"] == "satisfied"


def test_freshness_gate_stale_clone_and_doc_built_on_old_law():
    git = {
        ("rev-parse", "@{u}"): "abc",
        ("rev-list", "--left-right", "--count", "HEAD...@{u}"): "0\t3",
    }
    rows = boot.freshness_gate(
        ["Forge"],
        [{"where": "constitution-proposal/", "built_on": "0.7"}],
        "0.8",
        git=lambda repo, *args: git.get(args, ""),
    )
    assert [r["verdict"] for r in rows] == ["failing", "failing"]
    assert rows[0]["why"] == "3 commit(s) behind its remote"
    assert rows[1]["why"] == "built on Draft 0.7; the law is 0.8"


def test_reachability_gate_keeps_three_states():
    def down():
        raise ConnectionError("404")

    rows = boot.reachability_gate(
        [
            {"name": "grove-mcp", "probe": down},
            {"name": "inbox", "probe": lambda: []},
            {"name": "store", "probe": lambda: [1]},
        ]
    )
    assert [r["state"] for r in rows] == ["unreachable", "empty", "populated"]
    assert [r["verdict"] for r in rows] == ["failing", "differently", "satisfied"]


def test_failing_gates_make_boot_a_hard_close(run):
    b = run.checkin(
        {"pins": {"tools": {"ruff": "0.16.7"}}, "version_of": lambda tool: "0.15.20"}
    )
    assert b["hard_close"]
    assert "toolchain: ruff: 0.15.20 on PATH; pinned 0.16.7" in b["lines"]


# ── layer 5: claims ──────────────────────────────────────────────────────────
FACTS = {
    "counts": {"scripts": 9, "pointers": 84},
    "green_sha": "96d1f4e5b475",
    "turns": 231,
    "operator_text": "Every gate fails closed, and loud.\n> egress means outbound",
}


def test_a_wrong_count_is_unverified_with_the_records_value(run):
    rows = run.say("The draft has seven scripts and 84 pointers.", FACTS)
    by = {r["claim"]: r for r in rows}
    assert by["seven scripts"]["verdict"] == "unverified"
    assert by["seven scripts"]["record"] == 9
    assert by["84 pointers"]["verdict"] == "verified"


def test_green_must_name_the_commit_it_is_about():
    rows = gate.check_claims("All tests passed. CI went green on 3f4baeb.", FACTS)
    assert [(r["verdict"], r["record"]) for r in rows] == [
        ("unverified", "names no commit"),
        ("unverified", "the record has green on 96d1f4e5b475"),
    ]
    ok = gate.check_claims("Tests passed on 96d1f4e.", FACTS)
    assert ok[0]["verdict"] == "verified"


def test_quotes_and_turns_are_checked_against_the_humans_words():
    text = 'At T67 you said "every gate fails closed, and loud" and at T999 "fail open is fine here".'
    rows = gate.check_claims(text, FACTS)
    assert [(r["kind"], r["verdict"]) for r in rows] == [
        ("turn", "verified"),
        ("quote", "verified"),
        ("turn", "unverified"),
        ("quote", "unverified"),
    ]


# ── layer 6: boundaries ──────────────────────────────────────────────────────
def _pile(run):
    w = gate.system()
    run.rec.write_file(w, "next-pile.md", b"a" * 10, provenance="authored")
    run.rec.write_file(w, "session-flow.json", b"b" * 20, provenance="transcript")
    run.rec.write_file(w, "pile.json.copy", b"c" * 5, provenance="memory")
    run.rec.write_file(w, "odd.bin", b"d")  # never classed
    return run.rec.pile()


def test_a_push_card_groups_every_file_by_provenance(run):
    d = gate.push_card(
        _pile(run),
        ["next-pile.md", "session-flow.json"],
        "run",
        "github:elsewhere",
        LAW,
    )
    assert d.verdict == "awaiting_grant"
    assert set(d.card["groups"]) == {"authored", "transcript"}
    assert d.card["bytes"] == 30


def test_a_blanket_grant_never_covers_transcript_or_memory(run):
    d = gate.push_card(
        _pile(run),
        ["next-pile.md", "session-flow.json", "pile.json.copy"],
        "run",
        "github:willows-grove",
        LAW,
    )
    assert d.verdict == "awaiting_grant"
    assert d.card["uncovered"] == ["pile.json.copy", "session-flow.json"]
    authored_only = gate.push_card(
        _pile(run), ["next-pile.md"], "run", "github:willows-grove", LAW
    )
    assert authored_only.verdict == "pass"


def test_an_unclassed_or_unlisted_file_never_leaves(run):
    pile = _pile(run)
    for files in (["odd.bin"], ["never-written.md"]):
        d = gate.push_card(pile, files, "run", "github:willows-grove", LAW)
        assert d.verdict == "refused"


# ── layer 7: mandate ─────────────────────────────────────────────────────────
MANDATES = {
    "m-write": {
        "turn": 240,
        "source": "human",
        "covers": ["write"],
        "words": "think of two more layers ... and write them in",
        "scope": ["docs/design/one-script/"],
    },
    "m-pr": {
        "turn": 236,
        "source": "human",
        "covers": ["write", "commit"],
        "words": "I would like your proposals separated out as a draft PR",
        "scope": ["docs/design/one-script/"],
    },
    "m-hook": {
        "turn": 0,
        "source": "hook",
        "covers": ["push"],
        "words": "Please commit and push these changes",
        "scope": [""],
    },
}
CONSTRAINTS = [
    {
        "rule": "No Forge code changes until the day-two run finishes",
        "paths": ["Forge/"],
        "kinds": ["write", "commit", "push"],
    }
]


def test_an_unknown_referent_is_a_hard_close_not_a_guess(run):
    row = run.act(
        {
            "kind": "write",
            "mandate": "m-write",
            "refers_to": ["the proposed shutdown"],
            "paths": ["docs/design/one-script/HANDOFF.md"],
        },
        MANDATES,
        CONSTRAINTS,
    )
    assert row["verdict"] == "hard_close"
    assert row["card"]["unresolved"] == ["the proposed shutdown"]


def test_the_words_must_cover_the_act(run):
    row = run.act(
        {"kind": "branch", "mandate": "m-pr", "paths": ["docs/design/one-script/"]},
        MANDATES,
        CONSTRAINTS,
    )
    assert row["verdict"] == "awaiting_mandate"


def test_a_hook_never_authorises(run):
    row = run.act(
        {
            "kind": "push",
            "mandate": "m-hook",
            "paths": [""],
            "files": [],
            "who": "run",
            "where": "github:willows-grove",
        },
        MANDATES,
        CONSTRAINTS,
    )
    assert row["verdict"] == "refused" and "hook" in row["reason"]


def test_standing_rules_are_checked_not_remembered(run):
    row = run.act(
        {
            "kind": "write",
            "mandate": "m-write",
            "paths": ["Forge/benchmarks/escalation/reliability.py"],
        },
        {**MANDATES, "m-write": {**MANDATES["m-write"], "scope": [""]}},
        CONSTRAINTS,
    )
    assert row["verdict"] == "refused" and "day-two" in row["reason"]


def test_new_scope_is_offered_not_started(run):
    row = run.act(
        {
            "kind": "write",
            "mandate": "m-write",
            "paths": ["docs/design/one-box/README.md"],
        },
        MANDATES,
        CONSTRAINTS,
    )
    assert row["verdict"] == "flagged" and "Rule 4" in row["reason"]


def test_no_mandate_at_all_waits(run):
    row = run.act({"kind": "write", "paths": ["x"]}, MANDATES, CONSTRAINTS)
    assert row["verdict"] == "awaiting_mandate"


# ── the close, run as one script ────────────────────────────────────────────
def test_the_close_tonight_as_one_script(tmp_path):
    r = Run(tmp_path / "box", KEYS, LAW, clock())
    b = r.checkin(
        {"pins": {"tools": {"ruff": "0.16.7"}}, "version_of": lambda tool: "0.15.20"}
    )
    _pile(r)
    r.act(
        {
            "kind": "write",
            "mandate": "m-write",
            "refers_to": ["the proposed shutdown"],
            "paths": ["docs/design/one-script/HANDOFF.md"],
        },
        MANDATES,
        CONSTRAINTS,
    )
    r.act(
        {
            "kind": "push",
            "mandate": "m-hook",
            "paths": [""],
            "files": [],
            "who": "run",
            "where": "github:willows-grove",
        },
        MANDATES,
        CONSTRAINTS,
    )
    r.say("All four tests passed.", FACTS)
    _, screen = r.checkout(boot_report=b)
    needs = screen.split("DISAGREEMENTS")[0]
    assert "ruff: 0.15.20 on PATH" in needs
    assert "hard_close · write" in needs
    assert "refused · push" in needs
    assert "unverified result" in needs
    # the screen waits; nothing above was performed
    assert not (tmp_path / "box" / "docs").exists()
