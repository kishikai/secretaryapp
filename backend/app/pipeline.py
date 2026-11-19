import difflib
import hashlib
import sqlite3
import unicodedata
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

FUZZY_WINDOW = 500
FUZZY_MIN_KEY_LENGTH = 12
FUZZY_MIN_RATIO = 0.9
FUZZY_MIN_WORD_OVERLAP = 0.5


class MessageIn(BaseModel):
    message_text: str = Field(min_length=1, max_length=10_000)
    chat_id: Optional[str] = Field(default=None, max_length=200)
    sender: Optional[str] = Field(default=None, max_length=200)
    timestamp: Optional[str] = Field(default=None, max_length=50)
    source: Literal["clipboard", "ocr"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("message_text")
    @classmethod
    def normalise_and_require_text(cls, value: str) -> str:
        text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
        if not text:
            raise ValueError("message_text must not be blank")
        return text

    def content_hash(self) -> str:
        # shortcut: identical text with no sender/timestamp in one chat counts as one message; refine once real samples show sender/time.
        return text_hash(self.chat_id, self.sender, self.timestamp, self.message_text)


def text_hash(chat_id: Optional[str], sender: Optional[str], timestamp: Optional[str], text: str) -> str:
    key = "\x1f".join([chat_id or "", sender or "", timestamp or "", text])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def match_key(text: str) -> str:
    """Case, punctuation and whitespace-insensitive form of a message."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return set("".join(ch if ch.isalnum() else " " for ch in folded).split())


def digits(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if ch.isdigit())


def _compatible(row: sqlite3.Row, message: MessageIn) -> bool:
    return (
        (row["sender"] is None or message.sender is None or row["sender"] == message.sender)
        and (
            row["timestamp"] is None
            or message.timestamp is None
            or row["timestamp"] == message.timestamp
        )
    )


def _find_match(
    conn: sqlite3.Connection, message: MessageIn, content_hash: str, key: str
) -> tuple[Optional[sqlite3.Row], Optional[str]]:
    row = conn.execute(
        "SELECT * FROM messages WHERE chat_id IS ? AND content_hash = ?",
        (message.chat_id, content_hash),
    ).fetchone()
    if row:
        return row, "exact"

    if key:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id IS ? AND match_key = ? ORDER BY id",
            (message.chat_id, key),
        ).fetchall()
        for row in rows:
            # Clipboard text is exact, so two clipboard copies that differ at all ("Ok?" vs "Ok.") are different messages.
            if _compatible(row, message) and not (row["source"] == "clipboard" and message.source == "clipboard"):
                return row, "normalised"

    # shortcut: scans the 500 newest rows per chat; add an index-backed candidate search if chats grow large.
    recent = conn.execute(
        "SELECT * FROM messages WHERE chat_id IS ? ORDER BY id DESC LIMIT ?",
        (message.chat_id, FUZZY_WINDOW),
    ).fetchall()
    new_digits = digits(message.message_text)
    new_words = _words(message.message_text)
    for row in recent:
        if not _compatible(row, message):
            continue
        if row["source"] == "clipboard" and message.source == "clipboard":
            continue
        if digits(row["message_text"]) != new_digits:
            continue
        row_key = row["match_key"] or match_key(row["message_text"])
        if len(row_key) < FUZZY_MIN_KEY_LENGTH or len(key) < FUZZY_MIN_KEY_LENGTH:
            continue
        # Word overlap is linear and rules out unrelated rows before the quadratic ratio().
        row_words = _words(row["message_text"])
        if len(new_words & row_words) < FUZZY_MIN_WORD_OVERLAP * len(new_words | row_words):
            continue
        matcher = difflib.SequenceMatcher(None, key, row_key, autojunk=False)
        if matcher.real_quick_ratio() >= FUZZY_MIN_RATIO and matcher.ratio() >= FUZZY_MIN_RATIO:
            return row, "fuzzy"
    return None, None


def _resolve_conflict(conn: sqlite3.Connection, row: sqlite3.Row, message: MessageIn) -> None:
    if message.source == "clipboard" and row["source"] == "ocr":
        new_source, new_confidence = "clipboard", None
    elif (
        message.source == "ocr"
        and row["source"] == "ocr"
        and (message.confidence or 0) > (row["confidence"] or 0)
    ):
        new_source, new_confidence = "ocr", message.confidence
    else:
        return
    try:
        conn.execute(
            """
            UPDATE messages
            SET message_text = ?, source = ?, confidence = ?, content_hash = ?, match_key = ?
            WHERE id = ?
            """,
            (
                message.message_text,
                new_source,
                new_confidence,
                text_hash(row["chat_id"], row["sender"], row["timestamp"], message.message_text),
                match_key(message.message_text),
                row["id"],
            ),
        )
    except sqlite3.IntegrityError:
        # Another row already holds this exact text (content_hash is unique), so leave this row as is.
        pass


def sources_for(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[str]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" * len(message_ids))
    result: dict[int, list[str]] = {message_id: [] for message_id in message_ids}
    for item in conn.execute(
        f"SELECT DISTINCT message_id, source FROM message_variants "
        f"WHERE message_id IN ({placeholders}) ORDER BY source",
        message_ids,
    ):
        result[item["message_id"]].append(item["source"])
    return result


def store_message(conn: sqlite3.Connection, message: MessageIn) -> tuple[dict, Optional[str]]:
    """Store or merge a message; returns (stored row, match).

    match is "exact", "normalised", "fuzzy" or None (a new row was inserted).
    """
    content_hash = message.content_hash()
    key = match_key(message.message_text)
    row, match = _find_match(conn, message, content_hash, key)
    if row is None:
        try:
            cursor = conn.execute(
                """
                INSERT INTO messages
                    (chat_id, message_text, sender, timestamp, source, confidence, content_hash, match_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat_id,
                    message.message_text,
                    message.sender,
                    message.timestamp,
                    message.source,
                    message.confidence,
                    content_hash,
                    key,
                ),
            )
        except sqlite3.IntegrityError:
            # A concurrent request stored the same exact text after our lookup: treat it as an exact match.
            row = conn.execute("SELECT * FROM messages WHERE content_hash = ?", (content_hash,)).fetchone()
            match = "exact"
    if row is None:
        message_id = cursor.lastrowid
    else:
        message_id = row["id"]
        _resolve_conflict(conn, row, message)

    conn.execute(
        """
        INSERT OR IGNORE INTO message_variants (message_id, source, message_text, confidence)
        VALUES (?, ?, ?, ?)
        """,
        (message_id, message.source, message.message_text, message.confidence),
    )
    stored = dict(conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone())
    stored["sources"] = sources_for(conn, [message_id])[message_id]
    return stored, match
