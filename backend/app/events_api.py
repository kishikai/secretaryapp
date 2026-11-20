import re
import sqlite3
from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.auth import require_user
from app.db import connect
from app.members_api import ISO_DATE, blank_to_none, member_ids_by_group, require_editor, require_secretary

router = APIRouter()

MAX_LISTED_EVENTS = 1000  # shortcut: no paging yet; the list is capped
MAX_ATTENDEE_IDS = 5000
EVENT_FIELDS = ("name", "starts_at", "ends_at", "location", "category", "notes")
LOCAL_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
Status = Literal["draft", "scheduled", "completed", "cancelled"]
# D5: the only status changes that are allowed.
TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
LOCKED = {"completed", "cancelled"}


class EventFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    starts_at: str
    ends_at: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: Any) -> Any:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("a name is required")
        return value

    @field_validator("starts_at", mode="before")
    @classmethod
    def clean_starts_at(cls, value: Any) -> Any:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("a start time is required")
        return value

    @field_validator("ends_at", "location", "category", "notes", mode="before")
    @classmethod
    def clean_optional(cls, value: Any) -> Any:
        return blank_to_none(value)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def check_datetime(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            try:
                if not LOCAL_DATETIME.fullmatch(value):
                    raise ValueError
                datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("must be a date and time like 2024-03-31T18:30") from None
        return value

    @field_validator("ends_at")
    @classmethod
    def check_order(cls, value: Optional[str], info) -> Optional[str]:
        starts_at = info.data.get("starts_at")
        if value is not None and starts_at is not None and value < starts_at:
            raise ValueError("must not be before the start time")
        return value


class EventIn(EventFields):
    status: Literal["draft", "scheduled"] = "draft"  # new events start as draft or scheduled


class EventState(EventFields):
    """An existing event with any status; what a PATCH is validated as."""

    status: Status


class AttendeesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_ids: list[int] = Field(default_factory=list, max_length=MAX_ATTENDEE_IDS)
    group_ids: list[int] = Field(default_factory=list, max_length=MAX_ATTENDEE_IDS)

    @model_validator(mode="after")
    def need_something(self) -> "AttendeesIn":
        if not self.member_ids and not self.group_ids:
            raise ValueError("choose at least one member or group")
        return self


# ------------------------------------------------------------------ helpers


EVENT_SELECT = (
    "SELECT e.*, (SELECT COUNT(*) FROM event_attendees a WHERE a.event_id = e.id) AS expected_count "
    "FROM events e"
)


def event_out(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "name", "starts_at", "ends_at", "location", "category",
                                      "notes", "status", "expected_count")}


def get_event(conn: sqlite3.Connection, event_id: int) -> dict:
    """The event with its expected attendees, by name."""
    row = conn.execute(EVENT_SELECT + " WHERE e.id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Event not found")
    groups = member_ids_by_group(conn)
    attendees = [
        {
            "member_id": r["id"],
            "full_name": r["full_name"],
            "email": r["email"],
            "phone": r["phone"],
            "active": bool(r["active"]),
            "group_ids": groups.get(r["id"], []),
            "status": r["status"],
            "recorded_at": r["recorded_at"],
            "recorded_by": r["recorded_by"],
        }
        for r in conn.execute(
            "SELECT m.id, m.full_name, m.email, m.phone, m.active, a.status, a.recorded_at, "
            "u.username AS recorded_by FROM event_attendees a "
            "JOIN members m ON m.id = a.member_id LEFT JOIN admins u ON u.id = a.recorded_by "
            "WHERE a.event_id = ? ORDER BY m.full_name COLLATE NOCASE, m.id",
            (event_id,),
        )
    ]
    finalized_by = conn.execute(
        "SELECT username FROM admins WHERE id = ?", (row["finalized_by"],)
    ).fetchone()
    return {
        **event_out(row),
        "finalized_at": row["finalized_at"],
        "finalized_by": finalized_by[0] if finalized_by else None,
        "attendees": attendees,
    }


def require_unlocked(conn: sqlite3.Connection, event_id: int) -> None:
    """404 if the event is missing; 409 if it is completed or cancelled (attendee changes are locked)."""
    row = conn.execute("SELECT status FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Event not found")
    if row["status"] in LOCKED:
        raise HTTPException(409, f"A {row['status']} event's attendees can't be changed")


def insert_event(conn: sqlite3.Connection, event: EventFields, status: str) -> int:
    cursor = conn.execute(
        "INSERT INTO events (name, starts_at, ends_at, location, category, notes, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event.name, event.starts_at, event.ends_at, event.location, event.category, event.notes, status),
    )
    return cursor.lastrowid


def check_day(value: Optional[str], name: str) -> Optional[str]:
    if value is None:
        return None
    try:
        if not ISO_DATE.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{name} must be a date like 2024-03-31") from None
    return value


# ------------------------------------------------------------------- events


