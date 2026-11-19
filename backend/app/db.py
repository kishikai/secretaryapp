import sqlite3
from pathlib import Path

from app.pipeline import match_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    message_text TEXT NOT NULL,
    sender TEXT,
    timestamp TEXT,
    source TEXT NOT NULL CHECK (source IN ('clipboard', 'ocr')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    content_hash TEXT,
    match_key TEXT
);

CREATE TABLE IF NOT EXISTS message_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    source TEXT NOT NULL,
    message_text TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (message_id, source, message_text)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # Migration for databases created before duplicate detection (v0.1.0).
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        if "content_hash" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN content_hash TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_content_hash ON messages (content_hash)"
        )
        # Migration for databases created before merging across sources (v0.2).
        if "match_key" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN match_key TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_match ON messages (chat_id, match_key)"
        )
        pending = conn.execute("SELECT id, message_text FROM messages WHERE match_key IS NULL").fetchall()
        conn.executemany(
            "UPDATE messages SET match_key = ? WHERE id = ?",
            [(match_key(row["message_text"]), row["id"]) for row in pending],
        )
        conn.execute(
            "INSERT OR IGNORE INTO message_variants (message_id, source, message_text, confidence) "
            "SELECT id, source, message_text, confidence FROM messages"
        )
