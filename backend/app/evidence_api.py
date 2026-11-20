"""Message evidence review (Phase 5). Secretary only (D10).

A message is only evidence. Suggestions are worked out when the queue is read and are never
written anywhere; a register changes only when a secretary approves, through the same audited
path as the register screen. A finalised register is never changed: it must be reopened first.
"""
import sqlite3
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.attendance_api import check_recordable, locked_event, set_status
from app.db import connect
from app.members_api import blank_to_none, name_key, require_secretary

router = APIRouter(dependencies=[Depends(require_secretary)])

SCANNED_MESSAGES = 2000  # shortcut: only the newest messages are scanned for the queue; add paging if it matters
LISTED = 200
EVENT_WINDOW = (-1, 7)  # days around the message's date where its event may fall
# First match wins, so "sorry, running late" is late rather than an excused absence.
STATUS_HINTS = (
    ("late", ("running late", "be late", "bit late", "minutes late", "late today", "late tonight", "stuck in traffic")),
    ("absent_excused", ("apolog", "can't make", "cant make", "cannot make", "can't come", "cant come", "won't make",
                 "wont make", "won't be able", "unable to", "not able to", "unwell", "sick", "poorly")),
    ("present", ("i'm here", "im here", "i am here", "just arrived", "i've arrived")),
)
SQL_PREFILTER = " OR ".join(["lower(replace(message_text, '’', '''')) LIKE ?"] * sum(len(p) for _, p in STATUS_HINTS))
SQL_PREFILTER_PARAMS = [f"%{phrase}%" for _, phrases in STATUS_HINTS for phrase in phrases]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: int
    event_id: int
    status: Literal["present", "late", "late_excused", "absent", "absent_excused"]
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def clean(cls, value):
        return blank_to_none(value)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def clean(cls, value):
        return blank_to_none(value)


def status_hint(text: str) -> tuple[Optional[str], Optional[str]]:
    lowered = text.casefold().replace("’", "'")
    for status, phrases in STATUS_HINTS:
        for phrase in phrases:
            if phrase in lowered:
                return status, phrase
    return None, None


def suggest(message: sqlite3.Row, members: list[sqlite3.Row], events: list[sqlite3.Row], expected: set) -> dict:
    """Who, which event and which status the message probably means, with the reasons why."""
    why = []
    status, phrase = status_hint(message["message_text"])
    if status:
        why.append(f'Mentions "{phrase}"')

    member = None
    sender = name_key(message["sender"])
    if sender:
        exact = [m for m in members if name_key(m["full_name"]) == sender]
        first = [m for m in members if name_key(m["full_name"]).split(" ")[0] == sender.split(" ")[0]]
        if len(exact) == 1:
            member = exact[0]
            why.append(f"Sender name matches {member['full_name']}")
        elif len(first) == 1:
            member = first[0]
            why.append(f"Sender first name matches {member['full_name']} (the only member called that)")

    day = date.fromisoformat(message["created_at"][:10])
    low, high = (str(day + timedelta(days=d)) for d in EVENT_WINDOW)
    nearby = [e for e in events if low <= e["starts_at"][:10] <= high]
    event = None
    if member is not None:
        event = next((e for e in nearby if (e["id"], member["id"]) in expected), None)
        if event:
            why.append(f"{member['full_name']} is expected at {event['name']}, the nearest event")
    if event is None and nearby:
        event = nearby[0]
        why.append(f"{event['name']} is the nearest event to the message")
    return {
        "member_id": member["id"] if member else None,
        "member_name": member["full_name"] if member else None,
        "event_id": event["id"] if event else None,
        "event_name": event["name"] if event else None,
        "event_starts_at": event["starts_at"] if event else None,
        "status": status,
        "why": why,
    }


