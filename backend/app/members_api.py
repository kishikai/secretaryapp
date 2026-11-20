import csv
import io
import re
import sqlite3
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.auth import require_role, require_user
from app.db import connect

router = APIRouter()

require_editor = require_role("secretary", "assistant")
require_secretary = require_role("secretary")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # same limit as /ocr/screenshots in app/main.py
MAX_IMPORT_ROWS = 5000
MAX_LISTED_MEMBERS = 1000  # shortcut: no paging yet; the list is capped
IMPORT_COLUMNS = ("full_name", "email", "phone", "member_code", "notes", "joined_on", "left_on", "groups")
MEMBER_FIELDS = ("full_name", "email", "phone", "member_code", "notes", "joined_on", "left_on")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip() or None
    return value


class MemberFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=40)
    member_code: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=2000)
    joined_on: Optional[str] = None
    left_on: Optional[str] = None

    @field_validator("full_name", mode="before")
    @classmethod
    def clean_name(cls, value: Any) -> Any:
        value = blank_to_none(value)
        if value is None:
            raise ValueError("a name is required")
        return value

    @field_validator("email", "phone", "member_code", "notes", "joined_on", "left_on", mode="before")
    @classmethod
    def clean_optional(cls, value: Any) -> Any:
        return blank_to_none(value)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            local, _, domain = value.partition("@")
            if not local or not domain or "@" in domain:
                raise ValueError("must look like name@example.com")
        return value

    @field_validator("joined_on", "left_on")
    @classmethod
    def check_date(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            try:
                if not ISO_DATE.fullmatch(value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("must be a date like 2024-03-31") from None
        return value


class MemberIn(MemberFields):
    active: bool = True


class ImportData(MemberFields):
    groups: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("groups", mode="before")
    @classmethod
    def clean_groups(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [blank_to_none(item) for item in value if blank_to_none(item) is not None]
        return value

    @field_validator("groups")
    @classmethod
    def check_groups(cls, value: list[str]) -> list[str]:
        if any(len(name) > 100 for name in value):
            raise ValueError("a group name is longer than 100 characters")
        return value


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip(cls, value: Any) -> Any:
        return blank_to_none(value)


class GroupIdsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[int] = Field(max_length=1000)


class ConfirmRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)
    action: Literal["create", "update", "skip"]
    member_id: Optional[int] = None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ConfirmRow] = Field(max_length=MAX_IMPORT_ROWS)


# ------------------------------------------------------------------ helpers


def error_text(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "row"
        message = error["msg"].removeprefix("Value error, ")
        messages.append(f"{field}: {message}")
    return messages


def conflict(exc: sqlite3.IntegrityError, value: Optional[str] = None) -> HTTPException:
    """409 with plain English for the unique constraints; anything else is a real bug."""
    text = str(exc)
    if "members.member_code" in text:
        shown = f" ({value})" if value else ""
        return HTTPException(409, f"Another member already has that member code{shown}")
    if "groups.name" in text:
        shown = f" ({value})" if value else ""
        return HTTPException(409, f"A group with that name already exists{shown}")
    raise exc


def member_ids_by_group(conn: sqlite3.Connection) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for row in conn.execute("SELECT member_id, group_id FROM member_groups ORDER BY group_id"):
        groups.setdefault(row["member_id"], []).append(row["group_id"])
    return groups


def member_out(row: sqlite3.Row, group_ids: list[int]) -> dict:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "member_code": row["member_code"],
        "notes": row["notes"],
        "active": bool(row["active"]),
        "joined_on": row["joined_on"],
        "left_on": row["left_on"],
        "group_ids": group_ids,
    }


def get_member(conn: sqlite3.Connection, member_id: int) -> dict:
    row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Member not found")
    group_ids = [
        r[0]
        for r in conn.execute(
            "SELECT group_id FROM member_groups WHERE member_id = ? ORDER BY group_id", (member_id,)
        )
    ]
    return member_out(row, group_ids)


def group_out(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "name", "description", "member_count", "created_at")}


