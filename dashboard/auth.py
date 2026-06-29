"""Auth module: SQLite-backed user management with werkzeug password hashing."""

import os
import sqlite3
from werkzeug.security import check_password_hash, generate_password_hash

_DIR = os.path.join(os.path.dirname(__file__), 'data')
_DB = os.path.join(_DIR, 'auth.db')


def init_auth():
    os.makedirs(_DIR, exist_ok=True)
    with sqlite3.connect(_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username             TEXT PRIMARY KEY,
                password_hash        TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 0
            )
        """)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, must_change_password) VALUES (?, ?, ?)",
                ('admin', generate_password_hash('admin'), 1),
            )


def verify_user(username: str, password: str) -> dict | None:
    with sqlite3.connect(_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            return dict(row)
    return None


def change_password(username: str, new_password: str):
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE username = ?",
            (generate_password_hash(new_password), username),
        )
