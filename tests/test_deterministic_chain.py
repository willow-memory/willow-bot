"""The deterministic chain (D0, local tier, flowering) -- no network.

The local tier is a fake ``chat_fn`` except in the one test that checks
the schema reaches Ollama's wire payload, which uses a loopback mock.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

from willow_bot.deterministic import chain as chain_mod
from willow_bot.deterministic.chain import (
    LOCAL_SCHEMA,
    TIER_D0,
    TIER_FLOWERING,
    TIER_LOCAL,
    chain_act,
    d0_wrong,
    local_prompt,
    parse_local_reply,
    run_chain,
    summarize_chain,
)
from willow_bot.deterministic.ollama import chat
from willow_bot.deterministic.policy import Policy
from willow_bot.deterministic.resolvers import (
    STATUS_ESCALATE,
    STATUS_ROUTED,
    is_route_act,
    resolve_fixture,
    score_resolution,
)

ROUTE_BRIEF = (
    "Route this item to one fleet seat: hanuman (builds code), loki (audits "
    "code changes), ada (audits box posture). Answer with one app id, or "
    "ESCALATE if no fleet seat can act on it. Cite the excerpt id."
)


def _policy(tmp_path, base="http://127.0.0.1:9"):
    return Policy(
        socket_path=tmp_path / "sock",
        ollama_base=base,
        runs_dir=tmp_path / "runs",
        default_model="test-model",
        chain_tiers=("test-model",),
    )


def _fake_chat(reply="", err=None, calls=None):
    def fn(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return reply, 12, err

    return fn


def _route_fixture(fid="S-growth-16", expected_to_app="ESCALATE"):
    return {
        "id": fid,
        "class": "G1",
        "excluded_from_T": False,
        "generative": True,
        "brief": ROUTE_BRIEF,
        "excerpts": [
            {"id": "ex-73726e98", "text": "id 73726e98; title: Loki audits blocked"}
        ],
        "expected": {"to_app": expected_to_app, "must_cite": ["ex-73726e98"]},
    }


def _title_fixture():
    # S-growth-06 shape: the source title is over 120 chars, so D0 escalates.
    long_title = "CI stuck: willow-memory/willow-mcp#642 — 2 leg(s) cancelled, " + "x" * 80
    return {
        "id": "S-growth-06",
        "class": "G2",
        "excluded_from_T": False,
        "brief": "Write a desk-attention title. At most 120 characters.",
        "excerpts": [{"id": "ex-d77d0c46", "text": f"id d77d0c46; title: {long_title}"}],
        "expected": {"max_chars": 120, "must_name": ["willow-mcp", "cancelled"]},
    }


# ---------------------------------------------------------------------------
# D0 routing rule (gap 82897b2f507e)
# ---------------------------------------------------------------------------


def test_route_brief_is_a_route_act_and_field_read_is_not():
    assert is_route_act(_route_fixture())
    assert not is_route_act({"brief": "Name the to_app this dispatch is addressed to."})
    assert not is_route_act({"brief": None})
    assert not is_route_act("not a dict")


def test_route_act_closes_in_code_to_willow_citing_the_pool():
    res = resolve_fixture(_route_fixture())
    assert res["status"] == STATUS_ROUTED
    assert res["answer"] == "willow"
    assert res["cites"] == ["ex-73726e98"]
    assert res["reason"] == "routing_goes_through_willow"


def test_route_act_ignores_a_to_app_field_in_the_pool():
    fx = _route_fixture()
    fx["excerpts"][0]["text"] = "dispatch_id 9756A4F7; to_app hanuman"
    assert resolve_fixture(fx)["answer"] == "willow"


def test_route_act_with_no_excerpt_escalates():
    fx = _route_fixture()
    fx["excerpts"] = []
    assert resolve_fixture(fx)["status"] == STATUS_ESCALATE


def test_field_read_with_no_to_app_still_escalates():
    fx = {
        "id": "S-growth-03",
        "class": "G1",
        "brief": "Name the to_app this dispatch is addressed to.",
        "excerpts": [{"id": "ex-1", "text": "summary: Ada gather, Wave 0a."}],
        "expected": {"to_app": "ada", "must_cite": ["ex-1"]},
    }
    res = resolve_fixture(fx)
    assert res["status"] == STATUS_ESCALATE
    assert res["reason"] == "no_to_app_field"


def test_routed_row_is_scored_against_the_ruling_not_the_frozen_gold():
    # S-growth-13's frozen gold is hanuman; the operator's sheet scored
    # escalation to willow as a match, and that is the score of record.
    fx = _route_fixture("S-growth-13", expected_to_app="hanuman")
    score = score_resolution(fx, resolve_fixture(fx))
    assert score == {"scored": True, "correct": True, "detail": "g1_route_through_willow"}


def test_routed_row_missing_a_must_cite_is_wrong():
    fx = _route_fixture()
    fx["expected"]["must_cite"] = ["ex-73726e98", "ex-absent"]
    assert score_resolution(fx, resolve_fixture(fx))["correct"] is False


# ---------------------------------------------------------------------------
# Local reply contract (gap 81451b988833)
# ---------------------------------------------------------------------------


def test_prompt_offers_escalate_and_carries_only_brief_and_excerpts():
    text = local_prompt(_title_fixture())
    assert "ESCALATE" in text
    assert "[ex-d77d0c46]" in text
    assert "Respond in plain text" not in text


def test_schema_offers_escalate_as_a_status():
    assert "ESCALATE" in LOCAL_SCHEMA["properties"]["status"]["enum"]
    assert set(LOCAL_SCHEMA["required"]) == {"status", "answer", "cites"}


def test_parse_accepts_a_contracted_answer():
    out = parse_local_reply(
        json.dumps({"status": "answer", "answer": " a title ", "cites": ["ex-1"]}),
        ["ex-1"],
    )
    assert out == {"outcome": "answer", "answer": "a title", "cites": ["ex-1"], "reason": None}


def test_parse_escalate():
    out = parse_local_reply(
        json.dumps({"status": "ESCALATE", "answer": "", "cites": []}), ["ex-1"]
    )
    assert out["outcome"] == "escalate"
    assert out["reason"] == "local_escalate"


def test_parse_refuses_prose_and_off_schema_objects():
    for reply in (
        "willow",
        "[]",
        json.dumps({"status": "maybe", "answer": "x", "cites": []}),
        json.dumps({"status": "answer", "answer": 3, "cites": []}),
        json.dumps({"status": "answer", "answer": "x", "cites": "ex-1"}),
        json.dumps({"status": "answer", "answer": "x", "cites": [1]}),
        json.dumps({"status": "answer", "answer": "   ", "cites": ["ex-1"]}),
    ):
        out = parse_local_reply(reply, ["ex-1"])
        assert out["outcome"] == "refused", reply
        assert out["reason"] == "schema_fail", reply


def test_parse_refuses_a_cite_outside_the_pool():
    out = parse_local_reply(
        json.dumps({"status": "answer", "answer": "x", "cites": ["ex-1", "71b0ce7b"]}),
        ["ex-1"],
    )
    assert out["outcome"] == "refused"
    assert out["reason"] == "link_fail"


# ---------------------------------------------------------------------------
# One act through the chain
# ---------------------------------------------------------------------------


def test_d0_closed_act_never_calls_the_local_tier(tmp_path):
    calls: list = []
    row = chain_act(
        _route_fixture(),
        policy=_policy(tmp_path),
        model="m",
        chat_fn=_fake_chat(calls=calls),
    )
    assert calls == []
    assert row["tier"] == TIER_D0
    assert row["status"] == STATUS_ROUTED
    assert row["correct"] is True


def test_escalated_act_reaches_the_local_tier_with_the_schema(tmp_path):
    calls: list = []
    reply = json.dumps(
        {
            "status": "answer",
            "answer": "CI stuck: willow-memory/willow-mcp#642, 2 legs cancelled",
            "cites": ["ex-d77d0c46"],
        }
    )
    row = chain_act(
        _title_fixture(),
        policy=_policy(tmp_path),
        model="willow-lane4-3b",
        chat_fn=_fake_chat(reply, calls=calls),
    )
    assert len(calls) == 1
    assert calls[0]["fmt"] is LOCAL_SCHEMA
    assert calls[0]["model"] == "willow-lane4-3b"
    assert row["d0_status"] == STATUS_ESCALATE
    assert row["d0_reason"] == "title_over_limit"
    assert row["tier"] == TIER_LOCAL
    assert row["correct"] is True


def test_wrong_local_answer_is_scored_wrong_not_hidden(tmp_path):
    reply = json.dumps({"status": "answer", "answer": "CI broke", "cites": ["ex-d77d0c46"]})
    row = chain_act(
        _title_fixture(), policy=_policy(tmp_path), model="m", chat_fn=_fake_chat(reply)
    )
    assert row["tier"] == TIER_LOCAL
    assert row["scored"] is True
    assert row["correct"] is False


def test_local_escalate_becomes_a_flowering_row_for_willow(tmp_path):
    reply = json.dumps({"status": "ESCALATE", "answer": "", "cites": []})
    row = chain_act(
        _title_fixture(), policy=_policy(tmp_path), model="m", chat_fn=_fake_chat(reply)
    )
    assert row["tier"] == TIER_FLOWERING
    assert row["route_to"] == "willow"
    assert row["reason"] == "local_escalate"
    assert row["scored"] is False


def test_off_schema_reply_becomes_flowering_not_an_answer(tmp_path):
    row = chain_act(
        _title_fixture(),
        policy=_policy(tmp_path),
        model="m",
        chat_fn=_fake_chat("Here is a title: CI stuck"),
    )
    assert row["tier"] == TIER_FLOWERING
    assert row["reason"] == "schema_fail"
    assert row["raw_reply"] == "Here is a title: CI stuck"


def test_unreachable_local_tier_is_not_an_escalation(tmp_path):
    row = chain_act(
        _title_fixture(),
        policy=_policy(tmp_path),
        model="m",
        chat_fn=_fake_chat("", err="timed out"),
    )
    assert row["tier"] == TIER_FLOWERING
    assert row["reason"] == "local_unreachable"
    assert row["local_error"] == "timed out"


def test_malformed_fixture_goes_to_flowering_without_a_call(tmp_path):
    calls: list = []
    row = chain_act(
        "not a fixture", policy=_policy(tmp_path), model="m", chat_fn=_fake_chat(calls=calls)
    )
    assert calls == []
    assert row["tier"] == TIER_FLOWERING
    assert row["reason"] == "malformed_fixture"


# ---------------------------------------------------------------------------
# Runs and summary
# ---------------------------------------------------------------------------


def _write(dirpath, fixture):
    (dirpath / f"{fixture['id']}.json").write_text(json.dumps(fixture), encoding="utf-8")


def test_run_chain_writes_one_row_per_act_and_counts_by_tier(tmp_path):
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    _write(
        fixtures,
        {
            "id": "S-growth-01",
            "class": "G1",
            "excluded_from_T": False,
            "brief": "Name the to_app this dispatch is addressed to.",
            "excerpts": [{"id": "ex-615C332F", "text": "dispatch_id 615C332F; to_app loki"}],
            "expected": {"to_app": "loki", "must_cite": ["ex-615C332F"]},
        },
    )
    _write(fixtures, _title_fixture())
    _write(
        fixtures,
        {
            "id": "S-growth-10",
            "class": "G4",
            "excluded_from_T": True,
            "brief": "Implement it.",
            "excerpts": [{"id": "ex-g4", "text": "to_app hanuman"}],
            "expected": {"builder_seat": "hanuman"},
        },
    )
    _write(fixtures, _route_fixture())
    (fixtures / "S-growth-99.json").write_text("{not json", encoding="utf-8")

    reply = json.dumps({"status": "ESCALATE", "answer": "", "cites": []})
    out = tmp_path / "runs" / "chain.jsonl"
    result = run_chain(
        _policy(tmp_path),
        fixtures_dir=fixtures,
        model="m",
        out_path=out,
        chat_fn=_fake_chat(reply),
    )
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    s = result["summary"]
    assert s["n"] == 5
    assert s["counted"] == 4  # G4 excluded
    assert s["closed_code"] == 2  # 01 resolved, 16 routed
    assert s["closed_local"] == 0
    assert s["flowering"] == 2  # 06 local_escalate, 99 unreadable
    assert s["grown_share"] == 0.5
    assert s["cloud_per_act_max"] == 0.5
    assert s["flowering_reasons"]["local_escalate"] == 1
    assert s["precision"][TIER_D0] == {"total": 2, "correct": 2}
    assert not d0_wrong(result["rows"])


def test_summary_of_nothing_is_empty_not_zero():
    s = summarize_chain([])
    assert s["counted"] == 0
    assert s["grown_share"] is None
    assert s["cloud_per_act_max"] is None


def test_d0_wrong_flags_only_code_tier_defects():
    assert d0_wrong([{"tier": TIER_D0, "scored": True, "correct": False}])
    assert not d0_wrong([{"tier": TIER_LOCAL, "scored": True, "correct": False}])
    assert not d0_wrong([{"tier": TIER_D0, "scored": False, "correct": None}])


def test_default_chat_fn_is_the_ollama_client():
    assert chain_mod.chain_act.__kwdefaults__["chat_fn"] is chat


# ---------------------------------------------------------------------------
# The schema reaches Ollama's wire payload
# ---------------------------------------------------------------------------


class _CaptureHandler(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []

    def log_message(self, *_args):
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).seen.append(body)
        out = {"message": {"role": "assistant", "content": "{}"}}
        payload = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_chat_sends_format_only_when_given():
    _CaptureHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        chat(base_url=base, model="m", prompt="p", timeout_s=5, fmt=LOCAL_SCHEMA)
        chat(base_url=base, model="m", prompt="p", timeout_s=5)
    finally:
        server.shutdown()
    assert _CaptureHandler.seen[0]["format"] == LOCAL_SCHEMA
    assert "format" not in _CaptureHandler.seen[1]
