"""The capability door: run once, run for session, run permanently, or no."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate  # noqa: E402

KEY = b"human-key"


def proof(cap, scope, key=KEY):
    return gate.sign(key, "seal", f"allow:{cap}:{scope}")


@pytest.mark.parametrize(
    "cmd,cap",
    [
        ("python3 -m pytest -q tests", "pytest"),
        ("uvx --offline ruff@0.15.0 check src", "lint"),
        ("git commit -m x", "git write"),
        ("git log --oneline -3", "git read"),
        ("pip install msgspec", "package install"),
        ("curl https://example.com", "network"),
        ("node build.js", "node"),
        ("python3 merge_flows.py out a=b", "python"),
        ("systemctl --user status x", "systemd"),
        ("rm -rf build", "delete"),
        ("ls -la", "shell"),
        ("GITHUB_TOKEN=ghp_x gh pr list", "network"),
    ],
)
def test_commands_are_classed_by_what_they_need(cmd, cap):
    assert gate.capability(cmd) == cap


def test_no_grant_fails_closed_with_a_four_way_card():
    d = gate.allow("python3 x.py", "s1", [])
    assert d.verdict == "awaiting_grant"
    assert d.card["options"] == ["run once", "run for session", "run permanently", "no"]
    assert "offer" not in d.card  # first ask: no ladder offer yet


def test_once_is_consumed_by_one_act():
    grants = [gate.grant("python", "once", "s1", proof("python", "once"), KEY, "g1")]
    assert gate.allow("python3 x.py", "s1", grants).verdict == "pass"
    assert gate.allow("python3 y.py", "s1", grants).verdict == "awaiting_grant"


def test_session_covers_this_session_only():
    grants = [
        gate.grant("pytest", "session", "s1", proof("pytest", "session"), KEY, "g2")
    ]
    assert gate.allow("pytest -q", "s1", grants).verdict == "pass"
    assert gate.allow("pytest -q", "s1", grants).verdict == "pass"
    assert gate.allow("pytest -q", "s2", grants).verdict == "awaiting_grant"


def test_permanent_covers_every_session_until_revoked():
    grants = [
        gate.grant("lint", "permanent", "s1", proof("lint", "permanent"), KEY, "g3")
    ]
    assert gate.allow("ruff check .", "s9", grants).verdict == "pass"
    grants[0]["revoked"] = True
    assert gate.allow("ruff check .", "s9", grants).verdict == "awaiting_grant"


def test_a_grant_covers_its_capability_only():
    grants = [
        gate.grant(
            "git read", "permanent", "s1", proof("git read", "permanent"), KEY, "g4"
        )
    ]
    assert gate.allow("git log", "s1", grants).verdict == "pass"
    assert gate.allow("git push", "s1", grants).verdict == "awaiting_grant"


def test_only_the_humans_key_answers_a_card():
    with pytest.raises(gate.Refused):
        gate.grant(
            "python",
            "permanent",
            "s1",
            proof("python", "permanent", b"model"),
            KEY,
            "g5",
        )
    with pytest.raises(
        gate.Refused
    ):  # a proof for "once" can't be spent as "permanent"
        gate.grant("python", "permanent", "s1", proof("python", "once"), KEY, "g6")
    with pytest.raises(gate.Refused):
        gate.grant("python", "forever", "s1", proof("python", "forever"), KEY, "g7")


@pytest.mark.parametrize(
    "times,rung,offer",
    [
        (2, 0, None),
        (3, 3, "run for session?"),
        (13, 13, "run permanently?"),
        (23, 23, "run permanently?"),
    ],
)
def test_the_ladder_offers_and_never_applies(times, rung, offer):
    d = gate.allow("pytest -q", "s1", [], seen={"pytest": times - 1})
    assert d.verdict == "awaiting_grant"  # still asks: the offer is not a grant
    assert d.card["level"] == rung
    assert (
        (d.card.get("offer") or "").startswith(offer)
        if offer
        else "offer" not in d.card
    )


def test_egress_capabilities_say_the_bytes_go_on_their_own_card():
    assert "egress" in gate.allow("curl https://x", "s1", []).card
    assert "egress" not in gate.allow("ls", "s1", []).card


def test_a_door_error_fails_closed():
    d = gate.allow(
        "python3 x.py", "s1", [{"capability": "python"}]
    )  # malformed grant row
    assert d.verdict == "refused" and "failing closed" in d.reason
