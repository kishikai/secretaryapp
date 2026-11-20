import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, message_text) VALUES (new.id, new.message_text); END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_text) VALUES ('delete', old.id, old.message_text); END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF message_text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message_text) VALUES ('delete', old.id, old.message_text);
    INSERT INTO messages_fts(rowid, message_text) VALUES (new.id, new.message_text); END;
"""

RULES_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('keyword', 'pattern')),
    keywords TEXT,            -- JSON array of phrases, for kind = 'keyword'
    pattern TEXT,             -- predefined pattern name, for kind = 'pattern'
    category TEXT,
    notify INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    chat_id TEXT,             -- NULL = every chat
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS message_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    matched_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (message_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_rule_message ON message_matches (rule_id, message_id);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    matched_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    read_at TEXT,
    UNIQUE (rule_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications (read_at, id);
"""

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,      -- SHA-256 of the cookie value
    admin_id INTEGER NOT NULL REFERENCES admins(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,  -- SHA-256 of the token
    scope TEXT NOT NULL CHECK (scope IN ('ingest')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_used_at TEXT,
    revoked_at TEXT
);
"""

FTS_REBUILD = "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild');"


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success, rolls back on error, and is always closed."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        # SQLite ignores REFERENCES clauses unless this is set on every connection.
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()


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
        # Migration for databases created before search and filters (v0.4).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id, id)")
        # v0.7 (Sprint 8): /stats counts by source and by category without scanning whole tables.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_source ON messages (source)")
        conn.execute("DROP INDEX IF EXISTS idx_matches_rule")  # replaced by idx_matches_rule_message
        had_fts = (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'").fetchone()
            is not None
        )
        # executescript commits first; creating the index and rebuilding it run as one script.
        conn.executescript(FTS_SCHEMA if had_fts else FTS_SCHEMA + FTS_REBUILD)
        # Migration for databases created before rules and notifications (v0.5).
        conn.executescript(RULES_SCHEMA)
        # Migration for databases created before authentication (v0.6).
        conn.executescript(AUTH_SCHEMA)
        # v0.8 (Phase 1): roles. Existing accounts become secretaries and keep full access.
        if "role" not in {row["name"] for row in conn.execute("PRAGMA table_info(admins)")}:
            conn.execute(
                "ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'secretary' "
                "CHECK (role IN ('secretary', 'assistant', 'viewer'))"
            )