GROUP_SELECT = (
    "SELECT g.id, g.name, g.description, g.created_at, "
    "(SELECT COUNT(*) FROM member_groups mg WHERE mg.group_id = g.id) AS member_count FROM groups g"
)


def get_group(conn: sqlite3.Connection, group_id: int) -> dict:
    row = conn.execute(GROUP_SELECT + " WHERE g.id = ?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Group not found")
    return group_out(row)


def insert_member(conn: sqlite3.Connection, member: MemberFields, active: bool = True) -> int:
    cursor = conn.execute(
        "INSERT INTO members (full_name, email, phone, member_code, notes, active, joined_on, left_on) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (member.full_name, member.email, member.phone, member.member_code, member.notes,
         int(active), member.joined_on, member.left_on),
    )
    return cursor.lastrowid


# ------------------------------------------------------------------ members


@router.get("/members", dependencies=[Depends(require_user)])
def list_members(
    request: Request,
    q: Optional[str] = Query(default=None, max_length=200),
    group_id: Optional[int] = None,
    active: Optional[bool] = None,
):
    conditions: list[str] = []
    params: list = []
    if group_id is not None:
        conditions.append("id IN (SELECT member_id FROM member_groups WHERE group_id = ?)")
        params.append(group_id)
    if active is not None:
        conditions.append("active = ?")
        params.append(int(active))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    needle = " ".join(q.casefold().split()) if q else ""
    with connect(request.app.state.db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM members" + where + " ORDER BY full_name COLLATE NOCASE, id", params
        ).fetchall()
        groups = member_ids_by_group(conn)
    if needle:
        # shortcut: substring search runs in Python (SQLite LIKE is ASCII-only case-insensitive);
        # fine for a few thousand members.
        rows = [
            row for row in rows
            if any(
                needle in str(row[field]).casefold()
                for field in ("full_name", "email", "phone", "member_code")
                if row[field] is not None
            )
        ]
    return [member_out(row, groups.get(row["id"], [])) for row in rows[:MAX_LISTED_MEMBERS]]


@router.post("/members", status_code=201)
def create_member(member: MemberIn, request: Request, user=Depends(require_editor)):
    if not member.active and user["role"] != "secretary":
        raise HTTPException(403, "Only a secretary can deactivate a member")
    with connect(request.app.state.db_path) as conn:
        try:
            member_id = insert_member(conn, member, member.active)
        except sqlite3.IntegrityError as exc:
            raise conflict(exc, member.member_code)
        return get_member(conn, member_id)


@router.get("/members/{member_id}", dependencies=[Depends(require_user)])
def read_member(member_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        return get_member(conn, member_id)


@router.patch("/members/{member_id}")
def update_member(
    member_id: int,
    request: Request,
    changes: dict[str, Any] = Body(...),
    user=Depends(require_editor),
):
    with connect(request.app.state.db_path) as conn:
        current = get_member(conn, member_id)
        try:
            merged = MemberIn(**{**{key: current[key] for key in (*MEMBER_FIELDS, "active")}, **changes})
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False))
        if merged.active != current["active"] and user["role"] != "secretary":
            raise HTTPException(403, "Only a secretary can deactivate or reactivate a member")
        try:
            conn.execute(
                "UPDATE members SET full_name = ?, email = ?, phone = ?, member_code = ?, notes = ?, "
                "joined_on = ?, left_on = ?, active = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (merged.full_name, merged.email, merged.phone, merged.member_code, merged.notes,
                 merged.joined_on, merged.left_on, int(merged.active), member_id),
            )
        except sqlite3.IntegrityError as exc:
            raise conflict(exc, merged.member_code)
        return get_member(conn, member_id)


