"""Tests for the code-first resolver tier (D0) -- stdlib only, no network."""
from __future__ import annotations

import json

from willow_bot.deterministic import cli
from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.resolvers import (
    STATUS_ESCALATE,
    STATUS_FLOWERING_REQUIRED,
    STATUS_RESOLVED,
    STATUS_VERBATIM,
    parse_excerpt_fields,
    resolve_fixture,
    resolve_fixtures_dir,
    run_resolve,
    score_resolution,
    summarize,
)


# ---------------------------------------------------------------------------
# Parser edge cases
# ---------------------------------------------------------------------------


def test_parse_excerpt_fields_space_separated():
    fields = parse_excerpt_fields("dispatch_id 615C332F; to_app loki")
    assert fields == {"dispatch_id": "615C332F", "to_app": "loki"}


def test_parse_excerpt_fields_colon_separated_value_contains_colon():
    fields = parse_excerpt_fields(
        "title: CI red: willow-memory/willow-mcp#645 — 1 leg(s): dependabot-pr"
    )
    assert fields["title"] == (
        "CI red: willow-memory/willow-mcp#645 — 1 leg(s): dependabot-pr"
    )


def test_parse_excerpt_fields_ignores_unparseable_segment():
    # A segment with no colon and no space cannot be split into key/value
    # -- it must be ignored, never guessed.
    fields = parse_excerpt_fields("standalone; to_app hanuman")
    assert "standalone" not in fields
    assert fields["to_app"] == "hanuman"


def test_parse_excerpt_fields_first_writer_wins_on_duplicate_key():
    fields = parse_excerpt_fields("id first; id second")
    assert fields["id"] == "first"


def test_parse_excerpt_fields_empty_text():
    assert parse_excerpt_fields("") == {}
    assert parse_excerpt_fields(None) == {}


# ---------------------------------------------------------------------------
# G1 -- to_app
# ---------------------------------------------------------------------------


def _g1_fixture(text: str, expected_to_app: str | None = None) -> dict:
    return {
        "id": "S-growth-01",
        "class": "G1",
        "excerpts": [{"id": "ex-615C332F", "text": text}],
        "expected": {
            "to_app": expected_to_app,
            "must_cite": ["ex-615C332F"],
        },
    }


def test_g1_resolved_when_to_app_present():
    fixture = _g1_fixture(
        "dispatch_id 615C332F; to_app loki; summary: Audit delta", "loki"
    )
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == "loki"
    assert resolution["cites"] == ["ex-615C332F"]
    score = score_resolution(fixture, resolution)
    assert score["scored"] is True
    assert score["correct"] is True


