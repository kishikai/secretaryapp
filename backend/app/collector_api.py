"""Collector heartbeat (Phase 7): shows whether the message collector is running.

Purely informational: nothing in attendance depends on it (change plan principle 3).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import SIGN_IN_REQUIRED, _bearer_token, _valid_ingest_token, require_admin, stamp
from app.db import connect

router = APIRouter(prefix="/collector")

OFFLINE_AFTER = timedelta(minutes=5)


@router.post("/heartbeat", status_code=204)
def heartbeat(request: Request):
    """An ingest token says "still running"; checking it records last_used_at."""
    token = _bearer_token(request)
    with connect(request.app.state.db_path) as conn:
        if token is None or not _valid_ingest_token(conn, token):
            raise HTTPException(401, SIGN_IN_REQUIRED)
    return Response(status_code=204)


@router.get("/status", dependencies=[Depends(require_admin)])
def status(request: Request):
    cutoff = stamp(datetime.now(timezone.utc) - OFFLINE_AFTER)
    with connect(request.app.state.db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, last_used_at FROM api_tokens WHERE scope = 'ingest' AND revoked_at IS NULL ORDER BY id"
        ).fetchall()
    collectors = [
        {"id": r["id"], "name": r["name"], "last_seen": r["last_used_at"],
         "online": r["last_used_at"] is not None and r["last_used_at"] >= cutoff}
        for r in rows
    ]
    return {
        "online": any(c["online"] for c in collectors),
        "collectors": collectors,
        "offline_after_minutes": int(OFFLINE_AFTER.total_seconds() // 60),
    }
