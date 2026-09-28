"""Tests for the code-first resolver tier (D0) -- stdlib only, no network.

Includes the rework from 753B6124 (Loki audit 174F4F2A on D288B449
12704eb): a closed excerpt grammar, conflict/suspect-truncation
detection, per-status scored counts, and a resolver that never aborts
the whole run on one malformed fixture.
"""
from __future__ import annotations

import json

from willow_bot.deterministic import cli
from willow_bot.deterministic import resolvers
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
# Parser edge cases -- closed grammar
# ---------------------------------------------------------------------------


def test_parse_excerpt_fields_token_key_space_separated():
    fields = parse_excerpt_fields("dispatch_id 615C332F; to_app loki")
    assert fields == {"dispatch_id": "615C332F", "to_app": "loki"}


def test_parse_excerpt_fields_text_key_colon_value_contains_colon():
    fields = parse_excerpt_fields(
        "title: CI red: willow-memory/willow-mcp#645 — 1 leg(s): dependabot-pr"
    )
    assert fields["title"] == (
        "CI red: willow-memory/willow-mcp#645 — 1 leg(s): dependabot-pr"
    )


def test_parse_excerpt_fields_ignores_unparseable_segment():
    fields = parse_excerpt_fields("standalone; to_app hanuman")
    assert "standalone" not in fields
    assert fields["to_app"] == "hanuman"


def test_parse_excerpt_fields_first_writer_wins_on_duplicate_key_same_value():
    fields = parse_excerpt_fields("id 7fab1a78081b; id 7fab1a78081b")
    assert fields["id"] == "7fab1a78081b"


def test_parse_excerpt_fields_empty_text():
    assert parse_excerpt_fields("") == {}
    assert parse_excerpt_fields(None) == {}


def test_parse_rejects_prose_that_merely_contains_a_token_key_word():
    # "to_app is unclear here" has the key word but a multi-word value --
    # not a real token-key segment, must not become a field.
    fields = parse_excerpt_fields("to_app is unclear here")
    assert "to_app" not in fields


def test_parse_rejects_prose_that_merely_contains_a_text_key_word():
    # "title" with no colon directly after it is prose, not a field.
    fields = parse_excerpt_fields("title of the PR was wrong")
    assert "title" not in fields


def test_parse_rejects_token_key_used_with_colon_convention():
    # to_app is a token key -- a colon-form attempt does not match either
    # shape and must be ignored, not guessed at.
    fields = parse_excerpt_fields("to_app: loki")
    assert "to_app" not in fields


# ---------------------------------------------------------------------------
# Conflicting fields -- never pick between disagreeing excerpts
# ---------------------------------------------------------------------------


