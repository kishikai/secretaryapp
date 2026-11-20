"""CSV exports (Phase 6). They reuse the same filters and figures as the screens, so a download
always matches what the page shows, without the screens' row caps. Same permissions: any role."""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.auth import require_user
from app.db import connect
from app.events_api import Status, check_day, find_events, get_event
from app.members_api import find_members
from app.reports_api import absence_rows, member_attendance, rate

router = APIRouter(prefix="/exports", dependencies=[Depends(require_user)])

STATUS_LABELS = {"not_recorded": "Not recorded", "present": "Present", "late": "Late",
                 "absent": "Absent", "excused": "Excused"}
RULE = ("Counts only finalised registers of events that weren't cancelled. "
        "Absence rate = absent / (present + late + absent); excused isn't counted.")


def cell(value) -> str:
    """Text for one cell. A leading = + - @ would make a spreadsheet run the cell as a formula,
    and names and notes come from imported files, so such text is prefixed with a quote."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def percent(value: Optional[float]) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def csv_file(filename: str, title: str, about: list[tuple[str, str]], header: list[str],
             rows: list[list], totals: Optional[list] = None) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([cell(title)])
    writer.writerow(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")])
    for label, value in about:
        writer.writerow([label, cell(value)])
    writer.writerow([])
    writer.writerow(header)
    writer.writerows([cell(v) for v in row] for row in rows)
    if totals:
        writer.writerow([cell(v) for v in totals])
    # The BOM makes Excel read the file as UTF-8, so accented names survive.
    return Response(
        "﻿" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def group_name(conn, group_id: Optional[int]) -> str:
    if group_id is None:
        return "All groups"
    row = conn.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Group not found")
    return row[0]


def shown(active: Optional[bool]) -> str:
    return "All members" if active is None else ("Active members" if active else "Inactive members")


def period(date_from: Optional[str], date_to: Optional[str]) -> str:
    if not date_from and not date_to:
        return "All dates"
    return f"{date_from or 'the start'} to {date_to or 'the end'}"


@router.get("/members.csv")
def members_csv(
    request: Request,
    q: Optional[str] = Query(default=None, max_length=200),
    group_id: Optional[int] = None,
    active: Optional[bool] = None,
):
    with connect(request.app.state.db_path) as conn:
        names = dict(conn.execute("SELECT id, name FROM groups"))
        about = [("Search", q or "None"), ("Group", group_name(conn, group_id)), ("Show", shown(active))]
        members = find_members(conn, q, group_id, active)
    rows = [
        [m["full_name"], m["email"], m["phone"], m["member_code"],
         "; ".join(sorted((names[g] for g in m["group_ids"]), key=str.casefold)),
         "Yes" if m["active"] else "No", m["joined_on"], m["left_on"], m["notes"]]
        for m in members
    ]
    header = ["Name", "Email", "Phone", "Member code", "Groups", "Active", "Joined", "Left", "Notes"]
    return csv_file("members.csv", "Member list", about, header, rows, ["Total members", len(rows)])


@router.get("/events.csv")
def events_csv(
    request: Request,
    q: Optional[str] = Query(default=None, max_length=200),
    status: Optional[Status] = None,
    category: Optional[str] = Query(default=None, max_length=100),
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
):
    date_from, date_to = check_day(date_from, "date_from"), check_day(date_to, "date_to")
    statuses = list(STATUS_LABELS)
    with connect(request.app.state.db_path) as conn:
        events = find_events(conn, q, status, category, date_from, date_to)
        counts = {}
        for row in conn.execute("SELECT event_id, status, COUNT(*) FROM event_attendees GROUP BY event_id, status"):
            counts.setdefault(row[0], {})[row[1]] = row[2]
        finalized = {r[0] for r in conn.execute("SELECT id FROM events WHERE finalized_at IS NOT NULL")}
    rows = []
    for e in events:
        c = counts.get(e["id"], {})
        rows.append([e["starts_at"].replace("T", " "), (e["ends_at"] or "").replace("T", " "), e["name"],
                     e["location"], e["category"], e["status"].capitalize(),
                     "Finalised" if e["id"] in finalized else "Not finalised", e["expected_count"],
                     *[c.get(s, 0) for s in statuses[1:]], c.get("not_recorded", 0)])
    totals = ["Total", "", count(len(rows), "event"), "", "", "", "", *[sum(r[i] for r in rows) for i in range(7, 13)]]
    about = [("Search", q or "None"), ("Status", status.capitalize() if status else "All"),
             ("Category", category or "All"), ("Period", period(date_from, date_to))]
    header = ["Starts", "Ends", "Event", "Location", "Category", "Status", "Register", "Expected",
              "Present", "Late", "Absent", "Excused", "Not recorded"]
    return csv_file("events.csv", "Event summary", about, header, rows, totals)


@router.get("/events/{event_id}/register.csv")
def register_csv(event_id: int, request: Request):
    with connect(request.app.state.db_path) as conn:
        event = get_event(conn, event_id)
    if event["finalized_at"]:
        register = f"Finalised by {event['finalized_by']} at {event['finalized_at']} (UTC)"
    else:
        register = "Not finalised: statuses may still change"
    about = [("Event", event["name"]),
             ("When", event["starts_at"].replace("T", " ") + (f" to {event['ends_at'].replace('T', ' ')}" if event["ends_at"] else "")),
             ("Location", event["location"] or ""), ("Event status", event["status"].capitalize()), ("Register", register)]
    rows = [[a["full_name"], STATUS_LABELS[a["status"]], a["recorded_by"], a["recorded_at"]] for a in event["attendees"]]
    counts = {label: sum(1 for a in event["attendees"] if STATUS_LABELS[a["status"]] == label) for label in STATUS_LABELS.values()}
    about += [(f"Total {label.lower()}", str(n)) for label, n in counts.items()]
    return csv_file(f"register-event-{event_id}.csv", "Attendance sheet", about,
                    ["Name", "Status", "Recorded by", "Recorded at (UTC)"], rows, ["Total expected", len(rows)])


@router.get("/absences.csv")
def absences_csv(
    request: Request,
    date_from: Optional[str] = Query(default=None, max_length=10),
    date_to: Optional[str] = Query(default=None, max_length=10),
    group_id: Optional[int] = None,
    active: Optional[bool] = None,
):
    date_from, date_to = check_day(date_from, "date_from"), check_day(date_to, "date_to")
    with connect(request.app.state.db_path) as conn:
        about = [("Period", period(date_from, date_to)), ("Group", group_name(conn, group_id)),
                 ("Show", shown(active)), ("Rule", RULE)]
        found = absence_rows(conn, date_from, date_to, group_id, active)
    keys = ("events", "present", "late", "absent", "excused")
    rows = [[r["full_name"], "Yes" if r["active"] else "No", *[r[k] for k in keys], percent(r["absence_rate"])]
            for r in found]
    sums = {k: sum(r[k] for r in found) for k in keys}
    totals = ["Total", count(len(found), "member"), *sums.values(), percent(rate(sums["present"], sums["late"], sums["absent"]))]
    header = ["Name", "Active", "Events counted", "Present", "Late", "Absent", "Excused", "Absence rate"]
    return csv_file("absences.csv", "Absence report", about, header, rows, totals)


@router.get("/members/{member_id}/attendance.csv")
def member_history_csv(member_id: int, request: Request):
    history = member_attendance(member_id, request)
    s = history["summary"]
    about = [("Member", history["member"]["full_name"] + ("" if history["member"]["active"] else " (inactive)")),
             ("Rule", RULE)]
    rows = [[r["starts_at"].replace("T", " "), r["name"], r["event_status"].capitalize(),
             "Finalised" if r["finalized"] else "Not finalised", STATUS_LABELS[r["status"]],
             "Yes" if r["counted"] else "No"] for r in history["rows"]]
    about += [("Events counted", str(s["events"]))] + [
        (label, str(s[key])) for key, label in STATUS_LABELS.items() if key != "not_recorded"
    ] + [("Absence rate", percent(s["absence_rate"]))]
    return csv_file(f"attendance-member-{member_id}.csv", "Attendance history", about,
                    ["Starts", "Event", "Event status", "Register", "Status", "Counted"], rows)
