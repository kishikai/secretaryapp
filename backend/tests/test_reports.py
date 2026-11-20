"""Absence figures checked against a hand-worked example (D9)."""
import pytest

from app import reports_api
from tests.conftest import client_with_role
from tests.test_events import make_event, make_member, set_status


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "reports.db")


def event_with(client, starts_at, statuses, finalise=True, cancel=False):
    """A scheduled event with these {member_id: status} records."""
    event_id = make_event(client, name=f"Event {starts_at[:10]}", starts_at=starts_at, status="scheduled")["id"]
    client.post(f"/events/{event_id}/attendees", json={"member_ids": list(statuses)})
    records = [{"member_id": m, "status": s} for m, s in statuses.items() if s != "not_recorded"]
    if records:
        assert client.put(f"/events/{event_id}/attendance", json={"records": records}).status_code == 200
    if finalise:
        assert client.post(f"/events/{event_id}/finalize").status_code == 200
    if cancel:
        assert set_status(client, event_id, "cancelled").status_code == 200
    return event_id


@pytest.fixture
def world(db_path, monkeypatch):
    """Ann, Bob and Cat across two counted events and three that don't count.

    Counted:  10 Jan  Ann late (excused), Bob absent, Cat absent (excused)
              10 Feb  Ann late,    Bob absent, Cat absent
    Not counted: 10 Mar (not finalised, Ann absent), 1 Feb (cancelled, Ann absent),
                 15 Feb (not finalised, Ann not recorded)
    By hand (rate = absent / (present + late + late excused + absent)):
             Ann 2 events, 0 absent, rate 0/2 = 0; Bob 2 absent, rate 2/2 = 1;
             Cat 1 absent, 1 absent excused (not in the rate), rate 1/1 = 1.
    """
    monkeypatch.setattr(reports_api, "today", lambda: "2025-02-20")
    client = client_with_role(db_path, "secretary")
    ann, bob, cat = (make_member(client, n) for n in ("Ann", "Bob", "Cat"))
    ids = {
        "jan": event_with(client, "2025-01-10T19:00", {ann: "late_excused", bob: "absent", cat: "absent_excused"}),
        "feb": event_with(client, "2025-02-10T19:00", {ann: "late", bob: "absent", cat: "absent"}),
        "mar": event_with(client, "2025-03-10T19:00", {ann: "absent"}, finalise=False),
        "cancelled": event_with(client, "2025-02-01T19:00", {ann: "absent"}, finalise=False, cancel=True),
        "open": event_with(client, "2025-02-15T19:00", {ann: "not_recorded"}, finalise=False),
    }
    return client, {"ann": ann, "bob": bob, "cat": cat}, ids


def figures(rows):
    return [(r["full_name"], r["events"], r["present"], r["late"], r["late_excused"], r["absent"],
             r["absent_excused"], r["absence_rate"])
            for r in rows]


def test_absences_follow_the_hand_worked_example(world):
    client, _, _ = world
    assert figures(client.get("/absences").json()) == [
        ("Bob", 2, 0, 0, 0, 2, 0, 1.0),
        ("Cat", 2, 0, 0, 0, 1, 1, 1.0),
        ("Ann", 2, 0, 1, 1, 0, 0, 0.0),
    ]


def test_date_range_and_an_all_excused_member_has_no_rate(world):
    client, _, _ = world
    assert figures(client.get("/absences", params={"date_to": "2025-01-31"}).json()) == [
        ("Bob", 1, 0, 0, 0, 1, 0, 1.0),
        ("Ann", 1, 0, 0, 1, 0, 0, 0.0),
        ("Cat", 1, 0, 0, 0, 0, 1, None),
    ]
    assert client.get("/absences", params={"date_from": "2025-03-01"}).json() == []
    assert client.get("/absences", params={"date_from": "March"}).status_code == 422


def test_group_and_active_filters(world):
    client, people, _ = world
    group = client.post("/groups", json={"name": "Altos"}).json()["id"]
    client.put(f"/members/{people['cat']}/groups", json={"group_ids": [group]})
    assert [r["full_name"] for r in client.get("/absences", params={"group_id": group}).json()] == ["Cat"]
    client.patch(f"/members/{people['bob']}", json={"active": False})
    assert [r["full_name"] for r in client.get("/absences", params={"active": "false"}).json()] == ["Bob"]
    assert [r["full_name"] for r in client.get("/absences", params={"active": "true"}).json()] == ["Cat", "Ann"]


def test_member_history_explains_the_totals(world):
    client, people, ids = world
    history = client.get(f"/members/{people['ann']}/attendance").json()
    assert history["summary"] == {"events": 2, "present": 0, "late": 1, "late_excused": 1, "absent": 0,
                                  "absent_excused": 0, "absence_rate": 0.0}
    assert [(r["event_id"], r["status"], r["finalized"], r["counted"]) for r in history["rows"]] == [
        (ids["mar"], "absent", False, False),
        (ids["open"], "not_recorded", False, False),
        (ids["feb"], "late", True, True),
        (ids["cancelled"], "absent", False, False),
        (ids["jan"], "late_excused", True, True),
    ]
    assert client.get("/members/999/attendance").status_code == 404


def test_dashboard(world):
    client, people, ids = world
    board = client.get("/dashboard").json()
    assert board["members"] == {"active": 3, "inactive": 0} and board["groups"] == 0
    assert [e["id"] for e in board["upcoming_events"]] == [ids["mar"]] and board["upcoming_count"] == 1
    assert [(e["id"], e["not_recorded"]) for e in board["registers_to_finish"]] == [(ids["open"], 1)]
    # 90 days to 20 Feb covers both counted events: late 1, late excused 1, absent 3, absent excused 1 -> 3/5.
    assert board["absences"] == {"date_from": "2024-11-22", "date_to": "2025-02-20",
                                 "present": 0, "late": 1, "late_excused": 1, "absent": 3,
                                 "absent_excused": 1, "absence_rate": 0.6}
    assert [m["full_name"] for m in board["most_absent"]] == ["Bob", "Cat"]


def test_every_role_can_read_reports(db_path, world):
    viewer = client_with_role(db_path, "viewer")
    for path in ("/dashboard", "/absences", "/members/1/attendance"):
        assert viewer.get(path).status_code == 200
