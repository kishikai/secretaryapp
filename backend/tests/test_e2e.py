"""One realistic workflow through the HTTP API, from sign-in to backup and sign-out."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.backup import backup_database
from app.db import connect
from tests.conftest import CSRF_HEADERS
from tests.test_ocr import FakeEngine, line, png

FIX = "Present sir, see you at 9:00 am sharp"
MISREAD = "Presemt sir, see you at 9:00 am sharp"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def admin_client(db_path, make_client):
    return make_client(db_path, ocr_engine=FakeEngine([line(MISREAD, 20, 100, 380, 120, 0.8)]))


@pytest.fixture
def ingest(admin_client, db_path):
    """No cookie; an ingest token in the Authorization header."""
    with connect(db_path) as conn:
        token = auth.create_api_token(conn, "collector")
    return TestClient(admin_client.app, headers={"Authorization": f"Bearer {token}"})


def clip(ingest, text, chat_id):
    response = ingest.post("/messages", json={"message_text": text, "source": "clipboard", "chat_id": chat_id})
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_full_workflow(admin_client, ingest, db_path, tmp_path):
    # 1. Rules.
    for rule in (
        {"name": "Attendance", "kind": "keyword", "keywords": ["present", "absent"],
         "category": "attendance", "notify": True},
        {"name": "Money", "kind": "pattern", "pattern": "money", "category": "money"},
        {"name": "Time", "kind": "pattern", "pattern": "time", "category": "schedule", "notify": False},
    ):
        assert admin_client.post("/rules", json=rule).status_code == 201

    # 2. Ingest: clipboard to two chats, an OCR screenshot with a misread, then the clipboard fix.
    clip(ingest, "Reminder: bring P250 for the field trip", "class-a")
    clip(ingest, "Homework is due next week", "class-b")
    clip(ingest, "Hello everyone, welcome to the group", "class-b")
    shot = ingest.post(
        "/ocr/screenshots",
        files={"file": ("s.png", png(), "image/png")},
        data={"chat_id": "class-a"},
    )
    assert shot.status_code == 200, shot.text
    (ocr_row,) = shot.json()["messages"]
    assert ocr_row["message_text"] == MISREAD and ocr_row["match"] is None
    assert ocr_row["categories"] == ["schedule"]  # "Presemt" is not "present" yet
    assert admin_client.get("/notifications").json() == []

    fixed = clip(ingest, FIX, "class-a")
    assert fixed["match"] == "fuzzy" and fixed["id"] == ocr_row["id"]

    # 3. One merged row, with both sources, both categories and a single notification.
    messages = admin_client.get("/messages").json()
    assert len(messages) == 4
    (merged,) = [m for m in messages if m["id"] == ocr_row["id"]]
    assert merged["message_text"] == FIX
    assert merged["sources"] == ["clipboard", "ocr"]
    assert merged["source"] == "clipboard"
    assert "attendance" in merged["categories"] and "schedule" in merged["categories"]
    unread = admin_client.get("/notifications", params={"unread": "true"}).json()
    assert len(admin_client.get("/notifications").json()) == 1
    (notification,) = unread
    assert notification["rule_name"] == "Attendance"
    assert notification["message_id"] == merged["id"]

    # 4. Search, filters, pagination.
    def ids(**params):
        response = admin_client.get("/messages", params=params)
        assert response.status_code == 200, response.text
        return [m["id"] for m in response.json()]

    assert ids(q="present") == [merged["id"]]
    assert ids(q="presemt") == []
    assert ids(q="homework") == [2]
    assert len(ids(chat_id="class-b")) == 2
    assert ids(chat_id="class-a") == [merged["id"], 1]
    assert ids(category="attendance") == [merged["id"]]
    assert ids(category="money") == [1]
    assert ids(category="schedule") == [merged["id"]]
    assert len(ids(source="clipboard")) == 4 and ids(source="ocr") == []
    first_page = ids(limit=2)
    assert first_page == [4, 3]
    assert ids(limit=2, before_id=first_page[-1]) == [2, 1]
    assert ids(before_id=1) == []

    stats = admin_client.get("/stats").json()
    assert stats["total_messages"] == 4
    assert stats["by_source"] == {"clipboard": 4, "ocr": 0}
    assert stats["by_category"] == [
        {"category": "attendance", "count": 1},
        {"category": "money", "count": 1},
        {"category": "schedule", "count": 1},
    ]
    assert stats["unread_notifications"] == 1
    assert stats["enabled_rules"] == 3
    chats = {c["chat_id"]: c["count"] for c in admin_client.get("/chats").json()}
    assert chats == {"class-a": 2, "class-b": 2}

    read = admin_client.post(f"/notifications/{notification['id']}/read")
    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert admin_client.get("/notifications", params={"unread": "true"}).json() == []
    assert admin_client.get("/stats").json()["unread_notifications"] == 0

    # 5. A backup holds the same data and still searches.
    backup = backup_database(db_path, str(tmp_path / "bk"))
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4
        found = conn.execute(
            "SELECT m.message_text FROM messages_fts f JOIN messages m ON m.id = f.rowid "
            "WHERE messages_fts MATCH 'present*'"
        ).fetchall()
        assert found == [(FIX,)]
    finally:
        conn.close()

    # 6. After signing out the admin endpoints are closed; the ingest token only ingests.
    assert admin_client.post("/auth/logout").status_code == 204
    for path in ("/messages", "/stats", "/chats", "/notifications", "/rules", "/auth/me"):
        assert admin_client.get(path).status_code == 401, path
    for path in ("/messages", "/stats", "/chats", "/notifications", "/rules"):
        assert ingest.get(path).status_code == 401, path
    again = ingest.post("/messages", json={"message_text": "one more", "source": "clipboard"})
    assert again.status_code == 201
    assert ingest.post("/rules", json={}, headers=CSRF_HEADERS).status_code == 401
