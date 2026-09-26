from willow_bot.steward import upstream_triage as triage


def _n(**kwargs) -> triage.Notification:
    base = {
        "id": "1",
        "reason": "subscribed",
        "subject_type": "PullRequest",
        "subject_title": "fix: something",
        "subject_url": "https://api.github.com/repos/o/r/pulls/1",
        "repo": "other/upstream",
        "updated_at": "2026-01-01T00:00:00Z",
        "unread": True,
    }
    base.update(kwargs)
    return triage.Notification(**base)


def test_urgent_on_review_requested() -> None:
    assert triage.classify(_n(reason="review_requested")) == "urgent"


def test_noise_ci_on_unwatched_repo() -> None:
    n = _n(reason="ci_activity", subject_title="CI: tests failed")
    assert triage.classify(n, watch_repos=["watched/repo"]) == "noise"


def test_draft_comment_on_watched_repo() -> None:
    n = _n(reason="comment", repo="watched/repo", subject_title="please address")
    assert triage.classify(n, watch_repos=["watched/repo"]) == "draft"
