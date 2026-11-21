import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.auth import LoginRateLimiter, require_admin, require_ingest
from app.auth_api import router as auth_router
from app.attendance_api import router as attendance_router
from app.collector_api import router as collector_router
from app.db import connect, init_db
from app.evidence_api import router as evidence_router
from app.idempotency import IdempotencyMiddleware
from app.exports_api import router as exports_router
from app.reports_api import router as reports_router
from app.events_api import router as events_router
from app.members_api import router as members_router
from app.ocr import decode_image, group_lines
from app.pipeline import MessageIn, sources_for, store_message
from app.rules import categories_for
from app.rules_api import router as rules_router

DEFAULT_DB_PATH = os.environ.get("SECRETARY_DB_PATH", "data/secretary.db")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_SEARCH_TOKENS = 20
STATS_DAYS = 14
DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}
# The real OCR engine is not thread-safe, and endpoints run in a threadpool.
OCR_LOCK = threading.Lock()


def fts_match_expression(q: str) -> str:
    """Quote every word as a prefix term so user input can never be FTS syntax."""
    tokens = re.findall(r"\w+", q)
    if not tokens:
        raise HTTPException(422, "Search needs at least one letter or digit")
    return " ".join(f'"{token}"*' for token in tokens[:MAX_SEARCH_TOKENS])


def stored_timestamp(value: datetime) -> str:
    """Format like messages.created_at (UTC, millisecond precision); naive means UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def create_app(db_path: str = DEFAULT_DB_PATH, ocr_engine=None) -> FastAPI:
    init_db(db_path)
    # No interactive docs or schema: they would be public routes, and the CSP blocks their CDN assets.
    app = FastAPI(
        title="Secretary Messenger Monitor",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db_path = db_path
    app.state.ocr_engine = ocr_engine
    app.state.login_limiter = LoginRateLimiter()

    # Added first, so it sits inside the security-headers middleware below and replayed
    # responses get the headers too. Bodies above the upload limit (plus multipart framing) skip it.
    app.add_middleware(IdempotencyMiddleware, max_body_bytes=MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    app.include_router(auth_router)
    app.include_router(rules_router, dependencies=[Depends(require_admin)])
    app.include_router(members_router)  # each route declares its own role
    app.include_router(attendance_router)
    app.include_router(reports_router)  # any signed-in role
    app.include_router(evidence_router)  # secretary only
    app.include_router(exports_router)  # any signed-in role
    app.include_router(collector_router)  # heartbeat: ingest token; status: secretary
    app.include_router(events_router)  # likewise

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

    @app.post("/messages", status_code=201, dependencies=[Depends(require_ingest)])
    def create_message(message: MessageIn, response: Response):
        with connect(app.state.db_path) as conn:
            row, match = store_message(conn, message)
        if match is not None:
            response.status_code = 200
        return {**row, "duplicate": match is not None, "match": match}

    @app.post("/ocr/screenshots", dependencies=[Depends(require_ingest)])
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

    @app.get("/messages", dependencies=[Depends(require_admin)])
    def list_messages(
        limit: int = Query(default=50, ge=1, le=500),
        q: Optional[str] = Query(default=None, min_length=1, max_length=200),
        chat_id: Optional[str] = Query(default=None, max_length=200),
        sender: Optional[str] = Query(default=None, max_length=200),
        source: Optional[Literal["clipboard", "ocr"]] = None,
        category: Optional[str] = Query(default=None, max_length=50),
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        before_id: Optional[int] = Query(default=None, ge=1),
    ):
        # Only fixed SQL fragments go into the query; every value is a ? parameter.
        conditions: list[str] = []
        params: list = []
        if q is not None:
            conditions.append("id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
            params.append(fts_match_expression(q))
        if chat_id is not None:
            conditions.append("chat_id = ?")
            params.append(chat_id)
        if sender is not None:
            conditions.append("sender = ?")
            params.append(sender)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        if category is not None:
            conditions.append(
                "id IN (SELECT m.message_id FROM message_matches m "
                "JOIN rules r ON r.id = m.rule_id WHERE r.category = ?)"
            )
            params.append(category)
        if since is not None and until is not None and stored_timestamp(since) >= stored_timestamp(until):
            raise HTTPException(422, "since must be earlier than until")
        if since is not None:
            conditions.append("created_at >= ?")
            params.append(stored_timestamp(since))
        if until is not None:
            conditions.append("created_at < ?")
            params.append(stored_timestamp(until))
        if before_id is not None:
            conditions.append("id < ?")
            params.append(before_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with connect(app.state.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM messages" + where + " ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            sources = sources_for(conn, ids)
            categories = categories_for(conn, ids)
        return [
            {**dict(row), "sources": sources[row["id"]], "categories": categories[row["id"]]}
            for row in rows
        ]

    @app.get("/messages/{message_id}/variants", dependencies=[Depends(require_admin)])
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

    @app.get("/stats", dependencies=[Depends(require_admin)])
    def stats():
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=offset) for offset in range(STATS_DAYS - 1, -1, -1)]
        with connect(app.state.db_path) as conn:
            total, latest = conn.execute("SELECT COUNT(*), MAX(created_at) FROM messages").fetchone()
            by_source = {"clipboard": 0, "ocr": 0}
            for row in conn.execute("SELECT source, COUNT(*) AS n FROM messages GROUP BY source"):
                by_source[row["source"]] = row["n"]
            by_category = [
                {"category": row["category"], "count": row["n"]}
                for row in conn.execute(
                    "SELECT r.category AS category, COUNT(DISTINCT m.message_id) AS n "
                    "FROM message_matches m JOIN rules r ON r.id = m.rule_id "
                    "WHERE r.category IS NOT NULL GROUP BY r.category ORDER BY n DESC, r.category"
                )
            ]
            per_day_counts = {
                row["day"]: row["n"]
                for row in conn.execute(
                    "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n FROM messages "
                    "WHERE created_at >= ? GROUP BY day",
                    (days[0].isoformat(),),
                )
            }
            unread = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
            ).fetchone()[0]
            enabled = conn.execute("SELECT COUNT(*) FROM rules WHERE enabled = 1").fetchone()[0]
        return {
            "total_messages": total,
            "by_source": by_source,
            "by_category": by_category,
            "per_day": [
                {"day": day.isoformat(), "count": per_day_counts.get(day.isoformat(), 0)}
                for day in days
            ],
            "unread_notifications": unread,
            "enabled_rules": enabled,
            "latest_message_at": latest,
        }

    @app.get("/chats", dependencies=[Depends(require_admin)])
    def list_chats():
        with connect(app.state.db_path) as conn:
            # Grouping on the (chat_id, id) index avoids reading every row; ids grow with
            # created_at, so the newest id gives each chat's latest time.
            rows = conn.execute(
                "SELECT g.chat_id, g.count, m.created_at AS latest_at FROM "
                "(SELECT chat_id, COUNT(*) AS count, MAX(id) AS last_id FROM messages GROUP BY chat_id) g "
                "JOIN messages m ON m.id = g.last_id ORDER BY latest_at DESC, g.last_id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # Mounted last so every API route above wins over the static files.
    dist = Path(os.environ.get("SECRETARY_FRONTEND_DIST") or DEFAULT_FRONTEND_DIST)
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()
