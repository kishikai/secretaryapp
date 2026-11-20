import sqlite3

import pytest

from app.db import connect, init_db
from tests.conftest import client_with_role


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "events.db")


@pytest.fixture
def secretary(db_path):
    return client_with_role(db_path, "secretary")


def make_event(client, name="Meeting", starts_at="2025-03-10T18:30", **extra):
    response = client.post("/events", json={"name": name, "starts_at": starts_at, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def make_member(client, name, active=True):
    response = client.post("/members", json={"full_name": name, "active": active})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_group(client, name, member_ids=()):
    group_id = client.post("/groups", json={"name": name}).json()["id"]
    for member_id in member_ids:
        groups = client.get(f"/members/{member_id}").json()["group_ids"]
        client.put(f"/members/{member_id}/groups", json={"group_ids": [*groups, group_id]})
    return group_id


def set_status(client, event_id, status):
    return client.patch(f"/events/{event_id}", json={"status": status})


def attendee_ids(client, event_id):
    return [a["member_id"] for a in client.get(f"/events/{event_id}").json()["attendees"]]


# ------------------------------------------------------------------- CRUD


def test_event_crud(secretary):
    event = make_event(secretary, name="  Annual dinner ", ends_at="2025-03-10T21:00", location="", category="Social")
    assert event["name"] == "Annual dinner" and event["location"] is None
    assert event["status"] == "draft" and event["attendees"] == [] and event["expected_count"] == 0
    assert secretary.get(f"/events/{event['id']}").json() == event

    patched = secretary.patch(f"/events/{event['id']}", json={"location": "Hall", "ends_at": None})
    assert patched.status_code == 200
    assert patched.json()["location"] == "Hall" and patched.json()["ends_at"] is None
    assert patched.json()["name"] == "Annual dinner"

    assert secretary.get("/events/999").status_code == 404
    assert secretary.patch("/events/999", json={"notes": "x"}).status_code == 404
    assert secretary.delete(f"/events/{event['id']}").status_code == 204
    assert secretary.get(f"/events/{event['id']}").status_code == 404


def test_create_can_be_draft_or_scheduled_only(secretary):
    assert make_event(secretary, status="scheduled")["status"] == "scheduled"
    for status in ("completed", "cancelled", "bogus"):
        response = secretary.post("/events", json={"name": "x", "starts_at": "2025-01-01T10:00", "status": status})
        assert response.status_code == 422


@pytest.mark.parametrize("body", [
    {"name": "", "starts_at": "2025-01-01T10:00"},
    {"name": "x" * 201, "starts_at": "2025-01-01T10:00"},
    {"name": "x"},
    {"name": "x", "starts_at": "2025-01-01"},
    {"name": "x", "starts_at": "2025-01-01 10:00"},
    {"name": "x", "starts_at": "2025-13-01T10:00"},
    {"name": "x", "starts_at": "2025-01-01T25:00"},
    {"name": "x", "starts_at": "2025-01-01T10:00:00Z"},
    {"name": "x", "starts_at": "2025-01-01T10:00", "ends_at": "nope"},
    {"name": "x", "starts_at": "2025-01-01T10:00", "ends_at": "2025-01-01T09:59"},
    {"name": "x", "starts_at": "2025-01-01T10:00", "location": "x" * 201},
    {"name": "x", "starts_at": "2025-01-01T10:00", "category": "x" * 101},
    {"name": "x", "starts_at": "2025-01-01T10:00", "notes": "x" * 2001},
    {"name": "x", "starts_at": "2025-01-01T10:00", "surprise": 1},
])
def test_create_validation(secretary, body):
    assert secretary.post("/events", json=body).status_code == 422


def test_patch_validation(secretary):
    event = make_event(secretary, ends_at="2025-03-10T20:00")
    url = f"/events/{event['id']}"
    assert secretary.patch(url, json={"starts_at": "2025-03-10T21:00"}).status_code == 422  # now after ends_at
    assert secretary.patch(url, json={"ends_at": "2025-03-10T18:00"}).status_code == 422
    assert secretary.patch(url, json={"starts_at": "tomorrow"}).status_code == 422
    assert secretary.patch(url, json={"name": " "}).status_code == 422
    assert secretary.patch(url, json={"id": 5}).status_code == 422
    assert secretary.patch(url, json={"starts_at": "2025-03-10T19:00", "ends_at": "2025-03-10T22:00"}).status_code == 200
    assert secretary.get(url).json()["starts_at"] == "2025-03-10T19:00"


def test_list_filters_and_ordering(secretary):
    a = make_event(secretary, "Zumba night", "2025-03-12T09:00", location="Gym", category="Fitness")
    b = make_event(secretary, "Board meeting", "2025-03-10T18:00", location="Office", category="Admin", status="scheduled")
    c = make_event(secretary, "Café social", "2025-03-10T09:00", category="Social")
    d = make_event(secretary, "Late", "2025-03-10T09:00")  # same start as c: ties go by id

    listing = secretary.get("/events").json()
    assert [e["id"] for e in listing] == [c["id"], d["id"], b["id"], a["id"]]
    assert set(listing[0]) == {"id", "name", "starts_at", "ends_at", "location", "category", "notes",
                               "status", "expected_count", "series_id"}

    def ids(**params):
        return [e["id"] for e in secretary.get("/events", params=params).json()]

    assert ids(q="BOARD") == [b["id"]]
    assert ids(q="gym") == [a["id"]]  # location
    assert ids(q="fit") == [a["id"]]  # category
    assert ids(q="CAFÉ") == [c["id"]]
    assert ids(q="nothing here") == []
    assert ids(status="scheduled") == [b["id"]]
    assert ids(category="Admin") == [b["id"]]
    assert ids(category="admin") == []  # exact
    assert ids(date_from="2025-03-11") == [a["id"]]
    assert ids(date_to="2025-03-10") == [c["id"], d["id"], b["id"]]  # inclusive of the whole day
    assert ids(date_from="2025-03-10", date_to="2025-03-10") == [c["id"], d["id"], b["id"]]
    assert ids(date_from="2025-03-11", date_to="2025-03-12") == [a["id"]]
    assert ids(status="draft", q="social") == [c["id"]]
    assert secretary.get("/events", params={"status": "bogus"}).status_code == 422
    assert secretary.get("/events", params={"date_from": "03/10/2025"}).status_code == 422
    assert secretary.get("/events", params={"date_to": "2025-02-30"}).status_code == 422


def test_categories(secretary):
    assert secretary.get("/categories").json() == []
    make_event(secretary, category="Social")
    make_event(secretary, category="admin")
    make_event(secretary, category="Social")
    make_event(secretary)
    assert secretary.get("/categories").json() == ["admin", "Social"]


# --------------------------------------------------------------- lifecycle

ALLOWED = [("draft", "scheduled"), ("draft", "cancelled"), ("scheduled", "completed"), ("scheduled", "cancelled")]


def event_in_status(client, status):
    event = make_event(client)
    path = {"draft": [], "scheduled": ["scheduled"], "completed": ["scheduled", "completed"],
            "cancelled": ["cancelled"]}[status]
    for step in path:
        assert set_status(client, event["id"], step).status_code == 200
    return event["id"]


@pytest.mark.parametrize("old,new", ALLOWED)
def test_allowed_transitions(secretary, old, new):
    event_id = event_in_status(secretary, old)
    response = set_status(secretary, event_id, new)
    assert response.status_code == 200 and response.json()["status"] == new


DISALLOWED = [
    (old, new)
    for old in ("draft", "scheduled", "completed", "cancelled")
    for new in ("draft", "scheduled", "completed", "cancelled")
    if old != new and (old, new) not in ALLOWED
]


@pytest.mark.parametrize("old,new", DISALLOWED)
def test_disallowed_transitions(secretary, old, new):
    event_id = event_in_status(secretary, old)
    response = set_status(secretary, event_id, new)
    assert response.status_code == 409
    assert response.json()["detail"] == f"Can't change a {old} event to {new}"
    assert secretary.get(f"/events/{event_id}").json()["status"] == old


def test_unknown_status_is_422(secretary):
    event = make_event(secretary)
    assert set_status(secretary, event["id"], "archived").status_code == 422


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_locked_events(secretary, status):
    member = make_member(secretary, "Ana")
    other = make_member(secretary, "Ben")
    event_id = make_event(secretary, notes="old")["id"]
    secretary.post(f"/events/{event_id}/attendees", json={"member_ids": [member]})
    for step in (["scheduled", "completed"] if status == "completed" else ["cancelled"]):
        set_status(secretary, event_id, step)
    url = f"/events/{event_id}"

    for change in ({"name": "New"}, {"starts_at": "2026-01-01T10:00"}, {"ends_at": "2026-01-01T10:00"},
                   {"location": "Elsewhere"}, {"category": "X"}, {"name": "New", "notes": "fine"}):
        assert secretary.patch(url, json=change).status_code == 409, change
    assert secretary.get(url).json()["notes"] == "old"

    edited = secretary.patch(url, json={"notes": "new notes"})
    assert edited.status_code == 200 and edited.json()["notes"] == "new notes"
    assert secretary.patch(url, json={"notes": None}).json()["notes"] is None
    assert secretary.patch(url, json={"status": status}).status_code == 200  # no change, no problem

    assert secretary.post(f"{url}/attendees", json={"member_ids": [other]}).status_code == 409
    assert secretary.delete(f"{url}/attendees/{member}").status_code == 409
    assert attendee_ids(secretary, event_id) == [member]
    assert secretary.delete(url).status_code == 409


def test_delete_only_draft(secretary):
    for status in ("scheduled", "completed", "cancelled"):
        event_id = event_in_status(secretary, status)
        response = secretary.delete(f"/events/{event_id}")
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft events can be deleted; cancel it instead"
        assert secretary.get(f"/events/{event_id}").status_code == 200
    assert secretary.delete("/events/999").status_code == 404


def test_delete_draft_removes_attendees(secretary, db_path):
    member = make_member(secretary, "Ana")
    event_id = make_event(secretary)["id"]
    secretary.post(f"/events/{event_id}/attendees", json={"member_ids": [member]})
    assert secretary.delete(f"/events/{event_id}").status_code == 204
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_attendees").fetchone()[0] == 0
    assert secretary.get(f"/members/{member}").status_code == 200


# --------------------------------------------------------------- duplicate


def test_duplicate_copies_fields_and_attendees_as_draft(secretary):
    ana, ben = make_member(secretary, "Ana"), make_member(secretary, "Ben")
    original = make_event(secretary, "Choir", "2025-05-01T19:00", ends_at="2025-05-01T21:00",
                          location="Church", category="Music", notes="Bring music", status="scheduled")
    secretary.post(f"/events/{original['id']}/attendees", json={"member_ids": [ana, ben]})
    set_status(secretary, original["id"], "completed")

    response = secretary.post(f"/events/{original['id']}/duplicate")
    assert response.status_code == 201
    copy = response.json()
    assert copy["id"] != original["id"] and copy["status"] == "draft"
    for field in ("name", "starts_at", "ends_at", "location", "category", "notes"):
        assert copy[field] == original[field]
    assert [a["member_id"] for a in copy["attendees"]] == [ana, ben] and copy["expected_count"] == 2
    # the copy is independent and editable
    assert secretary.delete(f"/events/{copy['id']}/attendees/{ana}").status_code == 204
    assert attendee_ids(secretary, original["id"]) == [ana, ben]
    assert secretary.post("/events/999/duplicate").status_code == 404


# --------------------------------------------------------------- attendees


def test_group_is_a_snapshot_of_active_members(secretary):
    ana, ben = make_member(secretary, "Ana"), make_member(secretary, "Ben")
    gone = make_member(secretary, "Gone")
    group = make_group(secretary, "Choir", [ana, ben, gone])
    secretary.patch(f"/members/{gone}", json={"active": False})
    event_id = make_event(secretary)["id"]

    response = secretary.post(f"/events/{event_id}/attendees", json={"group_ids": [group]})
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 2 and body["event"]["expected_count"] == 2
    assert [a["member_id"] for a in body["event"]["attendees"]] == [ana, ben]
    assert body["event"]["attendees"][0]["group_ids"] == [group]

    late = make_member(secretary, "Cy")
    secretary.put(f"/members/{late}/groups", json={"group_ids": [group]})
    secretary.put(f"/members/{ben}/groups", json={"group_ids": []})
    assert attendee_ids(secretary, event_id) == [ana, ben]  # unchanged


def test_attendees_ordered_by_name_ignoring_case(secretary):
    zed, amy, bob = make_member(secretary, "zed"), make_member(secretary, "Amy"), make_member(secretary, "bob")
    event_id = make_event(secretary)["id"]
    secretary.post(f"/events/{event_id}/attendees", json={"member_ids": [zed, bob, amy]})
    assert attendee_ids(secretary, event_id) == [amy, bob, zed]
    first = secretary.get(f"/events/{event_id}").json()["attendees"][0]
    assert set(first) == {"member_id", "full_name", "email", "phone", "active", "group_ids",
                          "status", "recorded_at", "recorded_by"}


def test_duplicates_are_ignored(secretary):
    ana, ben = make_member(secretary, "Ana"), make_member(secretary, "Ben")
    group = make_group(secretary, "Choir", [ana])
    event_id = make_event(secretary)["id"]
    url = f"/events/{event_id}/attendees"
    assert secretary.post(url, json={"member_ids": [ana, ana], "group_ids": [group]}).json()["added"] == 1
    again = secretary.post(url, json={"member_ids": [ana, ben], "group_ids": [group]}).json()
    assert again["added"] == 1 and again["event"]["expected_count"] == 2
    assert secretary.post(url, json={"member_ids": [ana]}).json()["added"] == 0


def test_attendee_validation(secretary):
    ana = make_member(secretary, "Ana")
    gone = make_member(secretary, "Gone Gail", active=False)
    group = make_group(secretary, "Choir", [ana])
    event_id = make_event(secretary)["id"]
    url = f"/events/{event_id}/attendees"

    unknown = secretary.post(url, json={"member_ids": [ana, 998, 999]})
    assert unknown.status_code == 422 and "998, 999" in unknown.json()["detail"]
    inactive = secretary.post(url, json={"member_ids": [gone]})
    assert inactive.status_code == 422 and "Gone Gail" in inactive.json()["detail"]
    assert secretary.post(url, json={"group_ids": [group, 777]}).status_code == 422
    assert secretary.post(url, json={}).status_code == 422
    assert secretary.post(url, json={"member_ids": [], "group_ids": []}).status_code == 422
    assert secretary.post(url, json={"member_ids": list(range(1, 5002))}).status_code == 422
    assert secretary.post(url, json={"member_ids": ["x"]}).status_code == 422
    assert secretary.post(url, json={"member_ids": [ana], "extra": 1}).status_code == 422
    assert secretary.post("/events/999/attendees", json={"member_ids": [ana]}).status_code == 404
    assert attendee_ids(secretary, event_id) == []  # nothing was half-added


def test_remove_attendee(secretary):
    ana, ben = make_member(secretary, "Ana"), make_member(secretary, "Ben")
    event_id = make_event(secretary)["id"]
    secretary.post(f"/events/{event_id}/attendees", json={"member_ids": [ana, ben]})
    assert secretary.delete(f"/events/{event_id}/attendees/{ana}").status_code == 204
    assert attendee_ids(secretary, event_id) == [ben]
    assert secretary.delete(f"/events/{event_id}/attendees/{ana}").status_code == 404
    assert secretary.delete(f"/events/999/attendees/{ben}").status_code == 404
    assert secretary.get("/events").json()[0]["expected_count"] == 1


# ------------------------------------------------------------------- roles


def test_viewer_reads_but_cannot_write(db_path):
    secretary = client_with_role(db_path, "secretary")
    member = make_member(secretary, "Ana")
    event_id = make_event(secretary, category="Social")["id"]
    secretary.post(f"/events/{event_id}/attendees", json={"member_ids": [member]})

    viewer = client_with_role(db_path, "viewer")
    assert viewer.get("/events").status_code == 200
    assert viewer.get(f"/events/{event_id}").status_code == 200
    assert viewer.get("/categories").json() == ["Social"]
    writes = [
        viewer.post("/events", json={"name": "x", "starts_at": "2025-01-01T10:00"}),
        viewer.patch(f"/events/{event_id}", json={"notes": "x"}),
        viewer.post(f"/events/{event_id}/duplicate"),
        viewer.delete(f"/events/{event_id}"),
        viewer.post(f"/events/{event_id}/attendees", json={"member_ids": [member]}),
        viewer.delete(f"/events/{event_id}/attendees/{member}"),
    ]
    assert [r.status_code for r in writes] == [403] * 6
    assert len(secretary.get("/events").json()) == 1 and attendee_ids(secretary, event_id) == [member]


def test_assistant_can_do_everything_but_delete_events(db_path):
    assistant = client_with_role(db_path, "assistant")
    member = make_member(assistant, "Ana")
    event = make_event(assistant, status="scheduled")
    url = f"/events/{event['id']}"
    assert assistant.patch(url, json={"location": "Hall"}).status_code == 200
    assert assistant.post(f"{url}/attendees", json={"member_ids": [member]}).status_code == 200
    copy = assistant.post(f"{url}/duplicate")
    assert copy.status_code == 201
    assert assistant.delete(f"{url}/attendees/{member}").status_code == 204
    assert assistant.delete(f"/events/{copy.json()['id']}").status_code == 403
    assert assistant.get(f"/events/{copy.json()['id']}").status_code == 200

    secretary = client_with_role(db_path, "secretary")
    assert secretary.delete(f"/events/{copy.json()['id']}").status_code == 204


# --------------------------------------------------------------- migration


def test_init_db_adds_event_tables_to_an_older_database(tmp_path):
    path = str(tmp_path / "old.db")
    init_db(path)
    with connect(path) as conn:
        conn.execute("DROP TABLE event_attendees")
        conn.execute("DROP TABLE events")
        conn.execute("INSERT INTO members (full_name) VALUES ('Ana')")

    init_db(path)
    init_db(path)  # running twice is fine
    with connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"events", "event_attendees"} <= tables
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_events_starts_at", "idx_event_attendees_member"} <= indexes
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1
        conn.execute("INSERT INTO events (name, starts_at) VALUES ('x', '2025-01-01T10:00')")
        assert conn.execute("SELECT status FROM events").fetchone()[0] == "draft"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET status = 'bogus'")
