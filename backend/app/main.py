import os
import threading
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError

from app.db import connect, init_db
from app.ocr import decode_image, group_lines
from app.pipeline import MessageIn, sources_for, store_message

DEFAULT_DB_PATH = os.environ.get("SECRETARY_DB_PATH", "data/secretary.db")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# The real OCR engine is not thread-safe, and endpoints run in a threadpool.
OCR_LOCK = threading.Lock()


def create_app(db_path: str = DEFAULT_DB_PATH, ocr_engine=None) -> FastAPI:
    init_db(db_path)
    app = FastAPI(title="Secretary Messenger Monitor", version="0.1.0")
    app.state.db_path = db_path
    app.state.ocr_engine = ocr_engine

    def get_engine():
        with OCR_LOCK:
            if app.state.ocr_engine is None:
                try:
                    from app.ocr_paddle import PaddleEngine
                except ImportError:
                    raise HTTPException(503, "OCR engine not installed; see README")
                app.state.ocr_engine = PaddleEngine()
            return app.state.ocr_engine

    @app.get("/health")
    def health():
        with connect(app.state.db_path) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/messages", status_code=201)
    def create_message(message: MessageIn, response: Response):
        with connect(app.state.db_path) as conn:
            row, match = store_message(conn, message)
        if match is not None:
            response.status_code = 200
        return {**row, "duplicate": match is not None, "match": match}

    @app.post("/ocr/screenshots")
    def ocr_screenshot(
        file: UploadFile = File(...),
        chat_id: Optional[str] = Form(None, max_length=200),
    ):
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Screenshot is larger than 10 MB")
        try:
            image = decode_image(data)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        engine = get_engine()
        with OCR_LOCK:
            lines = engine.read(image)
        messages, skipped = group_lines(lines, image)

        stored, rejected = [], []
        with connect(app.state.db_path) as conn:
            for item in messages:
                try:
                    message = MessageIn(
                        message_text=item.text,
                        source="ocr",
                        chat_id=chat_id,
                        confidence=round(item.confidence, 4),
                    )
                except ValidationError:
                    rejected.append(item.text)
                    continue
                row, match = store_message(conn, message)
                stored.append(
                    {**row, "duplicate": match is not None, "match": match, "side": item.side}
                )
        return {
            "messages": stored,
            "skipped_lines": [line.text for line in skipped],
            "rejected": rejected,
        }

    @app.get("/messages")
    def list_messages(limit: int = Query(default=50, ge=1, le=500)):
        with connect(app.state.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            sources = sources_for(conn, [row["id"] for row in rows])
        return [{**dict(row), "sources": sources[row["id"]]} for row in rows]

    @app.get("/messages/{message_id}/variants")
    def list_variants(message_id: int):
        with connect(app.state.db_path) as conn:
            if conn.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone() is None:
                raise HTTPException(404, "Message not found")
            rows = conn.execute(
                "SELECT id, source, message_text, confidence, created_at "
                "FROM message_variants WHERE message_id = ? ORDER BY id",
                (message_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    return app


app = create_app()
