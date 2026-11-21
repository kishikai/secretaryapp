"""Absence figures (decision D9), member attendance history and the secretary's dashboard.

D9: only finalised registers of events that weren't cancelled count. Absence rate =
absent / (present + late + late excused + absent); absent (excused) is shown but not in the rate.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_user
from app.db import connect
from app.events_api import check_day

router = APIRouter(dependencies=[Depends(require_user)])

RECORDED = ("present", "late", "late_excused", "absent", "absent_excused")
COUNTED = "e.finalized_at IS NOT NULL AND e.status <> 'cancelled' AND a.status <> 'no_practice'"
STATUS_SUMS = (
    "COUNT(*) AS events, "
    + ", ".join(f"COALESCE(SUM(a.status = '{s}'), 0) AS {s}" for s in RECORDED)
)
DASHBOARD_DAYS = 90


def rate(counts: dict) -> Optional[float]:
    attended_or_absent = counts["present"] + counts["late"] + counts["late_excused"] + counts["absent"]
    return round(counts["absent"] / attended_or_absent, 4) if attended_or_absent else None


def with_rate(row) -> dict:
    out = dict(row)
    out["absence_rate"] = rate(out)
    return out


def today() -> str:
    # Event times are local wall-clock strings, so "today" is the server's local date.
    return date.today().isoformat()


def absence_rows(conn, date_from=None, date_to=None, group_id=None, active=None) -> list[dict]:
    conditions, params = [COUNTED], []
    if date_from:
        conditions.append("substr(e.starts_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("substr(e.starts_at, 1, 10) <= ?")
        params.append(date_to)
    if group_id is not None:
        conditions.append("m.id IN (SELECT member_id FROM member_groups WHERE group_id = ?)")
        params.append(group_id)
    if active is not None:
        conditions.append("m.active = ?")
        params.append(int(active))
    rows = conn.execute(
        f"SELECT m.id AS member_id, m.full_name, m.active, {STATUS_SUMS} "
        "FROM event_attendees a JOIN events e ON e.id = a.event_id JOIN members m ON m.id = a.member_id "
        f"WHERE {' AND '.join(conditions)} GROUP BY m.id",
        params,
    ).fetchall()
    out = [{**with_rate(row), "active": bool(row["active"])} for row in rows]
    out.sort(key=lambda r: (-r["absent"], -(r["absence_rate"] or 0), r["full_name"].casefold()))
    return out


@router.get("/absences")
def absences(
    request: Request,
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    group_id: Optional[int] = None,
    active: Optional[bool] = None,
):
    date_from, date_to = check_day(date_from, "date_from"), check_day(date_to, "date_to")
    with connect(request.app.state.db_path) as conn:
        return absence_rows(conn, date_from, date_to, group_id, active)


@router.get("/members/{member_id}/attendance")
def member_attendance(member_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        member = conn.execute("SELECT id, full_name, active FROM members WHERE id = ?", (member_id,)).fetchone()
        if member is None:
            raise HTTPException(404, "Member not found")
        rows = conn.execute(
            "SELECT e.id AS event_id, e.name, e.starts_at, e.ends_at, e.status AS event_status, "
            f"e.finalized_at IS NOT NULL AS finalized, ({COUNTED}) AS counted, a.status "
            "FROM event_attendees a JOIN events e ON e.id = a.event_id WHERE a.member_id = ? "
            "ORDER BY e.starts_at DESC, e.id DESC",
            (member_id,),
        ).fetchall()
    rows = [{**dict(r), "finalized": bool(r["finalized"]), "counted": bool(r["counted"])} for r in rows]
    counted = [r["status"] for r in rows if r["counted"]]
    summary = {"events": len(counted), **{s: counted.count(s) for s in RECORDED}}
    summary["no_practice"] = sum(
        r["status"] == "no_practice" and r["finalized"] and r["event_status"] != "cancelled" for r in rows
    )
    summary["absence_rate"] = rate(summary)
    return {"member": {**dict(member), "active": bool(member["active"])}, "summary": summary, "rows": rows}


@router.get("/dashboard")
def dashboard(request: Request):
    now = today()
    since = (date.fromisoformat(now) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    event_cols = "e.id, e.name, e.starts_at, e.ends_at, e.location, e.status, " \
                 "(SELECT COUNT(*) FROM event_attendees a WHERE a.event_id = e.id) AS expected_count"
    with connect(request.app.state.db_path) as conn:
        active, inactive = conn.execute(
            "SELECT COALESCE(SUM(active = 1), 0), COALESCE(SUM(active = 0), 0) FROM members"
        ).fetchone()
        groups = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        upcoming_where = "FROM events e WHERE e.status = 'scheduled' AND substr(e.starts_at, 1, 10) >= ?"
        upcoming = conn.execute(
            f"SELECT {event_cols} {upcoming_where} ORDER BY e.starts_at, e.id LIMIT 5", (now,)
        ).fetchall()
        upcoming_count = conn.execute(f"SELECT COUNT(*) {upcoming_where}", (now,)).fetchone()[0]
        to_finish = conn.execute(
            f"SELECT {event_cols}, (SELECT COUNT(*) FROM event_attendees a WHERE a.event_id = e.id "
            "AND a.status = 'not_recorded') AS not_recorded FROM events e "
            "WHERE e.status IN ('scheduled', 'completed') AND e.finalized_at IS NULL "
            "AND substr(e.starts_at, 1, 10) <= ? ORDER BY e.starts_at, e.id",
            (now,),
        ).fetchall()
        totals = conn.execute(
            f"SELECT {STATUS_SUMS} FROM event_attendees a JOIN events e ON e.id = a.event_id "
            f"WHERE {COUNTED} AND substr(e.starts_at, 1, 10) BETWEEN ? AND ?",
            (since, now),
        ).fetchone()
        most_absent = [r for r in absence_rows(conn, since, now) if r["absent"] > 0][:5]
    absences_summary = with_rate(totals)
    del absences_summary["events"]
    return {
        "members": {"active": active, "inactive": inactive},
        "groups": groups,
        "upcoming_events": [dict(r) for r in upcoming],
        "upcoming_count": upcoming_count,
        "registers_to_finish": [dict(r) for r in to_finish],
        "absences": {"date_from": since, "date_to": now, **absences_summary},
        "most_absent": [
            {k: r[k] for k in ("member_id", "full_name", "active", "absent", "absence_rate")} for r in most_absent
        ],
    }
