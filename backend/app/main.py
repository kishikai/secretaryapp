import hashlib
import os
import unicodedata
from typing import Literal, Optional

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.db import connect, init_db

DEFAULT_DB_PATH = os.environ.get("SECRETARY_DB_PATH", "data/secretary.db")


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
        key = "\x1f".join([self.chat_id or "", self.sender or "", self.timestamp or "", self.message_text])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_app(db_path: str = DEFAULT_DB_PATH) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="Secretary Messenger Monitor", version="0.1.0")
    app.state.db_path = db_path

    @app.get("/health")
    def health():
        with connect(app.state.db_path) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/messages", status_code=201)
    def create_message(message: MessageIn, response: Response):
        content_hash = message.content_hash()
        with connect(app.state.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (chat_id, message_text, sender, timestamp, source, confidence, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat_id,
                    message.message_text,
                    message.sender,
                    message.timestamp,
                    message.source,
                    message.confidence,
                    content_hash,
                ),
            )
            if cursor.rowcount == 0:
                response.status_code = 200
            row = conn.execute(
                "SELECT * FROM messages WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return {**dict(row), "duplicate": cursor.rowcount == 0}

    @app.get("/messages")
    def list_messages(limit: int = Query(default=50, ge=1, le=500)):
        with connect(app.state.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    return app


app = create_app()
