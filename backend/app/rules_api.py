import json
import sqlite3
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app import rules
from app.db import connect

router = APIRouter()

Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RULE_FIELDS = ("name", "kind", "keywords", "pattern", "category", "notify", "enabled", "chat_id")


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: Literal["keyword", "pattern"]
    keywords: Optional[list[Phrase]] = Field(default=None, min_length=1, max_length=50)
    pattern: Optional[Literal["url", "email", "phone", "money", "time", "date"]] = None
    category: Optional[str] = Field(default=None, min_length=1, max_length=50)
    notify: bool = False
    enabled: bool = True
    chat_id: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def check_kind_fields(self):
        if self.kind == "keyword":
            if self.keywords is None:
                raise ValueError("keywords are required for a keyword rule")
            if self.pattern is not None:
                raise ValueError("pattern is not allowed for a keyword rule")
            for phrase in self.keywords:
                if not rules.words(phrase):
                    raise ValueError("every keyword needs at least one letter or digit")
        else:
            if self.pattern is None:
                raise ValueError("pattern is required for a pattern rule")
            if self.keywords is not None:
                raise ValueError("keywords are not allowed for a pattern rule")
        return self


def rule_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "keywords": json.loads(row["keywords"]) if row["keywords"] is not None else None,
        "pattern": row["pattern"],
        "category": row["category"],
        "notify": bool(row["notify"]),
        "enabled": bool(row["enabled"]),
        "chat_id": row["chat_id"],
        "created_at": row["created_at"],
    }


def get_rule(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Rule not found")
    return row


def rule_values(rule: RuleIn) -> tuple:
    return (
        rule.name,
        rule.kind,
        json.dumps(rule.keywords) if rule.keywords is not None else None,
        rule.pattern,
        rule.category,
        int(rule.notify),
        int(rule.enabled),
        rule.chat_id,
    )


@router.post("/rules", status_code=201)
def create_rule(rule: RuleIn, request: Request):
    with connect(request.app.state.db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO rules (name, kind, keywords, pattern, category, notify, enabled, chat_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rule_values(rule),
        )
        return rule_out(get_rule(conn, cursor.lastrowid))


@router.get("/rules")
def list_rules(request: Request):
    with connect(request.app.state.db_path) as conn:
        return [rule_out(row) for row in conn.execute("SELECT * FROM rules ORDER BY id")]


@router.get("/rules/{rule_id}")
def read_rule(rule_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        return rule_out(get_rule(conn, rule_id))


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, request: Request, changes: dict[str, Any] = Body(...)):
    with connect(request.app.state.db_path) as conn:
        current = rule_out(get_rule(conn, rule_id))
        try:
            merged = RuleIn(**{**{key: current[key] for key in RULE_FIELDS}, **changes})
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False))
        conn.execute(
            "UPDATE rules SET name = ?, kind = ?, keywords = ?, pattern = ?, category = ?, "
            "notify = ?, enabled = ?, chat_id = ? WHERE id = ?",
            (*rule_values(merged), rule_id),
        )
        return rule_out(get_rule(conn, rule_id))


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        get_rule(conn, rule_id)
        conn.execute("DELETE FROM notifications WHERE rule_id = ?", (rule_id,))
        conn.execute("DELETE FROM message_matches WHERE rule_id = ?", (rule_id,))
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return Response(status_code=204)


@router.post("/rules/{rule_id}/backfill")
def backfill_rule(rule_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        get_rule(conn, rule_id)
        count = rules.backfill(conn, rule_id)
    return {"rule_id": rule_id, "matching_messages": count}


NOTIFICATION_SELECT = (
    "SELECT n.id, n.rule_id, r.name AS rule_name, n.message_id, m.message_text, m.chat_id, "
    "n.matched_text, n.created_at, n.read_at "
    "FROM notifications n JOIN rules r ON r.id = n.rule_id JOIN messages m ON m.id = n.message_id"
)


@router.get("/notifications")
def list_notifications(
    request: Request,
    unread: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=500),
    before_id: Optional[int] = Query(default=None, ge=1),
):
    conditions: list[str] = []
    params: list = []
    if unread is not None:
        conditions.append("n.read_at IS NULL" if unread else "n.read_at IS NOT NULL")
    if before_id is not None:
        conditions.append("n.id < ?")
        params.append(before_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connect(request.app.state.db_path) as conn:
        rows = conn.execute(
            NOTIFICATION_SELECT + where + " ORDER BY n.id DESC LIMIT ?", (*params, limit)
        ).fetchall()
    return [dict(row) for row in rows]


# Declared before the /{id}/read route; the paths differ in shape, so order only reads clearly.
@router.post("/notifications/read-all")
def read_all_notifications(request: Request):
    with connect(request.app.state.db_path) as conn:
        cursor = conn.execute(
            "UPDATE notifications SET read_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE read_at IS NULL"
        )
        return {"marked": cursor.rowcount}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        if conn.execute("SELECT 1 FROM notifications WHERE id = ?", (notification_id,)).fetchone() is None:
            raise HTTPException(404, "Notification not found")
        conn.execute(
            "UPDATE notifications SET read_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND read_at IS NULL",
            (notification_id,),
        )
        row = conn.execute(NOTIFICATION_SELECT + " WHERE n.id = ?", (notification_id,)).fetchone()
        return dict(row)
