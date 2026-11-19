import sqlite3
from pathlib import Path

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
    content_hash TEXT
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
