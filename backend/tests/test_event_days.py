"""Long events taken day by day: one linked event, and so one register, per day."""
import sqlite3

import pytest

from app.db import init_db
from tests.conftest import client_with_role
from tests.test_events import make_event, make_member


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "days.db")


@pytest.fixture
def secretary(db_path):
    return client_with_role(db_path, "secretary")


def three_day_event(client, **extra):
    ann, bob = make_member(client, "Ann"), make_member(client, "Bob")
    event = make_event(client, name="Camp", starts_at="2026-07-01T09:00", ends_at="2026-07-03T17:00",
                       location="Field", **extra)["id"]
    client.post(f"/events/{event}/attendees", json={"member_ids": [ann, bob]})
    return event, ann, bob


def test_split_gives_one_linked_event_per_day(secretary):
    event, ann, bob = three_day_event(secretary, status="scheduled")
    response = secretary.post(f"/events/{event}/split-days")
    assert response.status_code == 201, response.text
    days = response.json()["events"]
    assert [(d["name"], d["starts_at"], d["ends_at"], d["status"]) for d in days] == [
        ("Camp (day 1 of 3)", "2026-07-01T09:00", "2026-07-01T17:00", "scheduled"),
        ("Camp (day 2 of 3)", "2026-07-02T09:00", "2026-07-02T17:00", "scheduled"),
        ("Camp (day 3 of 3)", "2026-07-03T09:00", "2026-07-03T17:00", "scheduled"),
    ]
    assert days[0]["id"] == event and all(d["location"] == "Field" for d in days)
    assert all([a["full_name"] for a in d["attendees"]] == ["Ann", "Bob"] for d in days)
    assert [s["id"] for s in days[1]["series"]] == [d["id"] for d in days]
    assert all(e["series_id"] == event for e in secretary.get("/events").json())

    # Each day has its own register.
    secretary.put(f"/events/{days[1]['id']}/attendance", json={"records": [{"member_id": ann, "status": "absent"}]})
    statuses = [[a["status"] for a in secretary.get(f"/events/{d['id']}").json()["attendees"]] for d in days]
    assert statuses == [["not_recorded"] * 2, ["absent", "not_recorded"], ["not_recorded"] * 2]
    assert secretary.post(f"/events/{event}/split-days").status_code == 409  # already split


def test_split_refused_once_attendance_started_or_when_not_multi_day(secretary):
    event, ann, _ = three_day_event(secretary, status="scheduled")
    secretary.put(f"/events/{event}/attendance", json={"records": [{"member_id": ann, "status": "present"}]})
    assert secretary.post(f"/events/{event}/split-days").status_code == 409
    one_day = make_event(secretary, starts_at="2026-07-01T09:00", ends_at="2026-07-01T17:00")["id"]
    assert secretary.post(f"/events/{one_day}/split-days").status_code == 422
    no_end = make_event(secretary)["id"]
    assert secretary.post(f"/events/{no_end}/split-days").status_code == 422
    overnight = make_event(secretary, starts_at="2026-07-01T18:00", ends_at="2026-07-02T10:00")["id"]
    assert secretary.post(f"/events/{overnight}/split-days").status_code == 422
    long = make_event(secretary, starts_at="2026-07-01T09:00", ends_at="2026-08-15T17:00")["id"]
    assert secretary.post(f"/events/{long}/split-days").status_code == 422
    cancelled = make_event(secretary, starts_at="2026-07-01T09:00", ends_at="2026-07-02T17:00")["id"]
    secretary.patch(f"/events/{cancelled}", json={"status": "cancelled"})
    assert secretary.post(f"/events/{cancelled}/split-days").status_code == 409


def test_roles_and_upgrade(db_path, secretary):
    event, _, _ = three_day_event(secretary)
    assert client_with_role(db_path, "viewer").post(f"/events/{event}/split-days").status_code == 403
    assert client_with_role(db_path, "assistant").post(f"/events/{event}/split-days").status_code == 201
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE series_id = ?", (event,)).fetchone() == (3,)
