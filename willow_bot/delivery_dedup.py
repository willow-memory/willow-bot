"""Delivery-level webhook dedup for willow-bot.

Every GitHub webhook POST carries ``X-GitHub-Delivery: <uuid>``; a redelivery
(operator-triggered replay, GitHub's own retry on a non-2xx, or a proxy
that hands the same body to two instances) sends the SAME uuid. Without a
top-level guard bot.py's dispatch runs top-to-bottom on every retry:
``router.route`` calls ``quips.record_merge``, ``fleet_bridge.handle``
appends to ``event-log.jsonl`` and ``deposit_from_check_run_payload``
appends to ``ci_outcomes.jsonl``. The upstream_steward inbox and the
gitsync trigger flag are already semantic-idempotent, but the journals and
the merge counter are not — a replay doubles them.

This module is the boundary guard. ``mark_seen(delivery_id)`` returns
``True`` when the id is new (dispatch should run) and ``False`` when it
has been seen before (short-circuit). A bounded LRU persisted under
``$WILLOW_HOME/willow-bot/delivery-seen.json`` survives a restart. On a
disk failure we fail open — dispatch runs and the semantic-idempotent
layers still hold for the inbox / gitsync paths, and the journal takes a
duplicate — because a dropped legitimate event is worse than an occasional
duplicate row in a journal that is already a replay of what GitHub asserts.

Gap ``acfd27ae3259`` (webhook idempotency sub-part).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("willow-bot.delivery_dedup")

# The last N delivery ids to remember. GitHub retries a failing webhook up
# to 5 times over ~24 hours; an operator manual redelivery is arbitrary in
# time. 5000 is roughly a fleet-week of deliveries and stays small on disk.
_MAX_IDS = 5000


def _willow_home() -> Path:
    raw = os.environ.get("WILLOW_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "sean-data-vault" / "willow-operator-box"


def state_path() -> Path:
    """Where the seen-delivery LRU lives. Kept next to ``event-log.jsonl``
    so an operator inspecting the bot's write set finds it there."""
    override = os.environ.get("WILLOW_BOT_DELIVERY_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    return _willow_home() / "willow-bot" / "delivery-seen.json"


def _load(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if isinstance(x, str) and x]


def _write_atomic(path: Path, ids: list[str]) -> None:
    """Atomic rename so a partial write never leaves a truncated file the
    next mark reads as empty (which would mark every id as new)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".delivery-seen.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ids, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_seen(delivery_id: str) -> bool:
    """Return True the first time this delivery id is presented, False on
    every re-presentation. An empty or missing id returns True and is not
    persisted — a webhook without ``X-GitHub-Delivery`` is not GitHub's,
    and the caller can decide whether to accept it, but this step will
    not turn one missing header into a permanent drop.

    On a disk error we log and return True (fail open) so a broken cache
    never blocks a real delivery. The semantic-idempotent downstream
    layers keep this correct for the paths that matter (inbox, gitsync);
    a duplicate journal row in that case is the accepted cost.
    """
    if not delivery_id:
        return True
    path = state_path()
    try:
        ids = _load(path)
    except Exception as exc:  # noqa: BLE001 — a bad cache is not a reason to drop the event
        log.warning("delivery-seen load failed (%s): %s", path, exc)
        return True
    if delivery_id in ids:
        return False
    ids.append(delivery_id)
    if len(ids) > _MAX_IDS:
        ids = ids[-_MAX_IDS:]
    try:
        _write_atomic(path, ids)
    except Exception as exc:  # noqa: BLE001 — same rule as read: log, do not drop
        log.warning("delivery-seen write failed (%s): %s", path, exc)
    return True
