"""
Lightweight SQLite-backed state store for order processing.

Tracks:
  - order_id, last known status, whether notification was sent
  - sent Telegram message_id per destination (for delete-before-resend)

Old records (> STATE_RETENTION_DAYS) are pruned automatically via cleanup_old_records().
"""

import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.getenv('STATE_DB_PATH', 'order_state.db')
STATE_RETENTION_DAYS = int(os.getenv('STATE_RETENTION_DAYS', '30'))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every run."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_state (
                order_id   TEXT PRIMARY KEY,
                status     TEXT NOT NULL,
                notified   INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_ids (
                order_id       TEXT NOT NULL,
                destination_id TEXT NOT NULL,
                message_id     INTEGER NOT NULL,
                PRIMARY KEY (order_id, destination_id)
            )
        """)


def get_order_state(order_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM order_state WHERE order_id = ?", (str(order_id),)
        ).fetchone()
        return dict(row) if row else None


def set_order_state(order_id: str, status: str, notified: bool = False):
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute("""
            INSERT INTO order_state (order_id, status, notified, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status     = excluded.status,
                notified   = excluded.notified,
                updated_at = excluded.updated_at
        """, (str(order_id), status, int(notified), now))


def get_message_id(order_id: str, destination_id: str) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT message_id FROM message_ids WHERE order_id = ? AND destination_id = ?",
            (str(order_id), str(destination_id)),
        ).fetchone()
        return row['message_id'] if row else None


def set_message_id(order_id: str, destination_id: str, message_id: int):
    with _connect() as conn:
        conn.execute("""
            INSERT INTO message_ids (order_id, destination_id, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(order_id, destination_id) DO UPDATE SET
                message_id = excluded.message_id
        """, (str(order_id), str(destination_id), message_id))


def cleanup_old_records():
    """Remove records older than STATE_RETENTION_DAYS days."""
    cutoff = (datetime.utcnow() - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    with _connect() as conn:
        old_ids = [
            row['order_id']
            for row in conn.execute(
                "SELECT order_id FROM order_state WHERE updated_at < ?", (cutoff,)
            ).fetchall()
        ]
        if old_ids:
            placeholders = ','.join('?' * len(old_ids))
            conn.execute(f"DELETE FROM message_ids WHERE order_id IN ({placeholders})", old_ids)
            conn.execute(f"DELETE FROM order_state WHERE order_id IN ({placeholders})", old_ids)
