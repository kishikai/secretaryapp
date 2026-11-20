import sqlite3

import pytest

from app.db import EVENTS_SCHEMA, MEMBERS_SCHEMA, init_db
from tests.conftest import client_with_role
from tests.test_events import make_event, make_member, set_status


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "attendance.db")


@pytest.fixture
def secretary(db_path):
    return client_with_role(db_path, "secretary")


@pytest.fixture
def register(secretary):
    """A scheduled event with Ann and Bob expected."""
    ann, bob = make_member(secretary, "Ann"), make_member(secretary, "Bob")
    event = make_event(secretary)
    secretary.post(f"/events/{event['id']}/attendees", json={"member_ids": [ann, bob]})
    assert set_status(secretary, event["id"], "scheduled").status_code == 200
    return event["id"], ann, bob


def statuses(client, event_id):
    return {a["full_name"]: a["status"] for a in client.get(f"/events/{event_id}").json()["attendees"]}


def test_everyone_starts_not_recorded_and_saving_progress_works(secretary, register):
    event_id, ann, bob = register
    assert statuses(secretary, event_id) == {"Ann": "not_recorded", "Bob": "not_recorded"}
    response = secretary.put(f"/events/{event_id}/attendance", json={"records": [{"member_id": ann, "status": "late"}]})
    assert response.status_code == 200
    ann_row = response.json()["attendees"][0]
    assert ann_row["status"] == "late" and ann_row["recorded_by"] == "secretary" and ann_row["recorded_at"]
    assert statuses(secretary, event_id) == {"Ann": "late", "Bob": "not_recorded"}


def test_every_change_is_audited_and_unchanged_rows_are_not(secretary, register):
    event_id, ann, bob = register
    for status in ("present", "present", "absent"):
        secretary.put(f"/events/{event_id}/attendance",
                      json={"records": [{"member_id": ann, "status": status}], "reason": "  "})
    secretary.put(f"/events/{event_id}/attendance",
                  json={"records": [{"member_id": bob, "status": "excused"}], "reason": "Sent apologies"})
    trail = secretary.get(f"/events/{event_id}/audit").json()
    assert [(t["full_name"], t["old_status"], t["new_status"], t["reason"]) for t in trail] == [
        ("Bob", "not_recorded", "excused", "Sent apologies"),
        ("Ann", "present", "absent", None),
        ("Ann", "not_recorded", "present", None),
    ]
    assert all(t["actor"] == "secretary" and t["action"] == "record" and t["at"] for t in trail)


def test_finalise_needs_everyone_recorded_then_locks_and_completes(secretary, register):
    event_id, ann, bob = register
    secretary.put(f"/events/{event_id}/attendance", json={"records": [{"member_id": ann, "status": "present"}]})
    response = secretary.post(f"/events/{event_id}/finalize")
    assert response.status_code == 409 and response.json()["detail"] == "1 person is not recorded yet"

    secretary.put(f"/events/{event_id}/attendance", json={"records": [{"member_id": bob, "status": "absent"}]})
    event = secretary.post(f"/events/{event_id}/finalize").json()
    assert event["status"] == "completed" and event["finalized_by"] == "secretary" and event["finalized_at"]
    assert secretary.post(f"/events/{event_id}/finalize").status_code == 409

    locked = secretary.put(f"/events/{event_id}/attendance", json={"records": [{"member_id": bob, "status": "present"}]})
    assert locked.status_code == 409 and "reopen" in locked.json()["detail"]
    assert statuses(secretary, event_id) == {"Ann": "present", "Bob": "absent"}


