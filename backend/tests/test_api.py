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
