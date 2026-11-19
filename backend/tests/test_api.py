import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "test.db")))


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_list_message(client):
    payload = {"message_text": "  hello  ", "chat_id": "group-1", "source": "clipboard"}
    created = client.post("/messages", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["message_text"] == "hello"
    assert body["chat_id"] == "group-1"
    assert body["source"] == "clipboard"
    assert body["created_at"]

    listed = client.get("/messages").json()
    assert [m["id"] for m in listed] == [body["id"]]


def test_list_is_newest_first(client):
    for text in ("first", "second"):
        client.post("/messages", json={"message_text": text, "source": "ocr", "confidence": 0.9})
    texts = [m["message_text"] for m in client.get("/messages").json()]
    assert texts == ["second", "first"]


@pytest.mark.parametrize(
    "payload",
    [
        {"message_text": "   ", "source": "clipboard"},
        {"message_text": "", "source": "clipboard"},
        {"message_text": "hi", "source": "email"},
        {"message_text": "hi", "source": "ocr", "confidence": 1.5},
        {"source": "clipboard"},
    ],
)
def test_invalid_messages_are_rejected(client, payload):
    assert client.post("/messages", json=payload).status_code == 422


def test_list_limit_is_bounded(client):
    assert client.get("/messages?limit=0").status_code == 422
    assert client.get("/messages?limit=501").status_code == 422


def test_text_is_normalised(client):
    # "e" + combining acute (NFD) should be stored as the single NFC character.
    body = client.post(
        "/messages", json={"message_text": "  café  \r\nline two   \r\n", "source": "clipboard"}
    ).json()
    assert body["message_text"] == "café\nline two"


def test_duplicate_message_is_not_stored_twice(client):
    payload = {"message_text": "same text", "chat_id": "g1", "source": "clipboard"}
    first = client.post("/messages", json=payload)
    second = client.post("/messages", json={**payload, "message_text": "  same text \r\n"})
    assert first.status_code == 201 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/messages").json()) == 1


def test_same_text_in_another_chat_is_kept(client):
    for chat in ("g1", "g2"):
        assert client.post(
            "/messages", json={"message_text": "hello", "chat_id": chat, "source": "clipboard"}
        ).status_code == 201
    assert len(client.get("/messages").json()) == 2


def test_v010_database_is_migrated(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "old.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT,"
            " message_text TEXT NOT NULL, sender TEXT, timestamp TEXT, source TEXT NOT NULL,"
            " confidence REAL, created_at TEXT NOT NULL DEFAULT 'x')"
        )
        conn.execute("INSERT INTO messages (message_text, source) VALUES ('old row', 'ocr')")
    client = TestClient(create_app(db_path))
    assert client.post("/messages", json={"message_text": "new", "source": "clipboard"}).status_code == 201
    assert [m["message_text"] for m in client.get("/messages").json()] == ["new", "old row"]
