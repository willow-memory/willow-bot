"""The troll: every line carries a real fact, the situation picks the pool,
and nothing repeats in a repo until its pool is spent.

The pools are the real ones in willow-bot.json; the memory lives in the
sandboxed box (tests/conftest.py). Nothing here reaches GitHub.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone

import pytest

import quips
import router
import sigh
import troll

REPO = "willow-memory/willow-bot"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)  # a Tuesday
def _draw(sha: str) -> int:
    return int(__import__("hashlib").sha256(sha.encode()).hexdigest()[:8], 16) % troll.GUEST_DRAW


NO_GUEST_SHA = next(f"{i:040x}" for i in range(1000) if _draw(f"{i:040x}") > 1)
FRANK_SHA = next(f"{i:040x}" for i in range(1000) if _draw(f"{i:040x}") == 0)


def _pr(**over):
    pr = {"number": 7, "title": "feat: a thing", "draft": False, "additions": 40, "deletions": 10,
          "changed_files": 3, "created_at": "2026-09-27T12:00:00Z", "merged_at": None, "closed_at": None,
          "user": {"login": "someone", "type": "User"}, "head": {"ref": "feat/x", "sha": NO_GUEST_SHA},
          "author_association": "OWNER"}
    pr.update(over)
    return pr


# ── facts and situations ────────────────────────────────────────────────────

def test_pr_facts_from_the_payload():
    f = troll.pr_facts(_pr(merged_at="2026-09-29T12:00:00Z"))
    assert f["size"] == 50 and f["files"] == 3 and f["days"] == 2 and f["hours"] == 48.0
    assert not f["bot_author"] and not f["revert"] and not f["release"] and not f["weekend"]


@pytest.mark.parametrize("over,event,tag", [
    ({"additions": 900, "deletions": 0}, "pr_opened", "huge"),
    ({"additions": 2, "deletions": 1}, "pr_opened", "tiny"),
    ({"draft": True}, "pr_opened", "draft"),
    ({"title": "Revert \"feat: x\""}, "pr_opened", "revert"),
    ({"head": {"ref": "release-please--branches--main", "sha": "0"}}, "pr_merged", "release"),
    ({"user": {"login": "dependabot[bot]", "type": "Bot"}}, "pr_merged", "dependabot"),
    ({"created_at": "2026-09-29T11:30:00Z", "merged_at": "2026-09-29T12:00:00Z"}, "pr_merged", "speedrun"),
    ({"created_at": "2026-09-01T12:00:00Z", "merged_at": "2026-09-29T12:00:00Z"}, "pr_merged", "marathon"),
    ({}, "pr_opened", "default"),
])
def test_the_most_specific_situation_wins(over, event, tag):
    assert troll.situations(event, troll.pr_facts(_pr(**over), now=NOW))[0] == tag


# ── lines ────────────────────────────────────────────────────────────────────

def test_every_line_in_every_pool_formats_with_its_facts():
    """A typo'd slot in willow-bot.json fails here, not on a live PR."""
    facts = {**troll.pr_facts(_pr(merged_at="2026-09-29T12:00:00Z")), "reviewer": "r", "reds": 4}
    for event, pools in troll._cfg()["troll"].items():
        for tag, lines in pools.items():
            for line in lines:
                assert troll._fillable(line, facts), (event, tag, line)
                assert "{" not in troll._render(line, facts), (event, tag, line)


def test_every_line_carries_at_least_one_fact_slot():
    """The PR's promise: no line in any pool is fact-free. (The guests are
    separate: they are the troll's cousins, not the troll.)"""
    bare = [f"{e}.{t}: {ln}" for e, pools in troll._cfg()["troll"].items()
            for t, lines in pools.items() for ln in lines if "{" not in ln]
    assert bare == []


def _formatted(event: str, tag: str, facts: dict) -> set[str]:
    return {troll._render(ln, facts) for ln in troll._cfg()["troll"][event][tag] if troll._fillable(ln, facts)}


def test_a_line_carries_the_fact():
    line, tag = troll.say("pr_opened", REPO, troll.pr_facts(_pr(additions=1200, deletions=34), now=NOW),
                          sha=NO_GUEST_SHA, rng=random.Random(1))
    assert tag == "huge" and "1,234" in line


def test_no_line_repeats_until_the_pool_is_spent():
    pool = troll._cfg()["troll"]["pr_opened"]["default"]
    facts = troll.pr_facts(_pr(), now=NOW)
    rng = random.Random(7)
    seen = [troll.say("pr_opened", REPO, facts, sha=NO_GUEST_SHA, rng=rng)[0] for _ in pool]
    assert len(set(seen)) == len(pool)
    nxt = troll.say("pr_opened", REPO, facts, sha=NO_GUEST_SHA, rng=rng)[0]
    assert nxt != seen[-1]  # a fresh cycle never opens with the line just said


