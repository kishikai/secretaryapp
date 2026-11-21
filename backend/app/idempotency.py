"""Idempotency-Key support: a retried POST returns the first response instead of running again.

A pure ASGI middleware. For a POST outside /auth/ that carries an Idempotency-Key header and
comes from an authenticated caller, it claims an "in progress" row (UNIQUE scope + key), runs
the route, and stores the response when the status is 2xx or 4xx (not 401, 403 or 429). A
repeat with the same scope, key, method, path and body hash replays the stored response.
"""
import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import auth
from app.db import connect

KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
RETENTION = timedelta(hours=24)
# A row still "in progress" after this long belongs to a request that died (crash, restart);
# without this limit its key would answer 409 for the whole retention period.
IN_PROGRESS_LIMIT = timedelta(minutes=10)
MAX_STORED_BODY_BYTES = 1024 * 1024
NOT_STORED_STATUSES = {401, 403, 429}
REPLAY_HEADER = "Idempotent-Replayed"

RUN, MISMATCH, BUSY, REPLAY = "run", "mismatch", "busy", "replay"


def storable(status: int) -> bool:
    return (200 <= status < 300 or 400 <= status < 500) and status not in NOT_STORED_STATUSES


def claim(db_path: str, scope: str, key: str, method: str, path: str, body_hash: str):
    """Insert the in-progress row, or say what the existing row means: (outcome, row)."""
    now = datetime.now(timezone.utc)
    with connect(db_path) as conn:
        # Opportunistic purge; it also opens the write transaction, so the INSERT and the
        # SELECT below cannot race with another request's purge or finish.
        conn.execute("DELETE FROM idempotency_keys WHERE created_at <= ?", (auth.stamp(now - RETENTION),))
        conn.execute(
            "DELETE FROM idempotency_keys WHERE status IS NULL AND created_at <= ?",
            (auth.stamp(now - IN_PROGRESS_LIMIT),),
        )
        try:
            conn.execute(
                "INSERT INTO idempotency_keys (scope, key, method, path, body_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (scope, key, method, path, body_hash, auth.stamp(now)),
            )
            return RUN, None
        except sqlite3.IntegrityError:
            pass
        row = conn.execute(
            "SELECT method, path, body_hash, status, content_type, body FROM idempotency_keys "
            "WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
    if row is None:  # cannot happen inside the transaction; run rather than fail
        return RUN, None
    if (row["method"], row["path"], row["body_hash"]) != (method, path, body_hash):
        return MISMATCH, None
    if row["status"] is None:
        return BUSY, None
    return REPLAY, row


def finish(db_path: str, scope: str, key: str, status: int, content_type: Optional[str], body: bytes) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE idempotency_keys SET status = ?, content_type = ?, body = ? "
            "WHERE scope = ? AND key = ? AND status IS NULL",
            (status, content_type, body, scope, key),
        )


def release(db_path: str, scope: str, key: str) -> None:
    """Forget an unfinished request so a retry can run."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM idempotency_keys WHERE scope = ? AND key = ? AND status IS NULL", (scope, key))


def scope_of(db_path: str, request: Request) -> Optional[str]:
    with connect(db_path) as conn:
        return auth.caller_scope(conn, request)


def replaying_receive(messages: list[Message], receive: Receive) -> Receive:
    """Hand the app the messages already read, then whatever the client sends next."""
    pending = list(messages)

    async def replay() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return replay


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].startswith("/auth/"):
            await self.app(scope, receive, send)
            return
        headers = [value for name, value in scope["headers"] if name == b"idempotency-key"]
        if not headers:
            await self.app(scope, receive, send)
            return
        if len(headers) != 1 or not KEY_PATTERN.fullmatch(headers[0].decode("latin-1")):
            await JSONResponse({"detail": "Invalid Idempotency-Key"}, status_code=400)(scope, receive, send)
            return
        key = headers[0].decode("latin-1")

        for name, value in scope["headers"]:
            if name == b"content-length" and (not value.isdigit() or int(value) > self.max_body_bytes):
                await self.app(scope, receive, send)  # too big to hash safely (or malformed)
                return

        db_path = scope["app"].state.db_path
        caller = await run_in_threadpool(scope_of, db_path, Request(scope))
        if caller is None:
            await self.app(scope, receive, send)  # not authenticated: the route answers 401/403
            return

        messages: list[Message] = []
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":  # the client went away
                await self.app(scope, replaying_receive(messages, receive), send)
                return
            chunks.append(message.get("body", b""))
            size += len(chunks[-1])
            if size > self.max_body_bytes:
                await self.app(scope, replaying_receive(messages, receive), send)
                return
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        path = scope["path"]
        if scope.get("query_string"):
            path += "?" + scope["query_string"].decode("latin-1")
        body_hash = hashlib.sha256(body).hexdigest()
        outcome, row = await run_in_threadpool(claim, db_path, caller, key, "POST", path, body_hash)

        if outcome == MISMATCH:
            response: Response = JSONResponse(
                {"detail": "This Idempotency-Key was already used for a different request"}, status_code=422
            )
        elif outcome == BUSY:
            response = JSONResponse(
                {"detail": "A request with this Idempotency-Key is still in progress"}, status_code=409
            )
        elif outcome == REPLAY:
            response = Response(
                content=row["body"],
                status_code=row["status"],
                headers={REPLAY_HEADER: "true"},
            )
            if row["content_type"]:
                response.headers["content-type"] = row["content_type"]
        else:
            await self.run_and_store(scope, body, receive, send, db_path, caller, key)
            return
        await response(scope, receive, send)

    async def run_and_store(
        self, scope: Scope, body: bytes, receive: Receive, send: Send, db_path: str, caller: str, key: str
    ) -> None:
        resolved = False  # True once the row is finished or released
        tracking = False
        status = 0
        content_type: Optional[str] = None
        collected = bytearray()

        async def resolve(store: bool) -> None:
            nonlocal resolved
            if store:
                await run_in_threadpool(finish, db_path, caller, key, status, content_type, bytes(collected))
            else:
                await run_in_threadpool(release, db_path, caller, key)
            resolved = True

        async def sending(message: Message) -> None:
            nonlocal tracking, status, content_type
            if message["type"] == "http.response.start":
                status = message["status"]
                tracking = storable(status)
                for name, value in message.get("headers", []):
                    if name.lower() == b"content-type":
                        content_type = value.decode("latin-1")
                if not tracking:
                    await resolve(store=False)
            elif message["type"] == "http.response.body" and tracking:
                collected.extend(message.get("body", b""))
                if len(collected) > MAX_STORED_BODY_BYTES:
                    tracking = False
                    collected.clear()
                    await resolve(store=False)
                elif not message.get("more_body", False):
                    # Finished before the last chunk goes out, so a retry sent right after
                    # the client has the response finds the stored row, not "in progress".
                    await resolve(store=True)
            await send(message)

        sent = False

        async def request_receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, request_receive, sending)
        finally:
            if not resolved:  # raised, or never sent a complete response
                try:
                    release(db_path, caller, key)
                except sqlite3.Error:
                    pass