def test_reopen_needs_a_reason_and_allows_corrections(secretary, register):
    event_id, ann, bob = register
    secretary.put(f"/events/{event_id}/attendance",
                  json={"records": [{"member_id": ann, "status": "present"}, {"member_id": bob, "status": "absent"}]})
    assert secretary.post(f"/events/{event_id}/reopen", json={"reason": "x"}).status_code == 409  # not finalised
    secretary.post(f"/events/{event_id}/finalize")
    assert secretary.post(f"/events/{event_id}/reopen", json={"reason": "   "}).status_code == 422
    assert secretary.post(f"/events/{event_id}/reopen", json={}).status_code == 422

    event = secretary.post(f"/events/{event_id}/reopen", json={"reason": "Bob was there after all"}).json()
    assert event["finalized_at"] is None and event["status"] == "completed"
    assert secretary.put(f"/events/{event_id}/attendance",
                         json={"records": [{"member_id": bob, "status": "late"}]}).status_code == 200
    actions = [(t["action"], t["reason"]) for t in secretary.get(f"/events/{event_id}/audit").json()][:3]
    assert actions == [("record", None), ("reopen", "Bob was there after all"), ("finalize", None)]


@pytest.mark.parametrize("status", ["draft", "cancelled"])
def test_no_attendance_for_draft_or_cancelled_events(secretary, status):
    ann = make_member(secretary, "Ann")
    event = make_event(secretary)
    secretary.post(f"/events/{event['id']}/attendees", json={"member_ids": [ann]})
    if status == "cancelled":
        set_status(secretary, event["id"], "cancelled")
    response = secretary.put(f"/events/{event['id']}/attendance", json={"records": [{"member_id": ann, "status": "present"}]})
    assert response.status_code == 409
    assert secretary.post(f"/events/{event['id']}/finalize").status_code == 409


def test_finalising_an_empty_list_is_refused(secretary):
    event = make_event(secretary, status="scheduled")
    response = secretary.post(f"/events/{event['id']}/finalize")
    assert response.status_code == 409 and response.json()["detail"] == "No one is on the expected list"


def test_bad_records_are_rejected(secretary, register):
    event_id, ann, _ = register
    stranger = make_member(secretary, "Stranger")
    put = lambda body: secretary.put(f"/events/{event_id}/attendance", json=body)
    assert put({"records": [{"member_id": stranger, "status": "present"}]}).status_code == 422
    assert put({"records": [{"member_id": ann, "status": "asleep"}]}).status_code == 422
    assert put({"records": []}).status_code == 422
    assert put({"records": [{"member_id": ann, "status": "present", "x": 1}]}).status_code == 422
    assert secretary.put("/events/999/attendance", json={"records": [{"member_id": ann, "status": "present"}]}).status_code == 404
    assert statuses(secretary, event_id)["Ann"] == "not_recorded"


def test_roles(db_path, register):
    event_id, ann, bob = register
    viewer, assistant = client_with_role(db_path, "viewer"), client_with_role(db_path, "assistant")
    body = {"records": [{"member_id": ann, "status": "present"}, {"member_id": bob, "status": "present"}]}
    assert viewer.put(f"/events/{event_id}/attendance", json=body).status_code == 403
    assert viewer.get(f"/events/{event_id}/audit").status_code == 200
    assert assistant.put(f"/events/{event_id}/attendance", json=body).status_code == 200
    assert assistant.post(f"/events/{event_id}/finalize").status_code == 403
    secretary = client_with_role(db_path, "secretary")
    assert secretary.post(f"/events/{event_id}/finalize").status_code == 200
    assert assistant.post(f"/events/{event_id}/reopen", json={"reason": "x"}).status_code == 403
    assert secretary.get(f"/events/{event_id}/audit").json()[-1]["actor"] == "assistant"


def test_upgrade_adds_attendance_to_existing_event_lists(db_path):
    with sqlite3.connect(db_path) as conn:  # a Phase 2 database with an expected person
        conn.executescript(MEMBERS_SCHEMA + EVENTS_SCHEMA)
        conn.execute("INSERT INTO members (full_name) VALUES ('Ann')")
        conn.execute("INSERT INTO events (name, starts_at, status) VALUES ('Old', '2025-01-01T10:00', 'scheduled')")
        conn.execute("INSERT INTO event_attendees (event_id, member_id) VALUES (1, 1)")
    init_db(db_path)
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status, recorded_by FROM event_attendees").fetchone() == ("not_recorded", None)
        assert conn.execute("SELECT finalized_at FROM events").fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE event_attendees SET status = 'asleep'")
