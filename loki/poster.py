"""
poster.py — Post to Grove as loki.
b17: LOKI3
"""
import logging
import os

import psycopg2

log = logging.getLogger("loki.poster")


def _connect():
    # Match Grove's own connection pattern (safe-app-grove/grove_db.py)
    dsn = os.environ.get("WILLOW_DB_URL", "")
    if not dsn:
        pg_db = os.environ.get("WILLOW_PG_DB", "willow_19")
        pg_user = os.environ.get("WILLOW_PG_USER", os.environ.get("USER", ""))
        dsn = f"dbname={pg_db} user={pg_user}"
    return psycopg2.connect(dsn)


def _channel_id(cur, channel_name: str) -> int:
    cur.execute(
        "SELECT id FROM grove.channels WHERE name = %s AND is_archived = FALSE",
        (channel_name,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Grove channel not found: {channel_name!r}")
    return row[0]


def ensure_channel(channel_name: str, description: str = "") -> None:
    """Create channel if it doesn't exist."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM grove.channels WHERE name = %s",
                    (channel_name,),
                )
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO grove.channels (name, channel_type, description) VALUES (%s, 'group', %s)",
                        (channel_name, description or f"Auto-created: {channel_name}"),
                    )
                    log.info("Created Grove channel #%s", channel_name)
    finally:
        conn.close()


def post(message: str, channel: str = "tonight", sender: str = "loki") -> int:
    """Post message to Grove. Returns message id."""
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cid = _channel_id(cur, channel)
                cur.execute(
                    """
                    INSERT INTO grove.messages (channel_id, sender, content, message_type)
                    VALUES (%s, %s, %s, 'text')
                    RETURNING id
                    """,
                    (cid, sender, message),
                )
                msg_id = cur.fetchone()[0]
                log.info("Posted to #%s as %s — id=%d", channel, sender, msg_id)
                return msg_id
    finally:
        conn.close()


def active_channel() -> str:
    """Return #tonight if it had messages in the last 2 hours, else #general."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM grove.messages m
                JOIN grove.channels c ON c.id = m.channel_id
                WHERE c.name = 'tonight'
                  AND m.created_at > NOW() - INTERVAL '2 hours'
                  AND m.is_deleted = 0
                """
            )
            count = cur.fetchone()[0]
            return "tonight" if count > 0 else "general"
    finally:
        conn.close()
