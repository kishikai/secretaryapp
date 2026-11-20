"""Messages as attendance evidence: suggestions only, explicit approval, nothing silent."""
import sqlite3

import pytest

from tests.conftest import client_with_role
from tests.test_events import make_event, make_member, set_status


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "evidence.db")


@pytest.fixture
def world(db_path):
    """Ada and Ben expected at a scheduled event today; two attendance-like messages and one chat message."""
    client = client_with_role(db_path, "secretary")
    ada, ben = make_member(client, "Ada Lovelace"), make_member(client, "Ben Jones")
    make_member(client, "Ben Smith")
    with sqlite3.connect(db_path) as conn:
        today = conn.execute("SELECT date('now')").fetchone()[0]
    event = make_event(client, name="Choir", starts_at=f"{today}T19:00", status="scheduled")["id"]
    client.post(f"/events/{event}/attendees", json={"member_ids": [ada, ben]})
    ids = {}
    for key, text, sender in [
        ("late", "Sorry, I’m running late tonight!", "Ada Lovelace"),
        ("excused", "Apologies, can't make it this week", "Ben"),
        ("chat", "See you all at the concert", "Ada Lovelace"),
    ]:
        response = client.post("/messages", json={"message_text": text, "source": "clipboard", "sender": sender})
        assert response.status_code in (200, 201), response.text
        ids[key] = response.json()["id"]
    return client, {"ada": ada, "ben": ben, "event": event}, ids


def queue(client, state="pending"):
    return {item["message_id"]: item for item in client.get("/evidence", params={"state": state}).json()}


def register(client, event_id):
    return {a["full_name"]: a["status"] for a in client.get(f"/events/{event_id}").json()["attendees"]}


def test_queue_suggests_but_changes_nothing(world):
    client, people, ids = world
    items = queue(client)
    assert set(items) == {ids["late"], ids["excused"]}  # the plain chat message isn't evidence
    late = items[ids["late"]]["suggestion"]
    assert (late["member_id"], late["event_id"], late["status"]) == (people["ada"], people["event"], "late")
    assert any("running late" in why for why in late["why"])
    # "Ben" matches two members by first name, so no one is guessed; the status still is.
    excused = items[ids["excused"]]["suggestion"]
    assert excused["member_id"] is None and excused["status"] == "excused"
    assert register(client, people["event"]) == {"Ada Lovelace": "not_recorded", "Ben Jones": "not_recorded"}


def test_approve_updates_the_register_with_an_audited_link_to_the_message(world):
    client, people, ids = world
    body = {"member_id": people["ben"], "event_id": people["event"], "status": "excused", "note": "Told me by text"}
    item = client.post(f"/evidence/{ids['excused']}/approve", json=body).json()
    assert item["state"] == "approved" and item["review"]["member_name"] == "Ben Jones"
    assert item["review"]["reviewed_by"] == "secretary"
    assert register(client, people["event"])["Ben Jones"] == "excused"
    [entry] = client.get(f"/events/{people['event']}/audit").json()
    assert (entry["evidence_id"], entry["reason"], entry["new_status"]) == (ids["excused"], "Told me by text", "excused")
    assert ids["excused"] not in queue(client) and ids["excused"] in queue(client, "approved")
    again = client.post(f"/evidence/{ids['excused']}/approve", json=body)
    assert again.status_code == 409


def test_reject_keeps_the_message_and_changes_nothing(world):
    client, people, ids = world
    item = client.post(f"/evidence/{ids['late']}/reject", json={"note": "Different Ada"}).json()
    assert item["state"] == "rejected" and item["review"]["note"] == "Different Ada"
    assert client.get("/messages").json()  # the message itself is untouched
    assert register(client, people["event"])["Ada Lovelace"] == "not_recorded"
    assert client.get(f"/events/{people['event']}/audit").json() == []
    assert client.post(f"/evidence/{ids['late']}/reject", json={}).status_code == 409


def test_a_finalised_register_is_never_changed_by_evidence(world):
    client, people, ids = world
    client.put(f"/events/{people['event']}/attendance", json={"records": [
        {"member_id": people["ada"], "status": "present"}, {"member_id": people["ben"], "status": "absent"}]})
    assert client.post(f"/events/{people['event']}/finalize").status_code == 200
    body = {"member_id": people["ben"], "event_id": people["event"], "status": "excused"}
    response = client.post(f"/evidence/{ids['excused']}/approve", json=body)
    assert response.status_code == 409 and "reopen" in response.json()["detail"]
    assert register(client, people["event"])["Ben Jones"] == "absent"
    assert ids["excused"] in queue(client)  # still waiting; nothing was recorded


def test_approve_checks_the_choice(world):
    client, people, ids = world
    stranger = make_member(client, "Stranger")
    approve = lambda **body: client.post(f"/evidence/{ids['late']}/approve", json=body)
    assert approve(member_id=stranger, event_id=people["event"], status="late").status_code == 422
    assert approve(member_id=people["ada"], event_id=999, status="late").status_code == 404
    assert approve(member_id=people["ada"], event_id=people["event"], status="not_recorded").status_code == 422
    draft = make_event(client, name="Draft")["id"]
    assert approve(member_id=people["ada"], event_id=draft, status="late").status_code == 409
    cancelled = make_event(client, name="Off", status="scheduled")["id"]
    set_status(client, cancelled, "cancelled")
    assert approve(member_id=people["ada"], event_id=cancelled, status="late").status_code == 409
    assert client.post("/evidence/999/reject", json={}).status_code == 404
    assert register(client, people["event"])["Ada Lovelace"] == "not_recorded"


@pytest.mark.parametrize("role", ["assistant", "viewer"])
def test_evidence_review_is_secretary_only(db_path, world, role):
    client, people, ids = world
    other = client_with_role(db_path, role)
    assert other.get("/evidence").status_code == 403
    body = {"member_id": people["ada"], "event_id": people["event"], "status": "late"}
    assert other.post(f"/evidence/{ids['late']}/approve", json=body).status_code == 403
    assert other.post(f"/evidence/{ids['late']}/reject", json={}).status_code == 403
