"""
watcher.py — Loki main loop. Grove + disk + git.
b17: LOKI3

Run: python3 -m loki.watcher
"""
import json
import logging
import os
import select
import subprocess
import time
from pathlib import Path

import psycopg2
import psycopg2.extensions

from loki import cerebras, context, poster
from loki.accumulator import Accumulator, Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("loki.watcher")

_GITHUB_ROOT = Path("/home/sean-campbell/github")
_CATALOG_PATH = _GITHUB_ROOT / "safe-app-store" / "catalog.json"
_DISK_SCAN_INTERVAL = 15 * 60   # 15 minutes
_GIT_SCAN_INTERVAL = 30 * 60    # 30 minutes


def _dsn() -> str:
    dsn = os.environ.get("WILLOW_DB_URL", "")
    if not dsn:
        pg_db = os.environ.get("WILLOW_PG_DB", "willow_19")
        pg_user = os.environ.get("WILLOW_PG_USER", os.environ.get("USER", ""))
        dsn = f"dbname={pg_db} user={pg_user}"
    return dsn


def _load_catalog_ids() -> set[str]:
    try:
        data = json.loads(_CATALOG_PATH.read_text())
        return {a["id"] for a in data.get("apps", [])}
    except Exception as e:
        log.warning("Could not load catalog: %s", e)
        return set()


_SKIP_REPOS = frozenset({
    "safe-app-store",   # the store itself, not an app
    "textual",          # Python library, not a fleet repo
})


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _scan_disk(accumulator: Accumulator, catalog_ids: set[str]) -> list[Signal]:
    signals = []
    disk_repos = []
    for entry in sorted(_GITHUB_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in _SKIP_REPOS:
            continue
        if not _is_git_repo(entry):
            continue  # skip plain directories like textual, node_modules, etc.
        disk_repos.append(entry.name)

        # Mistletoe: new spec file?
        for spec_file in entry.rglob("*spec*.md"):
            sig = accumulator.check_mistletoe(str(spec_file), disk_repos, catalog_ids)
            if sig:
                signals.append(sig)
                break  # one signal per repo per scan

    return signals


def _scan_git(accumulator: Accumulator, catalog_ids: set[str]) -> list[Signal]:
    signals = []
    for entry in sorted(_GITHUB_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in catalog_ids:
            continue
        # Hermes: new commits in uncataloged repo?
        try:
            out = subprocess.check_output(
                ["git", "-C", str(entry), "log", "-1", "--format=%ct"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            ts = float(out.decode().strip() or 0)
        except Exception:
            continue

        sig = accumulator.check_hermes(str(entry), catalog_ids, ts)
        if sig:
            signals.append(sig)
    return signals


def _route_oakenscroll(message: dict) -> None:
    """Forward message to #oakenscroll inbox. Direct post — no Cerebras."""
    poster.ensure_channel("oakenscroll", description="Oakenscroll inbox — messages routed here for async pickup")
    # Strip @oakenscroll from content so the digest doesn't re-trigger on itself
    content = message['content'].replace("@oakenscroll", "@[oakenscroll]")
    digest = (
        f"[#{message['channel']}] {message['sender']} (id={message['id']}):\n\n"
        f"{content}"
    )
    poster.post(digest, channel="oakenscroll", sender="grove-router")
    log.info("Routed message id=%d to #oakenscroll", message["id"])


def _fire(signal: Signal, channel: str) -> None:
    log.info("Signal fired: %s — %s", signal.trigger, signal.description)
    try:
        packet = context.build(signal, channel)
        message = cerebras.call(packet)
        if message:
            poster.post(message, channel)
        else:
            log.info("SILENCE — no post for %s", signal.trigger)
    except Exception as e:
        log.error("Fire failed for %s: %s", signal.trigger, e)


def _grove_listener():
    """Connect to Postgres and return a LISTEN connection on grove_channel."""
    conn = psycopg2.connect(_dsn())
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("LISTEN grove_channel;")
    log.info("Listening on grove_channel")
    return conn


def _handle_grove_message(raw_payload: str, accumulator: Accumulator, channel: str) -> None:
    """Parse notification payload and check triggers."""
    # Payload is channel_id as text — we need to fetch the actual message.
    # Re-query the latest unprocessed message in the notified channel.
    try:
        channel_id = int(raw_payload)
    except (ValueError, TypeError):
        return

    conn = poster._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, c.name, m.sender, m.content, m.created_at
                FROM grove.messages m
                JOIN grove.channels c ON c.id = m.channel_id
                WHERE m.channel_id = %s AND m.is_deleted = 0
                ORDER BY m.id DESC LIMIT 1
                """,
                (channel_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            message = {
                "id": row[0], "channel": row[1], "sender": row[2],
                "content": row[3], "created_at": str(row[4]),
            }
    finally:
        conn.close()

    ch = message["channel"]

    # Oakenscroll — direct route, no Cerebras
    sig = accumulator.check_oakenscroll(message)
    if sig:
        _route_oakenscroll(message)
        return

    # Lokasenna
    sig = accumulator.check_lokasenna(message)
    if sig:
        _fire(sig, ch)
        return

    # Salmon
    sig = accumulator.check_salmon(message)
    if sig:
        _fire(sig, ch)
        return

    # Surfacing
    sig = accumulator.check_surfacing(ch, message)
    if sig:
        _fire(sig, ch)
        return


def run():
    log.info("Loki watcher starting")
    accumulator = Accumulator()

    last_disk_scan = 0.0
    last_git_scan = 0.0

    grove_conn = None

    while True:
        now = time.time()

        # Reconnect Grove listener if needed
        if grove_conn is None:
            try:
                grove_conn = _grove_listener()
            except Exception as e:
                log.error("Grove connect failed: %s — retrying in 30s", e)
                time.sleep(30)
                continue

        # Poll Grove LISTEN/NOTIFY (5s timeout)
        try:
            ready = select.select([grove_conn], [], [], 5)
            if ready[0]:
                grove_conn.poll()
                while grove_conn.notifies:
                    notify = grove_conn.notifies.pop()
                    active_ch = poster.active_channel()
                    _handle_grove_message(notify.payload, accumulator, active_ch)
        except Exception as e:
            log.error("Grove poll error: %s — reconnecting", e)
            try:
                grove_conn.close()
            except Exception:
                pass
            grove_conn = None
            continue

        # Disk scan every 15 minutes
        if now - last_disk_scan >= _DISK_SCAN_INTERVAL:
            catalog_ids = _load_catalog_ids()
            signals = _scan_disk(accumulator, catalog_ids)
            active_ch = poster.active_channel()
            for sig in signals:
                _fire(sig, active_ch)
            last_disk_scan = now

        # Git scan every 30 minutes
        if now - last_git_scan >= _GIT_SCAN_INTERVAL:
            catalog_ids = _load_catalog_ids()
            signals = _scan_git(accumulator, catalog_ids)
            active_ch = poster.active_channel()
            for sig in signals:
                _fire(sig, active_ch)
            last_git_scan = now


if __name__ == "__main__":
    run()
