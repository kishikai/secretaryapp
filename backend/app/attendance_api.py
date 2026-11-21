"""Attendance registers: record, finalise, reopen, and the audit trail (decisions D7, D8, D10)."""
import sqlite3
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import require_user
from app.db import connect
from app.events_api import get_event
from app.members_api import blank_to_none, require_editor, require_secretary

router = APIRouter()

Status = Literal["not_recorded", "present", "late", "late_excused", "absent", "absent_excused", "no_practice"]
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: int
    status: Status


class AttendanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[Record] = Field(min_length=1, max_length=5000)
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def clean(cls, value):
        return blank_to_none(value)


class ReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def clean(cls, value):
        value = blank_to_none(value)
        if value is None:
            raise ValueError("say why the register is being reopened")
        return value


def locked_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    """Take the write lock first, so the checks below and the writes that follow can't interleave
    with another request's; then load the event (404 if missing)."""
    conn.execute("BEGIN IMMEDIATE")
    event = conn.execute("SELECT id, status, finalized_at FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def audit(conn, event_id, user, action, member_id=None, old=None, new=None, reason=None, evidence_id=None) -> None:
    conn.execute(
        "INSERT INTO attendance_audit "
        "(event_id, member_id, action, old_status, new_status, reason, actor_id, actor, evidence_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, member_id, action, old, new, reason, user["id"], user["username"], evidence_id),
    )


def check_recordable(event: sqlite3.Row) -> None:
    if event["status"] not in ("scheduled", "completed"):
        raise HTTPException(409, f"Attendance can't be taken for a {event['status']} event")
    if event["finalized_at"]:
        raise HTTPException(409, "The register is finalised; a secretary must reopen it first")


def set_status(conn, event_id, member_id, old, new, user, reason=None, evidence_id=None) -> None:
    """Write one person's status and its audit row; callers have already run check_recordable."""
    if old == new:
        return
    conn.execute(
        f"UPDATE event_attendees SET status = ?, recorded_at = {NOW}, recorded_by = ? "
        "WHERE event_id = ? AND member_id = ?",
        (new, user["id"], event_id, member_id),
    )
    audit(conn, event_id, user, "record", member_id, old, new, reason, evidence_id)


@router.put("/events/{event_id}/attendance")
def record_attendance(event_id: int, body: AttendanceIn, request: Request, user=Depends(require_editor)):
    with connect(request.app.state.db_path) as conn:
        event = locked_event(conn, event_id)
        check_recordable(event)
        current = dict(conn.execute(
            "SELECT member_id, status FROM event_attendees WHERE event_id = ?", (event_id,)
        ).fetchall())
        missing = sorted({r.member_id for r in body.records if r.member_id not in current})
        if missing:
            raise HTTPException(422, f"Not on this event's expected list: member id {', '.join(map(str, missing))}")
        wanted = {r.member_id: r.status for r in body.records}  # a repeated id: the last one wins
        for member_id, status in wanted.items():
            set_status(conn, event_id, member_id, current[member_id], status, user, body.reason)
        return get_event(conn, event_id)


@router.post("/events/{event_id}/finalize")
def finalize(event_id: int, request: Request, user=Depends(require_secretary)):
    with connect(request.app.state.db_path) as conn:
        event = locked_event(conn, event_id)
        if event["status"] not in ("scheduled", "completed"):
            raise HTTPException(409, f"A {event['status']} event's register can't be finalised")
        if event["finalized_at"]:
            raise HTTPException(409, "The register is already finalised")
        expected, pending = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status = 'not_recorded'), 0) FROM event_attendees WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if expected == 0:
            raise HTTPException(409, "No one is on the expected list")
        if pending:
            raise HTTPException(409, f"{pending} {'person is' if pending == 1 else 'people are'} not recorded yet")
        # Finalising also completes a scheduled event.
        conn.execute(
            f"UPDATE events SET finalized_at = {NOW}, finalized_by = ?, status = 'completed', "
            f"updated_at = {NOW} WHERE id = ?",
            (user["id"], event_id),
        )
        audit(conn, event_id, user, "finalize")
        return get_event(conn, event_id)


@router.post("/events/{event_id}/reopen")
def reopen(event_id: int, body: ReopenIn, request: Request, user=Depends(require_secretary)):
    with connect(request.app.state.db_path) as conn:
        event = locked_event(conn, event_id)
        if not event["finalized_at"]:
            raise HTTPException(409, "The register isn't finalised")
        conn.execute(
            f"UPDATE events SET finalized_at = NULL, finalized_by = NULL, updated_at = {NOW} WHERE id = ?",
            (event_id,),
        )
        audit(conn, event_id, user, "reopen", reason=body.reason)
        return get_event(conn, event_id)


@router.get("/events/{event_id}/audit", dependencies=[Depends(require_user)])
def audit_trail(event_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
            raise HTTPException(404, "Event not found")
        rows = conn.execute(
            "SELECT a.id, a.at, a.actor, a.action, a.member_id, m.full_name, a.old_status, a.new_status, a.reason, "
            "a.evidence_id "
            "FROM attendance_audit a LEFT JOIN members m ON m.id = a.member_id "
            "WHERE a.event_id = ? ORDER BY a.id DESC",
            (event_id,),
        ).fetchall()
    return [dict(row) for row in rows]
