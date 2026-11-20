"""CSV exports, checked against the hand-worked example in test_reports."""
import csv
import io

from tests.conftest import client_with_role
from tests.test_reports import db_path, world  # noqa: F401  (fixtures)


def read(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    assert response.text.startswith("﻿")
    return list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))


def table(rows, header_first_cell):
    start = next(i for i, row in enumerate(rows) if row and row[0] == header_first_cell)
    return rows[start + 1:]


def about(rows):
    return {row[0]: row[1] for row in rows if len(row) == 2}


def test_absence_report_matches_the_hand_worked_figures(world):
    client, _, _ = world
    rows = read(client.get("/exports/absences.csv"))
    assert rows[0] == ["Absence report"] and rows[1][0] == "Generated"
    assert about(rows)["Period"] == "All dates" and "absent (excused) isn't counted" in about(rows)["Rule"]
    assert rows[rows.index(["Name", "Active", "Events counted", "Present", "Late", "Late (excused)", "Absent",
                            "Absent (excused)", "Absence rate"])]
    assert table(rows, "Name") == [
        ["Bob", "Yes", "2", "0", "0", "0", "2", "0", "100.0%"],
        ["Cat", "Yes", "2", "0", "0", "0", "1", "1", "100.0%"],
        ["Ann", "Yes", "2", "0", "1", "1", "0", "0", "0.0%"],
        # Overall: absent 3 / (present 0 + late 1 + late excused 1 + absent 3) = 60%.
        ["Total", "3 members", "6", "0", "1", "1", "3", "1", "60.0%"],
    ]
    filtered = read(client.get("/exports/absences.csv", params={"date_to": "2025-01-31"}))
    assert about(filtered)["Period"] == "the start to 2025-01-31"
    assert table(filtered, "Name")[-2] == ["Cat", "Yes", "1", "0", "0", "0", "0", "1", ""]  # no rate when only absent (excused)


def test_attendance_sheet(world):
    client, _, ids = world
    rows = read(client.get(f"/exports/events/{ids['jan']}/register.csv"))
    info = about(rows)
    assert info["Event"] == "Event 2025-01-10" and info["Register"].startswith("Finalised by secretary")
    assert (info["Total late (excused)"], info["Total absent"], info["Total absent (excused)"]) == ("1", "1", "1")
    assert [r[:3] for r in table(rows, "Name")] == [
        ["Ann", "Late (excused)", "secretary"], ["Bob", "Absent", "secretary"], ["Cat", "Absent (excused)", "secretary"],
        ["Total expected", "3"],
    ]
    open_rows = read(client.get(f"/exports/events/{ids['open']}/register.csv"))
    assert about(open_rows)["Register"].startswith("Not finalised")
    assert client.get("/exports/events/999/register.csv").status_code == 404


def test_member_history_and_event_summary(world):
    client, people, ids = world
    rows = read(client.get(f"/exports/members/{people['ann']}/attendance.csv"))
    info = about(rows)
    assert (info["Events counted"], info["Present"], info["Late"], info["Late (excused)"], info["Absence rate"]) == (
        "2", "0", "1", "1", "0.0%")
    assert [(r[1], r[4], r[5]) for r in table(rows, "Starts")] == [
        ("Event 2025-03-10", "Absent", "No"), ("Event 2025-02-15", "Not recorded", "No"),
        ("Event 2025-02-10", "Late", "Yes"), ("Event 2025-02-01", "Absent", "No"),
        ("Event 2025-01-10", "Late (excused)", "Yes"),
    ]
    events = table(read(client.get("/exports/events.csv", params={"date_to": "2025-02-10"})), "Starts")
    assert [r[2] for r in events[:-1]] == ["Event 2025-01-10", "Event 2025-02-01", "Event 2025-02-10"]
    assert events[0][5:] == ["Completed", "Finalised", "3", "0", "0", "1", "1", "1", "0"]
    # Raw per-event counts (the cancelled 1 Feb event included): Jan LE1 A1 AE1, 1 Feb A1, 10 Feb L1 A2.
    assert events[-1][0] == "Total" and events[-1][7:] == ["7", "0", "1", "1", "4", "1", "0"]


def test_member_list_and_formula_injection(db_path):  # noqa: F811
    client = client_with_role(db_path, "secretary")
    client.post("/members", json={"full_name": "=HYPERLINK(\"http://evil\")", "notes": "+1 cmd"})
    client.post("/members", json={"full_name": "Zoë Ångström", "email": "zoe@example.org"})
    rows = read(client.get("/exports/members.csv"))
    members = table(rows, "Name")
    assert members[0][0] == "'=HYPERLINK(\"http://evil\")" and members[0][8] == "'+1 cmd"
    assert members[1][:2] == ["Zoë Ångström", "zoe@example.org"]
    assert members[-1] == ["Total members", "2"]
    assert about(read(client.get("/exports/members.csv", params={"active": "false"})))["Show"] == "Inactive members"
    assert client.get("/exports/members.csv", params={"group_id": 99}).status_code == 404


def test_every_role_can_export_and_bad_dates_are_refused(db_path, world):  # noqa: F811
    viewer = client_with_role(db_path, "viewer")
    for path in ("/exports/members.csv", "/exports/events.csv", "/exports/absences.csv",
                 "/exports/members/1/attendance.csv", "/exports/events/1/register.csv"):
        assert viewer.get(path).status_code == 200, path
    assert viewer.get("/exports/absences.csv", params={"date_from": "soon"}).status_code == 422
