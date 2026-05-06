from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import sqlite3

from .config import DB_PATH, settings
from .security import hash_password


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def init_db() -> None:
    cfg = settings()
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_admin INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS usage_limits (
                user_id INTEGER PRIMARY KEY,
                daily_limit INTEGER NOT NULL,
                used_today INTEGER NOT NULL DEFAULT 0,
                last_reset_date TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS kb_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS kb_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(file_id) REFERENCES kb_files(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS kb_vectors (
                chunk_id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(chunk_id) REFERENCES kb_chunks(id) ON DELETE CASCADE,
                FOREIGN KEY(file_id) REFERENCES kb_files(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_id INTEGER,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                background TEXT NOT NULL,
                avatar TEXT NOT NULL,
                tone TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS group_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                event TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                persona_id INTEGER,
                speaker TEXT NOT NULL,
                avatar TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(group_id) REFERENCES group_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY(persona_id) REFERENCES personas(id) ON DELETE SET NULL
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO system_config(key, value) VALUES('default_daily_limit', ?)",
            (str(cfg["default_daily_limit"]),),
        )
        admin = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
        if not admin:
            created_at = now_iso()
            cur = db.execute(
                "INSERT INTO users(username, password_hash, created_at, is_admin) VALUES(?, ?, ?, 1)",
                ("admin", hash_password("admin123"), created_at),
            )
            db.execute(
                "INSERT INTO usage_limits(user_id, daily_limit, used_today, last_reset_date) VALUES(?, ?, 0, ?)",
                (cur.lastrowid, 999, date.today().isoformat()),
            )