def test_g1_escalates_on_conflicting_to_app_across_excerpts():
    fixture = {
        "id": "S-conflict",
        "class": "G1",
        "excerpts": [
            {"id": "ex-a", "text": "to_app loki"},
            {"id": "ex-b", "text": "to_app hanuman"},
        ],
        "expected": {"to_app": "loki", "must_cite": ["ex-a"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "conflicting_field"
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False


def test_g1_escalates_on_conflicting_to_app_within_one_excerpt():
    fixture = {
        "id": "S-conflict-2",
        "class": "G1",
        "excerpts": [{"id": "ex-a", "text": "to_app loki; to_app hanuman"}],
        "expected": {"to_app": "loki"},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "conflicting_field"


# ---------------------------------------------------------------------------
# G1 -- to_app
# ---------------------------------------------------------------------------


def test_g1_resolved_when_to_app_present():
    fixture = {
        "id": "S-growth-01",
        "class": "G1",
        "excerpts": [
            {
                "id": "ex-615C332F",
                "text": "dispatch_id 615C332F; to_app loki; summary: Audit delta",
            }
        ],
        "expected": {"to_app": "loki", "must_cite": ["ex-615C332F"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == "loki"
    assert resolution["cites"] == ["ex-615C332F"]
    score = score_resolution(fixture, resolution)
    assert score["scored"] is True
    assert score["correct"] is True


def test_g1_escalates_on_prose_only_no_to_app_field():
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


def test_g2_escalates_when_embedded_semicolon_truncates_title():
    # The real title legitimately contains ";" -- our top-level split on
    # ";" cuts it, and the remainder ("dependabot-pr leg failed") does
    # not parse as a fresh key. Must escalate, not silently score the
    # truncated fragment as correct.
    fixture = {
        "id": "S-semicolon-title",
        "class": "G2",
        "excerpts": [
            {
                "id": "ex-a",
                "text": "id abc12345; title: CI red: willow-mcp; dependabot-pr leg failed",
            }
        ],
        "expected": {"max_chars": 120, "must_name": ["willow-mcp"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "value_split_by_semicolon"


def test_g2_escalates_on_conflicting_title_across_excerpts():
    # Untested check (Loki 7EA73431 F2/F7): the G2 conflict check at
    # _resolve_g2 -- two excerpts disagreeing on title must escalate,
    # never pick one.
    fixture = {
        "id": "S-g2-conflict",
        "class": "G2",
        "excerpts": [
            {"id": "ex-a", "text": "title: CI red variant A"},
            {"id": "ex-b", "text": "title: CI red variant B"},
        ],
        "expected": {"max_chars": 120, "must_name": []},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "conflicting_field"


def test_g2_max_chars_fallback_when_expected_value_is_not_numeric():
    # Untested check (Loki 7EA73431 F7): the max_chars except
    # (TypeError, ValueError) fallback to 120 in _resolve_g2.
    fixture = {
        "id": "S-g2-bad-max-chars",
        "class": "G2",
        "excerpts": [{"id": "ex-a", "text": "title: Short title"}],
        "expected": {"max_chars": "not-a-number", "must_name": []},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == "Short title"


def test_g2_scorer_prefers_exact_reference_title_match():
    fixture = {
        "id": "S-g2-exact",
        "class": "G2",
        "excerpts": [{"id": "ex-a", "text": "id abc12345; title: Totally unrelated"}],
        "expected": {
            "max_chars": 120,
            "must_name": [],
            "reference_title": "CI red: willow-mcp#1",
        },
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    score = score_resolution(fixture, resolution)
    assert score["correct"] is False


# ---------------------------------------------------------------------------
# G3 -- gap id + question
# ---------------------------------------------------------------------------


def test_g3_escalates_on_truncated_question_no_terminal_punctuation():
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
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "source_truncated"


def test_g3_escalates_on_ascii_ellipsis():
    fixture = {
        "id": "S-g3-ellipsis-ascii",
        "class": "G3",
        "excerpts": [{"id": "ex-a", "text": "id abc12345; question: Step 0 wiring ..."}],
        "expected": {"must_cite": ["abc12345"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "source_truncated"


def test_g3_escalates_on_unicode_ellipsis():
    fixture = {
        "id": "S-g3-ellipsis-unicode",
        "class": "G3",
        "excerpts": [{"id": "ex-a", "text": "id abc12345; question: Step 0 wiring…"}],
        "expected": {"must_cite": ["abc12345"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "source_truncated"


def test_g3_escalates_on_mid_word_cut():
    fixture = {
        "id": "S-g3-midword",
        "class": "G3",
        "excerpts": [{"id": "ex-a", "text": "id abc12345; question: the stop is se"}],
        "expected": {"must_cite": ["abc12345"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "source_truncated"


def test_g3_escalates_on_conflicting_id_across_excerpts():
    # Untested check (Loki 7EA73431 F2/F7): the G3 conflict check at
    # _resolve_g3 -- two excerpts disagreeing on id must escalate.
    fixture = {
        "id": "S-g3-conflict",
        "class": "G3",
        "excerpts": [
            {"id": "ex-a", "text": "id abc12345; question: Ok?"},
            {"id": "ex-b", "text": "id def67890; question: Ok?"},
        ],
        "expected": {"must_cite": ["abc12345"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "conflicting_field"


def test_g3_escalates_when_embedded_semicolon_truncates_question():
    # Untested check (Loki 7EA73431 F7): the G3 ";" split -- prose after
    # a ";" that does not parse as a fresh key marks "question" suspect
    # and must escalate, not silently resolve the truncated fragment.
    fixture = {
        "id": "S-g3-semicolon",
        "class": "G3",
        "excerpts": [
            {
                "id": "ex-a",
                "text": "id abc12345; question: Is this ok; not a key at all",
            }
        ],
        "expected": {"must_cite": ["abc12345"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "value_split_by_semicolon"


def test_g3_resolved_when_question_terminated():
    fixture = {
        "id": "S-growth-07b",
        "class": "G3",
        "excerpts": [
            {"id": "ex-7fab1a78081b", "text": "id 7fab1a78081b; question: Is step 0 wired?"}
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
    fixture = {
        "id": "S-growth-07c",
        "class": "G3",
        "excerpts": [{"id": "ex-7fab1a78081b", "text": "id 7fab1a78081b; question: Ok?"}],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    tampered["answer"] = "7fab1a78081b: Ok? see also deadbeefcafe"
    score = score_resolution(fixture, tampered)
    assert score["correct"] is False


def test_g3_stray_id_check_ignores_dates_and_decimals():
    fixture = {
        "id": "S-growth-07d",
        "class": "G3",
        "excerpts": [{"id": "ex-7fab1a78081b", "text": "id 7fab1a78081b; question: Ok?"}],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    # 8-digit date and a long decimal fragment -- neither has a hex letter.
    tampered["answer"] = "7fab1a78081b: Ok? filed 20260925 at 3.14159265"
    score = score_resolution(fixture, tampered)
    assert score["correct"] is True


def test_g3_stray_id_check_is_case_insensitive():
    fixture = {
        "id": "S-growth-07e",
        "class": "G3",
        "excerpts": [{"id": "ex-a", "text": "id 7fab1a78081b; question: Ok?"}],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    tampered["answer"] = "7FAB1A78081B: Ok?"  # same id, uppercased
    score = score_resolution(fixture, tampered)
    assert score["correct"] is True


def test_g3_stray_id_check_catches_longer_ids():
    fixture = {
        "id": "S-growth-07f",
        "class": "G3",
        "excerpts": [{"id": "ex-a", "text": "id 7fab1a78081b; question: Ok?"}],
        "expected": {"must_cite": ["7fab1a78081b"]},
    }
    resolution = resolve_fixture(fixture)
    tampered = dict(resolution)
    tampered["answer"] = "7fab1a78081b: Ok? see gap_deadbeefcafefeed"  # 16-hex, underscore-joined
    score = score_resolution(fixture, tampered)
    assert score["correct"] is False


# ---------------------------------------------------------------------------
# G4 -- always flowering_required
# ---------------------------------------------------------------------------


def test_g4_always_flowering_required_with_builder_seat():
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


def test_g4_builder_seat_extracted_dynamically_not_hardcoded():
    # A G4 fixture whose to_app is NOT hanuman -- catches a resolver that
    # hardcodes "hanuman" instead of reading the field.
    fixture = {
        "id": "S-g4-loki",
        "class": "G4",
        "excerpts": [{"id": "ex-x", "text": "dispatch_id ABCDEF01; to_app loki"}],
        "expected": {"builder_seat": "loki"},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["answer"] == {"builder_seat": "loki"}
    score = score_resolution(fixture, resolution)
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


def test_g4_conflicting_to_app_is_a_correct_refusal_and_unscored():
    # Loki 7EA73431 F1: a G4 row with conflicting to_app is a correct
    # refusal (there is nothing to check against expected.builder_seat),
    # so it must be left unscored -- never counted wrong.
    fixture = {
        "id": "S-g4-conflict",
        "class": "G4",
        "excerpts": [
            {"id": "ex-a", "text": "to_app hanuman"},
            {"id": "ex-b", "text": "to_app loki"},
        ],
        "expected": {"builder_seat": "hanuman"},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_FLOWERING_REQUIRED
    assert resolution["answer"] == {}
    assert resolution["reason"] == "conflicting_field"
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False
    assert score["correct"] is None


def test_g4_missing_to_app_field_is_a_correct_refusal_and_unscored():
    # Same as above but for the "no to_app field at all" path (reason is
    # None, not "conflicting_field") -- also a correct refusal.
    fixture = {
        "id": "S-g4-missing",
        "class": "G4",
        "excerpts": [{"id": "ex-a", "text": "summary: nothing about a seat here"}],
        "expected": {"builder_seat": "hanuman"},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_FLOWERING_REQUIRED
    assert resolution["answer"] == {}
    assert resolution["reason"] is None
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False
    assert score["correct"] is None


def test_cli_resolve_exit_zero_on_g4_conflicting_to_app(tmp_path, monkeypatch):
    # The bug this fixes: _cmd_resolve used to exit 1 on a G4 row whose
    # to_app conflicted across excerpts, even though refusing is correct.
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G4",
            "excerpts": [
                {"id": "ex-a", "text": "to_app hanuman"},
                {"id": "ex-b", "text": "to_app loki"},
            ],
            "expected": {"builder_seat": "hanuman"},
        },
    )
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "home"))
    out = tmp_path / "out.jsonl"
    rc = cli.main(["resolve", "--fixtures", str(fixtures), "--out", str(out)])
    assert rc == 0


# ---------------------------------------------------------------------------
# G5 -- verbatim
# ---------------------------------------------------------------------------


def test_g5_verbatim_answer_and_cite():
    fixture = {
        "id": "S-growth-11",
        "class": "G5",
        "excerpts": [
            {
                "id": "ex-runtime-facts",
                "text": "Kart cannot reach loopback Ollama (allow_localhost retired; sandbox network_mode: isolated).",
            }
        ],
        "expected": {"answer": "no", "must_cite": ["ex-runtime-facts"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_VERBATIM
    assert resolution["answer"] == fixture["excerpts"][0]["text"]
    assert resolution["cites"] == ["ex-runtime-facts"]
    score = score_resolution(fixture, resolution)
    assert score["correct"] is True


def test_g5_scorer_fails_when_cite_does_not_match_must_cite():
    fixture = {
        "id": "S-g5-wrong-cite",
        "class": "G5",
        "excerpts": [{"id": "ex-actual", "text": "Some sealed fact."}],
        "expected": {"answer": "no", "must_cite": ["ex-other"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_VERBATIM
    score = score_resolution(fixture, resolution)
    assert score["scored"] is True
    assert score["correct"] is False


# ---------------------------------------------------------------------------
# Unknown class / malformed fixture
# ---------------------------------------------------------------------------


def test_unknown_class_escalates():
    fixture = {"id": "S-growth-99", "class": "G9", "excerpts": []}
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "unknown_class"


def test_malformed_fixture_not_a_dict_escalates_without_raising():
    resolution = resolve_fixture(["not", "a", "dict"])  # type: ignore[arg-type]
    assert resolution["status"] == STATUS_ESCALATE
    assert resolution["reason"] == "malformed_fixture"


def test_parse_excerpts_skips_non_string_excerpt_text_without_raising():
    # Untested check (Loki 7EA73431 F7/F8): the non-string guard in
    # _parse_excerpts (``if not isinstance(raw_text, str): continue``).
    # Without it, ``raw_text.split(";")`` on a non-string blows up.
    fixture = {
        "id": "S-g1-nonstring-text",
        "class": "G1",
        "excerpts": [
            {"id": "ex-a", "text": 12345},
            {"id": "ex-b", "text": "to_app loki"},
        ],
        "expected": {"to_app": "loki", "must_cite": ["ex-b"]},
    }
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_RESOLVED
    assert resolution["answer"] == "loki"


def test_resolve_fixtures_dir_catches_unexpected_resolver_exception(
    tmp_path, monkeypatch
):
    # Untested check (Loki 7EA73431 F7/F8): the resolver_error catch-all
    # in resolve_fixtures_dir (``except Exception as exc:``). It must
    # turn an exception any single fixture's resolution raises into one
    # escalate row, never abort the run.
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {"id": "S-growth-01", "class": "G1", "excerpts": [], "expected": {}},
    )

    def _boom(fixture):
        raise RuntimeError("boom")

    monkeypatch.setattr(resolvers, "resolve_fixture", _boom)
    rows = resolvers.resolve_fixtures_dir(fixtures)
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_ESCALATE
    assert rows[0]["reason"].startswith("resolver_error")
    assert rows[0]["scored"] is False
    assert rows[0]["correct"] is None


def test_one_malformed_fixture_escalates_its_row_and_does_not_abort_the_run(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    # A top-level JSON list, not an object -- used to raise inside the
    # resolver instead of producing one escalate row.
    (fixtures / "S-growth-01.json").write_text(json.dumps(["oops"]), encoding="utf-8")
    (fixtures / "S-growth-02.json").write_text(
        json.dumps(
            {
                "id": "S-growth-02",
                "class": "G1",
                "excerpts": [{"id": "ex-b", "text": "to_app hanuman"}],
                "expected": {"to_app": "hanuman", "must_cite": ["ex-b"]},
            }
        ),
        encoding="utf-8",
    )
    rows = resolve_fixtures_dir(fixtures)
    assert len(rows) == 2
    bad = next(r for r in rows if r["fixture_id"] != "S-growth-02")
    assert bad["status"] == STATUS_ESCALATE
    good = next(r for r in rows if r["fixture_id"] == "S-growth-02")
    assert good["status"] == STATUS_RESOLVED
    assert good["correct"] is True


# ---------------------------------------------------------------------------
# Escalate is never scored
# ---------------------------------------------------------------------------


def test_escalate_is_never_scored():
    fixture = {"id": "x", "class": "G1", "excerpts": [], "expected": {}}
    resolution = resolve_fixture(fixture)
    assert resolution["status"] == STATUS_ESCALATE
    score = score_resolution(fixture, resolution)
    assert score["scored"] is False
    assert score["correct"] is None


# ---------------------------------------------------------------------------
# summarize() -- per-status naming (Loki F1)
# ---------------------------------------------------------------------------


def test_summarize_resolved_total_counts_only_resolved_rows():
    rows = [
        {"status": "resolved", "scored": True, "correct": True},
        {"status": "resolved", "scored": True, "correct": False},
        {"status": "escalate", "scored": False, "correct": None},
        {"status": "verbatim", "scored": True, "correct": True},
        {"status": "flowering_required", "scored": True, "correct": False},
    ]
    summary = summarize(rows)
    assert summary["n"] == 5
    assert summary["by_status"] == {
        "resolved": 2,
        "escalate": 1,
        "verbatim": 1,
        "flowering_required": 1,
    }
    # The number named "resolved" must not include the verbatim row.
    assert summary["resolved_total"] == 2
    assert summary["resolved_correct"] == 1
    assert summary["verbatim_total"] == 1
    assert summary["verbatim_correct"] == 1
    assert summary["flowering_required_total"] == 1
    assert summary["flowering_required_correct"] == 0


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


def test_cli_resolve_end_to_end_exit_one_on_wrong_resolved_answer(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
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


def test_cli_resolve_exit_one_on_wrong_verbatim_answer(tmp_path, monkeypatch):
    # A verbatim (G5) row that fails its must_cite check must also fail
    # the run, not just a "resolved" row -- every scored check can fail.
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "S-growth-01.json",
        {
            "id": "S-growth-01",
            "class": "G5",
            "excerpts": [{"id": "ex-actual", "text": "Some sealed fact."}],
            "expected": {"answer": "no", "must_cite": ["ex-other"]},
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