@router.put("/members/{member_id}/groups", dependencies=[Depends(require_editor)])
def set_member_groups(member_id: int, body: GroupIdsIn, request: Request):
    wanted = sorted(set(body.group_ids))
    with connect(request.app.state.db_path) as conn:
        get_member(conn, member_id)
        known = {row[0] for row in conn.execute("SELECT id FROM groups")}
        unknown = [group_id for group_id in wanted if group_id not in known]
        if unknown:
            raise HTTPException(422, f"Unknown group id: {', '.join(map(str, unknown))}")
        conn.execute("DELETE FROM member_groups WHERE member_id = ?", (member_id,))
        conn.executemany(
            "INSERT INTO member_groups (member_id, group_id) VALUES (?, ?)",
            [(member_id, group_id) for group_id in wanted],
        )
        return get_member(conn, member_id)


# ------------------------------------------------------------------- groups


@router.get("/groups", dependencies=[Depends(require_user)])
def list_groups(request: Request):
    with connect(request.app.state.db_path) as conn:
        return [group_out(row) for row in conn.execute(GROUP_SELECT + " ORDER BY g.name COLLATE NOCASE")]


@router.post("/groups", status_code=201, dependencies=[Depends(require_editor)])
def create_group(group: GroupIn, request: Request):
    with connect(request.app.state.db_path) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO groups (name, description) VALUES (?, ?)", (group.name, group.description)
            )
        except sqlite3.IntegrityError as exc:
            raise conflict(exc, group.name)
        return get_group(conn, cursor.lastrowid)


@router.patch("/groups/{group_id}", dependencies=[Depends(require_editor)])
def update_group(group_id: int, request: Request, changes: dict[str, Any] = Body(...)):
    with connect(request.app.state.db_path) as conn:
        current = get_group(conn, group_id)
        try:
            merged = GroupIn(**{**{key: current[key] for key in ("name", "description")}, **changes})
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False))
        try:
            conn.execute(
                "UPDATE groups SET name = ?, description = ? WHERE id = ?",
                (merged.name, merged.description, group_id),
            )
        except sqlite3.IntegrityError as exc:
            raise conflict(exc, merged.name)
        return get_group(conn, group_id)


@router.delete("/groups/{group_id}", status_code=204, dependencies=[Depends(require_secretary)])
def delete_group(group_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        get_group(conn, group_id)
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))  # memberships cascade
    return Response(status_code=204)


# ------------------------------------------------------------------- import


def name_key(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


def phone_key(value: Optional[str]) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) >= 7 else ""


def match_keys(member: dict) -> dict[str, str]:
    """The (rule -> normalised value) pairs that make two people duplicate candidates (D4)."""
    keys = {
        "member code": (member.get("member_code") or "").strip(),
        "email": (member.get("email") or "").strip().casefold(),
        "phone": phone_key(member.get("phone")),
        "name": name_key(member.get("full_name")),
    }
    return {rule: value for rule, value in keys.items() if value}