def find_events(conn: sqlite3.Connection, q=None, status=None, category=None, date_from=None, date_to=None) -> list[dict]:
    """Every event matching the filters, by start time (the list route caps this; exports don't)."""
    conditions: list[str] = []
    params: list = []
    if status is not None:
        conditions.append("e.status = ?")
        params.append(status)
    if category is not None:
        conditions.append("e.category = ?")
        params.append(category)
    if date_from is not None:
        conditions.append("substr(e.starts_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to is not None:
        conditions.append("substr(e.starts_at, 1, 10) <= ?")
        params.append(date_to)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    needle = " ".join(q.casefold().split()) if q else ""
    rows = conn.execute(EVENT_SELECT + where + " ORDER BY e.starts_at, e.id", params).fetchall()
    if needle:
        # shortcut: substring search runs in Python, like /members.
        rows = [
            row for row in rows
            if any(
                needle in str(row[field]).casefold()
                for field in ("name", "location", "category")
                if row[field] is not None
            )
        ]
    return [event_out(row) for row in rows]


@router.get("/events", dependencies=[Depends(require_user)])
def list_events(
    request: Request,
    q: Optional[str] = Query(default=None, max_length=200),
    status: Optional[Status] = None,
    category: Optional[str] = Query(default=None, max_length=100),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
):
    date_from = check_day(date_from, "date_from")
    date_to = check_day(date_to, "date_to")
    with connect(request.app.state.db_path) as conn:
        return find_events(conn, q, status, category, date_from, date_to)[:MAX_LISTED_EVENTS]


@router.post("/events", status_code=201, dependencies=[Depends(require_editor)])
def create_event(event: EventIn, request: Request):
    with connect(request.app.state.db_path) as conn:
        return get_event(conn, insert_event(conn, event, event.status))


@router.get("/events/{event_id}", dependencies=[Depends(require_user)])
def read_event(event_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        return get_event(conn, event_id)


@router.patch("/events/{event_id}", dependencies=[Depends(require_editor)])
def update_event(event_id: int, request: Request, changes: dict[str, Any] = Body(...)):
    with connect(request.app.state.db_path) as conn:
        current = get_event(conn, event_id)
        try:
            merged = EventState(**{**{key: current[key] for key in (*EVENT_FIELDS, "status")}, **changes})
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False))
        old, new = current["status"], merged.status
        if new != old and new not in TRANSITIONS[old]:
            raise HTTPException(409, f"Can't change a {old} event to {new}")
        if old in LOCKED and any(
            getattr(merged, key) != current[key] for key in EVENT_FIELDS if key != "notes"
        ):
            raise HTTPException(409, f"A {old} event can't be edited, except its notes")
        conn.execute(
            "UPDATE events SET name = ?, starts_at = ?, ends_at = ?, location = ?, category = ?, "
            "notes = ?, status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (merged.name, merged.starts_at, merged.ends_at, merged.location, merged.category,
             merged.notes, merged.status, event_id),
        )
        return get_event(conn, event_id)


@router.post("/events/{event_id}/duplicate", status_code=201, dependencies=[Depends(require_editor)])
def duplicate_event(event_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        current = get_event(conn, event_id)
        new_id = insert_event(conn, EventFields(**{key: current[key] for key in EVENT_FIELDS}), "draft")
        conn.execute(
            "INSERT INTO event_attendees (event_id, member_id) "
            "SELECT ?, member_id FROM event_attendees WHERE event_id = ?",
            (new_id, event_id),
        )
        return get_event(conn, new_id)


@router.delete("/events/{event_id}", status_code=204, dependencies=[Depends(require_secretary)])
def delete_event(event_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        current = get_event(conn, event_id)
        if current["status"] != "draft":
            raise HTTPException(409, "Only draft events can be deleted; cancel it instead")
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))  # attendees cascade
    return Response(status_code=204)


@router.get("/categories", dependencies=[Depends(require_user)])
def list_categories(request: Request):
    with connect(request.app.state.db_path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT category FROM events WHERE category IS NOT NULL "
                "ORDER BY category COLLATE NOCASE, category"
            )
        ]


# ---------------------------------------------------------------- attendees


@router.post("/events/{event_id}/attendees", dependencies=[Depends(require_editor)])
def add_attendees(event_id: int, body: AttendeesIn, request: Request):
    with connect(request.app.state.db_path) as conn:
        require_unlocked(conn, event_id)
        # shortcut: lookups run in Python so a few thousand ids never hit SQLite's variable limit.
        members = {row["id"]: row for row in conn.execute("SELECT id, full_name, active FROM members")}
        unknown = sorted({i for i in body.member_ids if i not in members})
        if unknown:
            raise HTTPException(422, f"Unknown member id: {', '.join(map(str, unknown))}")
        inactive = sorted({i for i in body.member_ids if not members[i]["active"]})
        if inactive:
            names = ", ".join(f"{members[i]['full_name']} (id {i})" for i in inactive)
            raise HTTPException(422, f"Inactive members can't be added: {names}")
        wanted_groups = set(body.group_ids)
        known_groups = {row[0] for row in conn.execute("SELECT id FROM groups")}
        unknown_groups = sorted(wanted_groups - known_groups)
        if unknown_groups:
            raise HTTPException(422, f"Unknown group id: {', '.join(map(str, unknown_groups))}")

        # D6: a group is expanded now, to its active members; later group changes don't matter.
        chosen = set(body.member_ids)
        for member_id, group_id in conn.execute("SELECT member_id, group_id FROM member_groups"):
            if group_id in wanted_groups and members[member_id]["active"]:
                chosen.add(member_id)
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO event_attendees (event_id, member_id) VALUES (?, ?)",
            [(event_id, member_id) for member_id in sorted(chosen)],
        )
        added = conn.total_changes - before
        return {"added": added, "event": get_event(conn, event_id)}


@router.delete(
    "/events/{event_id}/attendees/{member_id}", status_code=204, dependencies=[Depends(require_editor)]
)
def remove_attendee(event_id: int, member_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        require_unlocked(conn, event_id)
        cursor = conn.execute(
            "DELETE FROM event_attendees WHERE event_id = ? AND member_id = ?", (event_id, member_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "That member is not on the expected list")
    return Response(status_code=204)