def build_items(conn: sqlite3.Connection, messages: list[sqlite3.Row]) -> list[dict]:
    members = conn.execute("SELECT id, full_name FROM members WHERE active = 1 ORDER BY id").fetchall()
    events = conn.execute(
        "SELECT id, name, starts_at FROM events WHERE status IN ('scheduled', 'completed') ORDER BY starts_at, id"
    ).fetchall()
    expected = {(r[0], r[1]) for r in conn.execute("SELECT event_id, member_id FROM event_attendees")}
    reviews = {
        r["message_id"]: r
        for r in conn.execute(
            "SELECT r.*, m.full_name AS member_name, e.name AS event_name FROM evidence_reviews r "
            "LEFT JOIN members m ON m.id = r.member_id LEFT JOIN events e ON e.id = r.event_id"
        )
    }
    items = []
    for message in messages:
        review = reviews.get(message["id"])
        items.append({
            "message_id": message["id"],
            "message_text": message["message_text"],
            "sender": message["sender"],
            "chat_id": message["chat_id"],
            "source": message["source"],
            "confidence": message["confidence"],
            "received_at": message["created_at"],
            "state": review["state"] if review else "pending",
            "suggestion": suggest(message, members, events, expected),
            "review": None if review is None else {
                key: review[key] for key in
                ("member_id", "member_name", "event_id", "event_name", "status", "note", "reviewed_at")
            } | {"reviewed_by": review["reviewer"], "reviewed_text": review["message_text"]},
        })
    return items


MESSAGE_COLUMNS = "id, message_text, sender, chat_id, source, confidence, created_at"


@router.get("/evidence")
def evidence_queue(request: Request, state: Literal["pending", "approved", "rejected"] = "pending"):
    with connect(request.app.state.db_path) as conn:
        if state == "pending":
            messages = conn.execute(
                f"SELECT {MESSAGE_COLUMNS} FROM (SELECT * FROM messages ORDER BY id DESC LIMIT ?) "
                f"WHERE id NOT IN (SELECT message_id FROM evidence_reviews) AND ({SQL_PREFILTER}) ORDER BY id DESC",
                (SCANNED_MESSAGES, *SQL_PREFILTER_PARAMS),
            ).fetchall()
        else:
            messages = conn.execute(
                f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE id IN "
                "(SELECT message_id FROM evidence_reviews WHERE state = ?) ORDER BY id DESC",
                (state,),
            ).fetchall()
        return build_items(conn, messages[:LISTED])


def unreviewed_message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    message = conn.execute(f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE id = ?", (message_id,)).fetchone()
    if message is None:
        raise HTTPException(404, "Message not found")
    if conn.execute("SELECT 1 FROM evidence_reviews WHERE message_id = ?", (message_id,)).fetchone():
        raise HTTPException(409, "This message has already been reviewed")
    return message


@router.post("/evidence/{message_id}/approve")
def approve(message_id: int, body: ApproveIn, request: Request, user=Depends(require_secretary)):
    with connect(request.app.state.db_path) as conn:
        event = locked_event(conn, body.event_id)  # takes the write lock before any check
        message = unreviewed_message(conn, message_id)
        check_recordable(event)
        row = conn.execute(
            "SELECT status FROM event_attendees WHERE event_id = ? AND member_id = ?", (body.event_id, body.member_id)
        ).fetchone()
        if row is None:
            raise HTTPException(422, "That person isn't on this event's expected list")
        reason = body.note or f"From message #{message_id}"
        set_status(conn, body.event_id, body.member_id, row["status"], body.status, user, reason, message_id)
        conn.execute(
            "INSERT INTO evidence_reviews (message_id, state, message_text, member_id, event_id, status, note, "
            "reviewer_id, reviewer) VALUES (?, 'approved', ?, ?, ?, ?, ?, ?, ?)",
            (message_id, message["message_text"], body.member_id, body.event_id, body.status, body.note,
             user["id"], user["username"]),
        )
        return build_items(conn, [message])[0]


@router.post("/evidence/{message_id}/reject")
def reject(message_id: int, body: RejectIn, request: Request, user=Depends(require_secretary)):
    with connect(request.app.state.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        message = unreviewed_message(conn, message_id)
        conn.execute(
            "INSERT INTO evidence_reviews (message_id, state, message_text, note, reviewer_id, reviewer) "
            "VALUES (?, 'rejected', ?, ?, ?, ?)",
            (message_id, message["message_text"], body.note, user["id"], user["username"]),
        )
        return build_items(conn, [message])[0]