def test_memory_is_per_repo():
    facts = troll.pr_facts(_pr(), now=NOW)
    pool = troll._cfg()["troll"]["pr_opened"]["default"]
    for _ in pool:
        troll.say("pr_opened", REPO, facts, sha=NO_GUEST_SHA)
    other = {troll.say("pr_opened", "o/other", facts, sha=NO_GUEST_SHA, rng=random.Random(i))[0]
             for i in range(len(pool))}
    assert len(other) == len(pool)


def test_guest_voices_are_seeded_by_the_sha_and_only_on_open_and_merge():
    line, tag = troll.say("pr_merged", REPO, {}, sha=FRANK_SHA)
    assert tag == "guest" and line.startswith("FRANK")
    assert troll.say("pr_merged", REPO, {}, sha=FRANK_SHA)[0] == line  # same sha, same guest
    # Loki 8F92E889: never on the moments FRANK has no line for.
    for event in ("review_approved", "review_changes_requested", "pr_ready", "pr_reopened",
                  "pr_closed_unmerged", "ci_recovered"):
        assert troll.say(event, REPO, {"reviewer": "r", "reds": 4, "size": 1, "files": 1, "days": 1},
                         sha=FRANK_SHA)[1] != "guest"


def test_the_guest_rate_is_one_in_twenty():
    n = 20000
    hits = sum(1 for i in range(n) if troll._guest("pr_opened", f"{i:040x}", "x"))
    assert 0.04 < hits / n < 0.06


def test_an_event_with_no_pool_says_nothing():
    assert troll.say("no_such_event", REPO, {}, sha=NO_GUEST_SHA) == ("", "none")


# ── the router ───────────────────────────────────────────────────────────────

@pytest.fixture
def posts(monkeypatch):
    monkeypatch.delenv("FRANK_MODE", raising=False)
    monkeypatch.delenv("PROPHET_MODE", raising=False)
    monkeypatch.setattr(router.fleet_bridge, "handle", lambda *a, **k: None)
    out = []
    return out, (lambda repo, number, body: out.append((repo, number, body)))


def _event(action, **pr_over):
    return {"action": action, "repository": {"full_name": REPO}, "pull_request": _pr(**pr_over)}


def test_opened_greets_with_title_fact_and_rune(posts):
    out, post = posts
    router.route("pull_request", _event("opened", additions=900, deletions=0), post)
    [(repo, number, body)] = out
    assert number == 7 and body.startswith("**Thrall someone** — ")
    line, rune = body[len("**Thrall someone** — "):].split("\n\n", 1)
    # Whichever huge line was drawn, it is one of the huge lines with the
    # real facts filled in (not a lucky pick of the one that says 900).
    assert line in _formatted("pr_opened", "huge", troll.pr_facts(_pr(additions=900, deletions=0)))
    assert rune.startswith("> ")


def test_merged_carries_days_title_and_horoscope(posts):
    out, post = posts
    router.route("pull_request", _event("closed", merged=True, merged_at="2026-09-29T12:00:00Z"), post)
    [(_, _, body)] = out
    assert body.startswith("**Karl someone** — ") and "_Today" in body


def test_a_bot_pr_gets_no_title_and_no_merge_count(posts):
    out, post = posts
    router.route("pull_request", _event("closed", merged=True, merged_at="2026-09-29T12:00:00Z",
                                        user={"login": "dependabot[bot]", "type": "Bot"}), post)
    [(_, _, body)] = out
    assert not body.startswith("**") and quips.get_title("dependabot[bot]") == "thrall"


@pytest.mark.parametrize("action,event", [("ready_for_review", "pr_ready"), ("reopened", "pr_reopened")])
def test_new_pr_moments_speak_from_their_own_pool(posts, action, event):
    out, post = posts
    router.route("pull_request", _event(action), post)
    [(_, number, body)] = out
    assert number == 7 and body in _formatted(event, "default", troll.pr_facts(_pr()))


def test_closed_without_merge_speaks(posts):
    out, post = posts
    router.route("pull_request", _event("closed", closed_at="2026-09-29T12:00:00Z"), post)
    assert len(out) == 1


@pytest.mark.parametrize("state,expect", [("approved", 1), ("changes_requested", 1), ("commented", 0)])
def test_reviews_name_the_reviewer(posts, state, expect):
    out, post = posts
    payload = {"action": "submitted", "repository": {"full_name": REPO}, "pull_request": _pr(),
               "review": {"state": state, "user": {"login": "reviewer-x"}, "commit_id": NO_GUEST_SHA,
                          "author_association": "MEMBER"}}
    router.route("pull_request_review", payload, post)
    assert len(out) == expect
    if expect:
        assert "reviewer-x" in out[0][2]


