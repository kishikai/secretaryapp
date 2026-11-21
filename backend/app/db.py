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

IDEMPOTENCY_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,              -- 'admin:<id>' or 'token:<id>'
    key TEXT NOT NULL,                -- the Idempotency-Key header value
    method TEXT NOT NULL,
    path TEXT NOT NULL,               -- including the query string
    body_hash TEXT NOT NULL,          -- SHA-256 of the raw request body
    status INTEGER,                   -- NULL while the request is in progress
    content_type TEXT,
    body BLOB,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (scope, key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys (created_at);
"""

MEMBERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    member_code TEXT UNIQUE,  -- optional external ID
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    joined_on TEXT,           -- YYYY-MM-DD
    left_on TEXT,             -- YYYY-MM-DD
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_members_full_name ON members (full_name);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS member_groups (
    member_id INTEGER NOT NULL REFERENCES members(id),
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (member_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_member_groups_group ON member_groups (group_id);
"""

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    starts_at TEXT NOT NULL,  -- local wall-clock time, YYYY-MM-DDTHH:MM (no time zone)
    ends_at TEXT,             -- same format
    location TEXT,
    category TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'completed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_events_starts_at ON events (starts_at);
CREATE TABLE IF NOT EXISTS event_attendees (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (event_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_event_attendees_member ON event_attendees (member_id);
"""

# v0.11 (Phase 3): attendance. One status per expected person (D7); every change is audited (D8).
ATTENDANCE_COLUMNS = {
    "event_attendees": {
        "status": "TEXT NOT NULL DEFAULT 'not_recorded' "
        "CHECK (status IN ('not_recorded', 'present', 'late', 'late_excused', 'absent', 'absent_excused', 'no_practice'))",
        "recorded_at": "TEXT",
        "recorded_by": "INTEGER REFERENCES admins(id)",
    },
    "events": {
        "finalized_at": "TEXT",
        "finalized_by": "INTEGER REFERENCES admins(id)",
        # v0.13: the first day's id when a long event is taken day by day. No REFERENCES, so a
        # draft first day can still be deleted.
        "series_id": "INTEGER",
    },
}
ATTENDANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS attendance_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),  -- no cascade: history outlives nothing
    member_id INTEGER REFERENCES members(id),         -- NULL for finalise and reopen
    action TEXT NOT NULL CHECK (action IN ('record', 'finalize', 'reopen')),
    old_status TEXT,
    new_status TEXT,
    reason TEXT,
    actor_id INTEGER REFERENCES admins(id),
    actor TEXT NOT NULL,                               -- username at the time
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_attendance_audit_event ON attendance_audit (event_id, id);
"""

# v0.12 (Phase 5): a secretary's decision on a message as attendance evidence. Messages are only ever
# evidence: nothing changes a register until a secretary approves (decision 4 of the change plan).
EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_reviews (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
    state TEXT NOT NULL CHECK (state IN ('approved', 'rejected')),
    message_text TEXT NOT NULL,  -- the text as reviewed; a message's text can be upgraded later
    member_id INTEGER REFERENCES members(id),
    event_id INTEGER REFERENCES events(id),
    status TEXT CHECK (status IN ('present', 'late', 'late_excused', 'absent', 'absent_excused')),
    note TEXT,
    reviewer_id INTEGER REFERENCES admins(id),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

# v0.14: "excused" became "late_excused" and "absent_excused". SQLite can't change a CHECK, so
# these tables are rebuilt once; an old "excused" (notice of absence) becomes "absent_excused".
STATUS_REBUILDS = {
    "event_attendees": """
CREATE TABLE event_attendees_v14 (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status TEXT NOT NULL DEFAULT 'not_recorded'
        CHECK (status IN ('not_recorded', 'present', 'late', 'late_excused', 'absent', 'absent_excused')),
    recorded_at TEXT,
    recorded_by INTEGER REFERENCES admins(id),
    PRIMARY KEY (event_id, member_id)
);
INSERT INTO event_attendees_v14 (event_id, member_id, added_at, status, recorded_at, recorded_by)
    SELECT event_id, member_id, added_at,
           CASE status WHEN 'excused' THEN 'absent_excused' ELSE status END, recorded_at, recorded_by
    FROM event_attendees;
DROP TABLE event_attendees;
ALTER TABLE event_attendees_v14 RENAME TO event_attendees;
CREATE INDEX IF NOT EXISTS idx_event_attendees_member ON event_attendees (member_id);
""",
    "evidence_reviews": """
CREATE TABLE evidence_reviews_v14 (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
    state TEXT NOT NULL CHECK (state IN ('approved', 'rejected')),
    message_text TEXT NOT NULL,
    member_id INTEGER REFERENCES members(id),
    event_id INTEGER REFERENCES events(id),
    status TEXT CHECK (status IN ('present', 'late', 'late_excused', 'absent', 'absent_excused')),
    note TEXT,
    reviewer_id INTEGER REFERENCES admins(id),
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
INSERT INTO evidence_reviews_v14
    SELECT message_id, state, message_text, member_id, event_id,
           CASE status WHEN 'excused' THEN 'absent_excused' ELSE status END, note, reviewer_id, reviewer, reviewed_at
    FROM evidence_reviews;
DROP TABLE evidence_reviews;
ALTER TABLE evidence_reviews_v14 RENAME TO evidence_reviews;
""",
}

# v0.15: no_practice is a valid attendance status, so existing SQLite CHECK constraints need a rebuild.
NO_PRACTICE_REBUILD = """
CREATE TABLE event_attendees_v15 (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    member_id INTEGER NOT NULL REFERENCES members(id),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status TEXT NOT NULL DEFAULT 'not_recorded'
        CHECK (status IN ('not_recorded', 'present', 'late', 'late_excused', 'absent', 'absent_excused', 'no_practice')),
    recorded_at TEXT,
    recorded_by INTEGER REFERENCES admins(id),
    PRIMARY KEY (event_id, member_id)
);
INSERT INTO event_attendees_v15 (event_id, member_id, added_at, status, recorded_at, recorded_by)
    SELECT event_id, member_id, added_at, status, recorded_at, recorded_by FROM event_attendees;
DROP TABLE event_attendees;
ALTER TABLE event_attendees_v15 RENAME TO event_attendees;
CREATE INDEX IF NOT EXISTS idx_event_attendees_member ON event_attendees (member_id);
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
        # v0.9 (Phase 1): members and groups.
        conn.executescript(MEMBERS_SCHEMA)
        # v0.10 (Phase 2): events and expected attendees.
        conn.executescript(EVENTS_SCHEMA)
        for table, wanted in ATTENDANCE_COLUMNS.items():
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in wanted.items():
                if column not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.executescript(ATTENDANCE_SCHEMA)
        if "evidence_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(attendance_audit)")}:
            conn.execute("ALTER TABLE attendance_audit ADD COLUMN evidence_id INTEGER REFERENCES messages(id)")
        conn.executescript(EVIDENCE_SCHEMA)
        # Idempotency-Key replay storage.
        conn.executescript(IDEMPOTENCY_SCHEMA)
        for table, rebuild in STATUS_REBUILDS.items():
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            if "absent_excused" not in sql:
                conn.executescript("BEGIN;" + rebuild + "COMMIT;")
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'event_attendees'").fetchone()[0]
        if "no_practice" not in sql:
            conn.executescript("BEGIN;" + NO_PRACTICE_REBUILD + "COMMIT;")