def parse_csv(raw: bytes) -> tuple[list[str], list[tuple[int, dict]]]:
    """The recognised column names and (row number, cleaned values) per data row; blank rows are dropped."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Save the file as CSV UTF-8")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = {
            original: re.sub(r"\s+", "_", original.strip().lower())
            for original in (reader.fieldnames or [])
            if original is not None
        }
        if "full_name" in headers.values():
            headers = {original: key for original, key in headers.items() if key != "name"}
        else:
            headers = {original: ("full_name" if key == "name" else key) for original, key in headers.items()}
        headers = {original: key for original, key in headers.items() if key in IMPORT_COLUMNS}
        columns = list(dict.fromkeys(headers.values()))
        if "full_name" not in columns:
            raise HTTPException(422, "The file needs a full_name column (or name)")
        rows = []
        for number, record in enumerate(reader, start=1):
            if number > MAX_IMPORT_ROWS:
                raise HTTPException(422, f"The file has more than {MAX_IMPORT_ROWS} rows; split it up")
            values = {key: None for key in IMPORT_COLUMNS}
            for original, key in headers.items():
                values[key] = blank_to_none(record.get(original))
            if not any(values.values()):
                continue
            names: dict[str, str] = {}
            for name in (values["groups"] or "").split(";"):
                if name.strip():
                    names.setdefault(name.strip().casefold(), name.strip())  # first spelling wins
            values["groups"] = list(names.values())
            rows.append((number, values))
    except csv.Error:
        raise HTTPException(422, "Could not read the file as CSV")
    return columns, rows


@router.post("/members/import/preview", dependencies=[Depends(require_secretary)])
def import_preview(request: Request, file: UploadFile = File(...)):
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 10 MB")
    columns, parsed = parse_csv(raw)

    with connect(request.app.state.db_path) as conn:
        existing = [dict(row) for row in conn.execute(
            "SELECT id, full_name, email, phone, member_code FROM members ORDER BY id"
        )]
    # shortcut: every existing member is compared in Python for each row; fine up to a few
    # thousand members, so index the keys if it ever gets slow.
    existing_keys = [(member, match_keys(member)) for member in existing]

    rows = []
    earlier: list[tuple[int, dict, dict[str, str]]] = []  # (row number, data, keys)
    for number, values in parsed:
        errors: list[str] = []
        try:
            data = ImportData(**values).model_dump()
        except ValidationError as exc:
            data = values
            errors = error_text(exc)
        keys = match_keys(data)
        duplicates = []
        for member, member_keys in existing_keys:
            rules = [f"same {rule}" for rule, value in keys.items() if member_keys.get(rule) == value]
            if rules:
                duplicates.append(
                    {"member_id": member["id"], "full_name": member["full_name"], "reason": ", ".join(rules)}
                )
        for other_number, other, other_keys in earlier:
            rules = [f"same {rule}" for rule, value in keys.items() if other_keys.get(rule) == value]
            if rules:
                duplicates.append({
                    "member_id": None,
                    "full_name": other["full_name"],
                    "reason": ", ".join(rules) + f" as row {other_number}",
                })
        earlier.append((number, data, keys))
        rows.append({"row": number, "data": data, "errors": errors, "duplicates": duplicates})
    return {"rows": rows, "columns": columns}


@router.post("/members/import/confirm", dependencies=[Depends(require_secretary)])
def import_confirm(body: ConfirmIn, request: Request):
    problems: list[str] = []
    valid: list[tuple[ConfirmRow, Optional[ImportData]]] = []
    for number, row in enumerate(body.rows, start=1):
        if row.action == "skip":
            valid.append((row, None))
            continue
        try:
            valid.append((row, ImportData(**row.data)))
        except ValidationError as exc:
            problems.extend(f"Row {number}: {message}" for message in error_text(exc))
            continue
        if row.action == "update" and row.member_id is None:
            problems.append(f"Row {number}: choose which member to update")
    if problems:
        raise HTTPException(422, "; ".join(problems[:10]) + (" ..." if len(problems) > 10 else ""))

    created = updated = skipped = 0
    with connect(request.app.state.db_path) as conn:
        for number, (row, data) in enumerate(valid, start=1):
            if row.action == "skip":
                skipped += 1
                continue
            if row.action == "create":
                try:
                    member_id = insert_member(conn, data)
                except sqlite3.IntegrityError as exc:
                    raise conflict(exc, data.member_code)
                created += 1
            else:
                member_id = row.member_id
                if conn.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone() is None:
                    raise HTTPException(422, f"Row {number}: member {member_id} does not exist")
                # Only fill what the row has; never blank existing data.
                fields = [name for name in MEMBER_FIELDS if getattr(data, name) is not None]
                try:
                    conn.execute(
                        "UPDATE members SET "
                        + "".join(f"{name} = ?, " for name in fields)
                        + "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                        (*[getattr(data, name) for name in fields], member_id),
                    )
                except sqlite3.IntegrityError as exc:
                    raise conflict(exc, data.member_code)
                updated += 1
            for name in data.groups:
                group = conn.execute("SELECT id FROM groups WHERE name = ?", (name,)).fetchone()
                group_id = group["id"] if group else conn.execute(
                    "INSERT INTO groups (name) VALUES (?)", (name,)
                ).lastrowid
                conn.execute(
                    "INSERT OR IGNORE INTO member_groups (member_id, group_id) VALUES (?, ?)",
                    (member_id, group_id),
                )
    return {"created": created, "updated": updated, "skipped": skipped}