def test_green_after_three_reds_speaks_once(posts):
    out, post = posts
    for _ in range(3):
        sigh.bump_fail(REPO, 7)

    def leg(conclusion):
        return {"action": "completed", "repository": {"full_name": REPO},
                "check_run": {"conclusion": conclusion, "head_sha": NO_GUEST_SHA, "name": "t",
                              "pull_requests": [{"number": 7}]}}

    router.route("check_run", leg("success"), post)
    router.route("check_run", leg("success"), post)  # a second green leg of the same head
    assert len(out) == 1 and re.search(r"\b3\b", out[0][2])


def test_green_after_two_reds_says_nothing(posts):
    out, post = posts
    for _ in range(2):
        sigh.bump_fail(REPO, 7)
    router.route("check_run", {"action": "completed", "repository": {"full_name": REPO},
                               "check_run": {"conclusion": "success", "pull_requests": [{"number": 7}]}}, post)
    assert out == []


def test_frank_mode_still_takes_over(posts, monkeypatch):
    out, post = posts
    monkeypatch.setenv("FRANK_MODE", "1")
    router.route("pull_request", _event("opened"), post)
    assert out and "FRANK" in out[0][2]



# ── Loki 8F92E889 ────────────────────────────────────────────────────────────

def test_a_line_whose_fact_is_missing_is_never_drawn():
    """F4: sparse facts draw only lines they can fill; nothing reads "some"."""
    sparse = {"login": "x"}  # no size, no files, no days
    for _ in range(12):
        line, tag = troll.say("pr_opened", REPO, sparse, sha=NO_GUEST_SHA)
        assert line == "" or "some" not in line.split()
    # pr_opened.bot carries {login} and {size}: without size, nothing fits; silence.
    assert troll.say("pr_opened", REPO, {"login": "x", "bot_author": True}, sha=NO_GUEST_SHA)[0] == ""


def test_a_new_cycle_never_opens_with_the_line_just_said():
    """F6: a stub that always prefers the last line said proves the guard."""
    pool = troll._cfg()["troll"]["pr_opened"]["default"]
    facts = troll.pr_facts(_pr(), now=NOW)
    said = []

    class _Stub(random.Random):
        def choice(self, seq):
            wanted = said[-1] if said else None
            return next((x for x in seq if troll._render(x, facts) == wanted), seq[0])

    for _ in range(len(pool) + 1):
        said.append(troll.say("pr_opened", "o/cycle", facts, sha=NO_GUEST_SHA, rng=_Stub())[0])
    assert said[-1] != said[-2]


def test_out_of_scope_org_is_silent(posts):
    out, post = posts
    payload = _event("opened")
    payload["repository"]["full_name"] = "forge-play/Forge"
    router.route("pull_request", payload, post)
    assert out == []


@pytest.mark.parametrize("assoc", ["CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "NONE", None])
def test_outside_contributors_are_not_trolled(posts, assoc):
    out, post = posts
    router.route("pull_request", _event("opened", author_association=assoc), post)
    assert out == []


def test_close_reopen_loop_speaks_once_each(posts):
    """F2: 30 close/reopen cycles used to post 60 comments."""
    out, post = posts
    for _ in range(30):
        router.route("pull_request", _event("closed", closed_at="2026-09-29T12:00:00Z"), post)
        router.route("pull_request", _event("reopened"), post)
    assert len(out) == 2


def test_one_line_per_reviewer_per_pr(posts):
    out, post = posts
    for _ in range(10):
        router.route("pull_request_review", {
            "action": "submitted", "repository": {"full_name": REPO}, "pull_request": _pr(),
            "review": {"state": "approved", "user": {"login": "reviewer-x"}, "author_association": "MEMBER"}}, post)
    router.route("pull_request_review", {
        "action": "submitted", "repository": {"full_name": REPO}, "pull_request": _pr(),
        "review": {"state": "approved", "user": {"login": "reviewer-y"}, "author_association": "MEMBER"}}, post)
    assert len(out) == 2


def test_daily_cap_per_repo(posts, monkeypatch):
    out, post = posts
    monkeypatch.setitem(troll._cfg()["troll_scope"], "daily_cap", 3)
    for n in range(1, 8):
        router.route("pull_request", _event("opened", number=n), post)
    assert len(out) == 3


def test_red_count_is_called_red_checks_not_tries():
    """F5: the count is failing check runs, not attempts."""
    for line in troll._cfg()["troll"]["ci_recovered"]["default"]:
        assert "{reds} red checks" in line and "tries" not in line
