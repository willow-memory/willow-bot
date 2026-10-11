"""redeliver: ask GitHub again for the App-webhook deliveries that never got
a 2xx, for events the bot reads, a bounded number of times. No network."""
from __future__ import annotations

from willow_bot.steward import redeliver


def _d(i, guid, event="check_run", code=404):
    return {"id": i, "guid": guid, "event": event, "status_code": code,
            "delivered_at": "2026-10-11T04:00:00Z"}


def test_plan_picks_failed_handled_events_without_a_later_success():
    deliveries = [
        _d(1, "g-lost"),                       # 404, never answered: ask again
        _d(2, "g-ok", code=200),               # answered
        _d(3, "g-retried"), _d(4, "g-retried", code=200),  # a redelivery already landed
        _d(5, "g-noise", event="workflow_job"),  # an event no handler reads
        _d(6, "g-unanswered", code=0),         # no HTTP answer at all: also lost
    ]
    assert [d["guid"] for d in redeliver.plan(deliveries, {})] == ["g-unanswered", "g-lost"]


def test_one_guid_is_asked_once_per_tick_and_capped_overall():
    deliveries = [_d(1, "g"), _d(2, "g")]
    assert [d["id"] for d in redeliver.plan(deliveries, {})] == [2]  # the newest attempt only
    assert redeliver.plan(deliveries, {"g": {"n": redeliver.MAX_ATTEMPTS}}) == []


def test_run_asks_and_counts_attempts(monkeypatch):
    asked = []
    monkeypatch.setattr(redeliver, "_fetch_deliveries", lambda: [_d(10, "g1"), _d(11, "g2", "pull_request")])
    monkeypatch.setattr(redeliver, "_redeliver", lambda i: asked.append(i))
    r = redeliver.run()
    assert r["status"] == "ok" and sorted(asked) == [10, 11]
    assert [x["attempt"] for x in r["redelivered"]] == [1, 1]
    redeliver.run()
    saved = redeliver._load()
    assert saved["g1"]["n"] == 2 and saved["g2"]["n"] == 2


def test_stops_after_max_attempts(monkeypatch):
    asked = []
    monkeypatch.setattr(redeliver, "_fetch_deliveries", lambda: [_d(10, "g1")])
    monkeypatch.setattr(redeliver, "_redeliver", lambda i: asked.append(i))
    for _ in range(redeliver.MAX_ATTEMPTS + 3):
        r = redeliver.run()
    assert len(asked) == redeliver.MAX_ATTEMPTS and r["gave_up"] == 1


def test_a_refused_redelivery_is_reported_and_not_counted(monkeypatch):
    def refuse(i):
        raise RuntimeError("422")

    monkeypatch.setattr(redeliver, "_fetch_deliveries", lambda: [_d(10, "g1")])
    monkeypatch.setattr(redeliver, "_redeliver", refuse)
    r = redeliver.run()
    assert r["status"] == "partial" and r["refused"] and "g1" not in redeliver._load()


def test_unreachable_record_is_its_own_state():
    assert redeliver.run()["status"] == "unreachable"  # the test floor keeps it off the network
