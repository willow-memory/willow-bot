"""Delivery-id dedup for the webhook boundary.

Gap acfd27ae3259 (webhook idempotency sub-part): a redelivered
X-GitHub-Delivery must not run dispatch a second time. The module is a
plain LRU on disk; every path here is a unit test — no FastAPI, no
network. The bot.py integration is covered in tests/test_bot_webhook.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from willow_bot import delivery_dedup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.delenv("WILLOW_BOT_DELIVERY_STATE", raising=False)
    return tmp_path


def test_first_delivery_is_new_second_is_not(home: Path) -> None:
    assert delivery_dedup.mark_seen("d-0001") is True
    assert delivery_dedup.mark_seen("d-0001") is False


def test_distinct_ids_are_all_new(home: Path) -> None:
    assert delivery_dedup.mark_seen("d-a") is True
    assert delivery_dedup.mark_seen("d-b") is True
    assert delivery_dedup.mark_seen("d-c") is True
    # And the second visit to each is a dedup.
    assert delivery_dedup.mark_seen("d-a") is False
    assert delivery_dedup.mark_seen("d-b") is False
    assert delivery_dedup.mark_seen("d-c") is False


def test_state_survives_a_reload(home: Path) -> None:
    """An LRU that only lived in memory would forget every id at restart —
    which is exactly when a GitHub retry lands. The file must round-trip."""
    delivery_dedup.mark_seen("d-persistent")
    # Simulate a restart by reading the file back directly (no cache in the
    # module — every mark reads current state, so this is already the shape).
    ids = json.loads(delivery_dedup.state_path().read_text())
    assert "d-persistent" in ids
    assert delivery_dedup.mark_seen("d-persistent") is False


def test_empty_id_is_accepted_but_not_persisted(home: Path) -> None:
    """A webhook without X-GitHub-Delivery is accepted (mark returns True
    so the caller dispatches once); nothing is written, so an empty id
    does not consume LRU slots and does not collide with a later empty."""
    assert delivery_dedup.mark_seen("") is True
    assert delivery_dedup.mark_seen("") is True  # every empty is "new"
    assert not delivery_dedup.state_path().is_file()


def test_lru_trims_to_max_and_keeps_recent(home: Path, monkeypatch) -> None:
    """The LRU has a fixed cap; the oldest ids evict first. An id that
    was evicted then re-presented is 'new' again — that's the accepted
    cost of a bounded cache, and only bites a redelivery arriving after
    _MAX_IDS other deliveries."""
    monkeypatch.setattr(delivery_dedup, "_MAX_IDS", 3)
    for i in range(5):
        assert delivery_dedup.mark_seen(f"d-{i}") is True
    # 0 and 1 evicted, 2/3/4 retained.
    ids = json.loads(delivery_dedup.state_path().read_text())
    assert ids == ["d-2", "d-3", "d-4"]
    assert delivery_dedup.mark_seen("d-4") is False
    assert delivery_dedup.mark_seen("d-0") is True  # evicted, so 'new' again


def test_corrupt_state_file_is_treated_as_empty(home: Path) -> None:
    """A truncated / hand-edited state file must not crash the receiver;
    every id becomes 'new' until the file is repaired on the next write."""
    path = delivery_dedup.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert delivery_dedup.mark_seen("d-after-corrupt") is True
    # And now the file is a valid JSON list again.
    ids = json.loads(path.read_text())
    assert ids == ["d-after-corrupt"]


def test_state_file_of_wrong_shape_is_treated_as_empty(home: Path) -> None:
    """A JSON file that is not a list must not confuse the LRU."""
    path = delivery_dedup.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"delivery-seen": ["d-a"]}', encoding="utf-8")
    assert delivery_dedup.mark_seen("d-a") is True  # the wrong-shape file is ignored


def test_state_path_env_override(tmp_path, monkeypatch) -> None:
    """The state path can be pinned with WILLOW_BOT_DELIVERY_STATE — a test
    knob and, in production, a way to keep the LRU on a durable mount
    when WILLOW_HOME is a tmpfs."""
    override = tmp_path / "elsewhere" / "delivery.json"
    monkeypatch.setenv("WILLOW_BOT_DELIVERY_STATE", str(override))
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    assert delivery_dedup.state_path() == override
    assert delivery_dedup.mark_seen("d-elsewhere") is True
    assert override.is_file()


def test_atomic_write_never_leaves_truncated_state(home: Path) -> None:
    """A crash halfway through a write must not leave an empty state that
    the next receiver reads as 'no ids seen'. The atomic-rename shape
    means either the old file is still there or the new one is."""
    delivery_dedup.mark_seen("d-1")
    delivery_dedup.mark_seen("d-2")
    path = delivery_dedup.state_path()
    # No temp file left over.
    leftovers = list(path.parent.glob(".delivery-seen.*.tmp"))
    assert leftovers == []
    # The final file is a valid JSON list.
    ids = json.loads(path.read_text())
    assert ids == ["d-1", "d-2"]