def test_g1_escalates_on_prose_only_no_to_app_field():
    # Mirrors S-growth-03: a gather dispatch written up as prose, no
    # structured to_app field -- never infer a seat from prose.
    fixture = {
        "id": "S-growth-03",
        "class": "G1",
        "excerpts": [
            {
                "id": "ex-B2E3BBF4",
                "text": "Ada gather — dispatch B2E3BBF4 · Wave 0a. Written: 2026-09-24. status: complete.",
            }
        ],
        "expected": {"to_app": "ada", "must_cite": ["ex-B2E3BBF4"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "no_to_app_field"
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False


# ---------------------------------------------------------------------------
# G2 -- title
# ---------------------------------------------------------------------------


def test_g2_resolved_verbatim_title_under_limit():
    fixture = {
        "id": "S-growth-04",
        "class": "G2",
        "excerpts": [
            {
                "id": "ex-0079cdb7",
                "text": (
                    "id 0079cdb7; title: CI red: willow-memory/willow-mcp#645 "
                    "— 1 leg(s): dependabot-pr; source_ref: https://example.com"
                ),
            }
        ],
        "expected": {
            "max_chars": 120,
            "must_name": ["willow-mcp", "dependabot-pr"],
            "reference_title": "CI red: willow-memory/willow-mcp#645 — 1 leg(s): dependabot-pr",
        },
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == fixture["expected"]["reference_title"]
    score = score_resolution(fixture, resolution)
    assert score["correct"] is True


def test_g2_escalates_when_title_over_limit():
    # Mirrors S-growth-06: title is well over 120 chars.
    long_title = "CI stuck: " + "x" * 120
    fixture = {
        "id": "S-growth-06",
        "class": "G2",
        "excerpts": [{"id": "ex-d77d0c46", "text": f"id d77d0c46; title: {long_title}"}],
        "expected": {"max_chars": 120, "must_name": ["willow-mcp"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "title_over_limit"


# ---------------------------------------------------------------------------
# G3 -- gap id + question
# ---------------------------------------------------------------------------


def test_g3_escalates_on_truncated_question():
    # Mirrors S-growth-07: question cut mid-sentence, no terminal punctuation.
    fixture = {
        "id": "S-growth-07",
        "class": "G3",
        "excerpts": [
            {
                "id": "ex-7fab1a78081b",
                "text": (
                    "id 7fab1a78081b; topic forge-convergence/local-models-step0; "
                    "status open; asked_count 1; question: Step 0 wiring: which"
                ),
            }
        ],
        "expected": {
            "must_cite": ["7fab1a78081b"],
            "forbid_seal_ids_not_in_excerpts": True,
        },
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "source_truncated"


def test_g3_resolved_when_question_terminated():
    fixture = {
        "id": "S-growth-07b",
        "class": "G3",
        "excerpts": [
            {
                "id": "ex-7fab1a78081b",
                "text": "id 7fab1a78081b; question: Is step 0 wired?",
            }
        ],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == "7fab1a78081b: Is step 0 wired?"
    assert resolution["cites"] == ["7fab1a78081b"]
    score = score_resolution(fixture, resolution)
    assert score["correct"] is True


def test_g3_scorer_catches_stray_id_in_answer():
    # A resolved G3 answer that leaks an id not present in the excerpts
    # is a defect, not a pass, even though must_cite is satisfied.
    fixture = {
        "id": "S-growth-07c",
        "class": "G3",
        "excerpts": [
            {"id": "ex-7fab1a78081b", "text": "id 7fab1a78081b; question: Ok?"}
        ],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    tampered["answer"] = "7fab1a78081b: Ok? see also deadbeefcafe"
    score = score_resolution(fixture, tampered)
    assert score["correct"] is False


# ---------------------------------------------------------------------------
# G4 -- always flowering_required
# ---------------------------------------------------------------------------


def test_g4_always_flowering_required_with_builder_seat():
    # Mirrors S-growth-10.
    fixture = {
        "id": "S-growth-10",
        "class": "G4",
        "excerpts": [
            {
                "id": "ex-AF6B0EDB",
                "text": "dispatch_id AF6B0EDB; to_app hanuman; summary: Ship the broker side",
            }
        ],
        "expected": {
            "flowering_required": True,
            "local_only_pass": False,
            "builder_seat": "hanuman",
        },
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_FLOWERING_REQUIRED
    assert resolution["answer"] == {"builder_seat": "hanuman"}
    score = score_resolution(fixture, resolution)
    assert score["scored"] is True
    assert score["correct"] is True


def test_g4_scorer_catches_wrong_builder_seat():
    fixture = {
        "id": "S-growth-10b",
        "class": "G4",
        "excerpts": [{"id": "ex-x", "text": "to_app hanuman"}],
        "expected": {"builder_seat": "hanuman"},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    tampered["answer"] = {"builder_seat": "loki"}
    score = score_resolution(fixture, tampered)
    assert score["correct"] is False


# ---------------------------------------------------------------------------
# G5 -- verbatim
# ---------------------------------------------------------------------------


def test_g5_verbatim_answer_and_cite():
    # Mirrors S-growth-11.
    fixture = {
        "id": "S-growth-11",
        "class": "G5",
        "excerpts": [
            {
                "id": "ex-runtime-facts",
                "text": "Kart cannot reach loopback Ollama (allow_localhost retired; sandbox network_mode: isolated).",
            }
        ],
        "expected": {
            "answer": "no",
            "must_cite": ["ex-runtime-facts"],
        },
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_VERBATIM
    assert resolution["answer"] == fixture["excerpts"][0]["text"]
    assert resolution["cites"] == ["ex-runtime-facts"]
    score = score_resolution(fixture, resolution)
    assert score["correct"] is True


# ---------------------------------------------------------------------------
# Unknown class
# ---------------------------------------------------------------------------


def test_unknown_class_escalates():
    fixture = {"id": "S-growth-99", "class": "G9", "excerpts": []}
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "unknown_class"


# ---------------------------------------------------------------------------
# Scorer: escalate is never scored / never a failure
# ---------------------------------------------------------------------------


def test_escalate_is_never_scored():
    fixture = {"id": "x", "class": "G1", "excerpts": [], "expected": {}}
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False
    assert score["correct"] is None


# ---------------------------------------------------------------------------
# summarize()
# ---------------------------------------------------------------------------


def test_summarize_counts_by_status_and_correctness():
    rows = [
        {"status": "resolved", "scored": True, "correct": True},
        {"status": "resolved", "scored": True, "correct": False},
        {"status": "escalate", "scored": False, "correct": None},
        {"status": "verbatim", "scored": True, "correct": True},
    ]
    summary = summarize(rows)
    assert summary["n"] == 4
    assert summary["by_status"] == {"resolved": 2, "escalate": 1, "verbatim": 1}
    assert summary["resolved_total"] == 3
    assert summary["resolved_correct"] == 2


# ---------------------------------------------------------------------------
# resolve_fixtures_dir / run_resolve on the frozen fixture shapes
# ---------------------------------------------------------------------------


def _write_fixture(dir_path, name, payload):
    (dir_path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_fixtures_dir_reads_all_files_sorted(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-02.json",
        {
            "id": "S-growth-02",
            "class": "G1",
            "excerpts": [{"id": "ex-b", "text": "to_app hanuman"}],
            "expected": {"to_app": "hanuman", "must_cite": ["ex-b"]},
        },
    )
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G1",
            "excerpts": [{"id": "ex-a", "text": "to_app loki"}],
            "expected": {"to_app": "loki", "must_cite": ["ex-a"]},
        },
    )
    rows = resolve_fixtures_dir(fixtures)
    assert [r["fixture_id"] for r in rows] == ["S-growth-01", "S-growth-02"]
    for row in rows:
        assert row["fixture_sha256"]
        assert row["scored"] is True
        assert row["correct"] is True


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


def test_cli_resolve_end_to_end_exit_zero(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G1",
            "excerpts": [{"id": "ex-a", "text": "to_app loki"}],
            "expected": {"to_app": "loki", "must_cite": ["ex-a"]},
        },
    )
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "home"))
    out = tmp_path / "out.jsonl"
    rc = cli.main(["resolve", "--fixtures", str(fixtures), "--out", str(out)])
    assert rc == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["status"] == "resolved"
    assert row["correct"] is True


def test_cli_resolve_end_to_end_exit_one_on_wrong_resolved_answer(
    tmp_path, monkeypatch
):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    # to_app is correctly resolved as "loki" but expected demands "hanuman"
    # -- a resolved-but-wrong row must fail the run.
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G1",
            "excerpts": [{"id": "ex-a", "text": "to_app loki"}],
            "expected": {"to_app": "hanuman", "must_cite": ["ex-a"]},
        },
    )
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "home"))
    out = tmp_path / "out.jsonl"
    rc = cli.main(["resolve", "--fixtures", str(fixtures), "--out", str(out)])
    assert rc == 1


def test_run_resolve_writes_under_policy_runs_dir_when_no_out_given(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G1",
            "excerpts": [{"id": "ex-a", "text": "to_app loki"}],
            "expected": {"to_app": "loki", "must_cite": ["ex-a"]},
        },
    )
    policy = Policy(
        socket_path=tmp_path / "sock",
        ollama_base="http://127.0.0.1:0",
        runs_dir=tmp_path / "runs",
        default_model="unused",
        chain_tiers=("unused",),
    )
    result = run_resolve(policy, fixtures_dir=fixtures)
    out_path = result["out_path"]
    assert out_path.parent == policy.runs_dir
    assert out_path.name.startswith("resolve-")
    assert out_path.exists()
