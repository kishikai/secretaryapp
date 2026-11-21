"""Idempotency-Key: a retried POST replays the first response instead of creating a duplicate."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import auth
from app.db import connect
from app.idempotency import MAX_STORED_BODY_BYTES
from tests.conftest import CSRF_HEADERS, client_with_role

KEY = "0b5c7f0e-3d52-4a54-9c0a-2f4f6f1b7d11"
MEMBER = {"full_name": "Ana Reyes"}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "idem.db")


@pytest.fixture
def client(db_path):
    return client_with_role(db_path, "secretary")


def idem(key=KEY, **extra):
    return {"Idempotency-Key": key, **extra}


def count(db_path, table="members"):
    with connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def rows(db_path):
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM idempotency_keys")]


def test_without_header_two_posts_create_two_records(client, db_path):
    assert client.post("/members", json=MEMBER).status_code == 201
    assert client.post("/members", json=MEMBER).status_code == 201
    assert count(db_path) == 2
    assert rows(db_path) == []


def test_same_key_twice_creates_one_record_and_replays(client, db_path):
    first = client.post("/members", json=MEMBER, headers=idem())
    second = client.post("/members", json=MEMBER, headers=idem())
    assert first.status_code == second.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.content == first.content
    assert second.headers["content-type"] == first.headers["content-type"]
    assert second.headers["X-Content-Type-Options"] == "nosniff"  # security headers still added
    assert count(db_path) == 1


def test_same_key_different_body_path_or_query_is_422(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    expected = {"detail": "This Idempotency-Key was already used for a different request"}
    other_body = client.post("/members", json={"full_name": "Someone Else"}, headers=idem())
    assert other_body.status_code == 422 and other_body.json() == expected
    other_path = client.post("/groups", json={"name": "Choir"}, headers=idem())
    assert other_path.status_code == 422 and other_path.json() == expected
    other_query = client.post("/members?x=1", json=MEMBER, headers=idem())
    assert other_query.status_code == 422
    assert count(db_path) == 1


@pytest.mark.parametrize("bad", ["", "has space", "semi;colon", "a" * 129, "café", "a/b"])
def test_invalid_key_is_400(client, db_path, bad):
    response = client.post("/members", json=MEMBER, headers={"Idempotency-Key": bad.encode("latin-1")})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Idempotency-Key"}
    assert count(db_path) == 0


def test_longest_valid_key_is_accepted(client):
    response = client.post("/members", json=MEMBER, headers=idem("a-Z_9" * 25 + "abc"))  # 128 chars
    assert response.status_code == 201


def test_two_admins_with_the_same_key_are_independent(db_path):
    one = client_with_role(db_path, "secretary")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO admins (username, password_hash, role) VALUES ('second', "
            "(SELECT password_hash FROM admins LIMIT 1), 'secretary')"
        )
    two = TestClient(one.app, headers=CSRF_HEADERS)
    assert two.post("/auth/login", json={"username": "second", "password": "correct horse battery"}).status_code == 200
    first = one.post("/members", json=MEMBER, headers=idem())
    second = two.post("/members", json={"full_name": "Different"}, headers=idem())
    assert first.status_code == second.status_code == 201
    assert "Idempotent-Replayed" not in second.headers
    assert second.json()["full_name"] == "Different"
    assert count(db_path) == 2
    assert {r["scope"] for r in rows(db_path)} == {"admin:1", "admin:2"}


def failing_store(monkeypatch, exc):
    state = {"calls": 0}

    def boom(*args, **kwargs):
        state["calls"] += 1
        raise exc

    monkeypatch.setattr(main, "store_message", boom)
    return state


def test_exception_is_not_stored_and_a_retry_runs(db_path, monkeypatch):
    client = client_with_role(db_path, "secretary")
    client = TestClient(client.app, headers=CSRF_HEADERS, cookies=client.cookies, raise_server_exceptions=False)
    state = failing_store(monkeypatch, RuntimeError("database is locked"))
    message = {"message_text": "hello", "source": "clipboard"}
    assert client.post("/messages", json=message, headers=idem()).status_code == 500
    assert rows(db_path) == []
    assert client.post("/messages", json=message, headers=idem()).status_code == 500
    assert state["calls"] == 2
    monkeypatch.undo()
    retry = client.post("/messages", json=message, headers=idem())
    assert retry.status_code == 201 and "Idempotent-Replayed" not in retry.headers
    assert count(db_path, "messages") == 1


def test_5xx_response_is_not_stored(client, db_path, monkeypatch):
    from fastapi import HTTPException

    state = failing_store(monkeypatch, HTTPException(503, "busy"))
    message = {"message_text": "hello", "source": "clipboard"}
    assert client.post("/messages", json=message, headers=idem()).status_code == 503
    assert rows(db_path) == []
    assert client.post("/messages", json=message, headers=idem()).status_code == 503
    assert state["calls"] == 2


def test_4xx_validation_error_is_stored_and_replayed(client, db_path):
    first = client.post("/members", json={"full_name": ""}, headers=idem())
    assert first.status_code == 422
    second = client.post("/members", json={"full_name": ""}, headers=idem())
    assert second.status_code == 422
    assert second.content == first.content
    assert second.headers["Idempotent-Replayed"] == "true"
    assert [r["status"] for r in rows(db_path)] == [422]


def test_in_progress_row_gives_409_and_is_not_run(client, db_path):
    body = b'{"full_name":"Ana Reyes"}'
    with connect(db_path) as conn:
        admin_id = conn.execute("SELECT id FROM admins").fetchone()[0]
        conn.execute(
            "INSERT INTO idempotency_keys (scope, key, method, path, body_hash, created_at) "
            "VALUES (?, ?, 'POST', '/members', ?, ?)",
            (f"admin:{admin_id}", KEY, hashlib.sha256(body).hexdigest(), auth.now_stamp()),
        )
    response = client.post("/members", content=body, headers=idem(**{"Content-Type": "application/json"}))
    assert response.status_code == 409
    assert response.json() == {"detail": "A request with this Idempotency-Key is still in progress"}
    assert count(db_path) == 0
    # A different request under the same key is a mismatch, even while in progress.
    assert client.post("/members", json={"full_name": "Other"}, headers=idem()).status_code == 422


def test_row_is_in_progress_while_the_route_runs(client, db_path, monkeypatch):
    seen = {}
    real = main.store_message

    def spying(conn, message):
        seen["rows"] = rows(db_path)
        return real(conn, message)

    monkeypatch.setattr(main, "store_message", spying)
    response = client.post("/messages", json={"message_text": "hi", "source": "clipboard"}, headers=idem())
    assert response.status_code == 201
    assert [r["status"] for r in seen["rows"]] == [None]
    assert [r["status"] for r in rows(db_path)] == [201]


def test_expired_row_is_ignored_and_purged(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    old = auth.stamp(datetime.now(timezone.utc) - timedelta(hours=25))
    with connect(db_path) as conn:
        conn.execute("UPDATE idempotency_keys SET created_at = ?", (old,))
    again = client.post("/members", json=MEMBER, headers=idem())
    assert again.status_code == 201 and "Idempotent-Replayed" not in again.headers
    assert count(db_path) == 2
    assert len(rows(db_path)) == 1 and rows(db_path)[0]["created_at"] > old


def test_stale_in_progress_row_does_not_block_forever(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    old = auth.stamp(datetime.now(timezone.utc) - timedelta(hours=1))
    with connect(db_path) as conn:
        conn.execute("UPDATE idempotency_keys SET created_at = ?, status = NULL, body = NULL", (old,))
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    assert count(db_path) == 2


def test_auth_routes_are_not_affected(client, db_path):
    body = {"username": "admin", "password": "wrong"}
    one = client.post("/auth/login", json=body, headers=idem("not valid!"))  # not even validated
    assert one.status_code == 401
    two = client.post("/auth/login", json={"username": "secretary", "password": "correct horse battery"},
                      headers=idem())
    three = client.post("/auth/login", json={"username": "secretary", "password": "correct horse battery"},
                        headers=idem())
    assert two.status_code == three.status_code == 200
    assert "Idempotent-Replayed" not in three.headers
    assert rows(db_path) == []


def test_replay_still_requires_a_session(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    anonymous = TestClient(client.app, headers=CSRF_HEADERS)
    response = anonymous.post("/members", json=MEMBER, headers=idem())
    assert response.status_code == 401
    assert "Idempotent-Replayed" not in response.headers
    assert len(rows(db_path)) == 1  # nothing stored for the anonymous call


def test_replay_still_requires_the_csrf_header(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    no_csrf = TestClient(client.app, cookies=client.cookies)
    response = no_csrf.post("/members", json=MEMBER, headers=idem())
    assert response.status_code == 403
    assert "Idempotent-Replayed" not in response.headers
    assert count(db_path) == 1


def test_signed_out_session_cannot_replay(client, db_path):
    assert client.post("/members", json=MEMBER, headers=idem()).status_code == 201
    stale = TestClient(client.app, headers=CSRF_HEADERS, cookies={auth.SESSION_COOKIE: "not-a-session"})
    assert stale.post("/members", json=MEMBER, headers=idem()).status_code == 401


def test_ingest_token_scope_for_messages(db_path):
    client = client_with_role(db_path, "secretary")
    with connect(db_path) as conn:
        token_a = auth.create_api_token(conn, "a")
        token_b = auth.create_api_token(conn, "b")
    plain = TestClient(client.app)  # no cookie, no CSRF header: Bearer needs neither
    message = {"message_text": "Meet at 6pm", "source": "clipboard"}

    first = plain.post("/messages", json=message, headers=idem(Authorization=f"Bearer {token_a}"))
    second = plain.post("/messages", json=message, headers=idem(Authorization=f"Bearer {token_a}"))
    assert first.status_code == second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json() and second.json()["duplicate"] is False
    assert count(db_path, "messages") == 1

    other = plain.post("/messages", json=message, headers=idem(Authorization=f"Bearer {token_b}"))
    assert "Idempotent-Replayed" not in other.headers
    assert other.status_code == 200 and other.json()["duplicate"] is True  # really ran
    assert {r["scope"] for r in rows(db_path)} == {"token:1", "token:2"}

    revoked = plain.post("/messages", json=message, headers=idem(Authorization="Bearer st_nope"))
    assert revoked.status_code == 401
    assert len(rows(db_path)) == 2


def test_multipart_upload_is_replayed(db_path):
    class Engine:
        calls = 0

        def read(self, image):
            Engine.calls += 1
            return []

    import cv2
    import numpy as np

    from tests.conftest import signed_in_client

    client = signed_in_client(db_path, ocr_engine=Engine())
    ok, buf = cv2.imencode(".png", np.full((48, 64, 3), 200, np.uint8))
    # The body is hashed as raw bytes, so a retry must resend the same bytes (same boundary).
    boundary = "testboundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="s.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + buf.tobytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = idem(**{"Content-Type": f"multipart/form-data; boundary={boundary}"})
    first = client.post("/ocr/screenshots", content=body, headers=headers)
    second = client.post("/ocr/screenshots", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert second.headers["Idempotent-Replayed"] == "true" and second.content == first.content
    assert Engine.calls == 1


def test_oversized_body_skips_idempotency(db_path, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(main, "MULTIPART_OVERHEAD_BYTES", 0)
    client = client_with_role(db_path, "secretary")
    response = client.post("/members", json={"full_name": "A" * 50}, headers=idem())
    assert response.status_code == 201  # ran normally and the body reached the route intact
    assert response.json()["full_name"] == "A" * 50
    assert rows(db_path) == []
    again = client.post("/members", json={"full_name": "A" * 50}, headers=idem())
    assert "Idempotent-Replayed" not in again.headers
    assert count(db_path) == 2


def test_large_response_is_not_stored(client, db_path, monkeypatch):
    big = {"data": "x" * (MAX_STORED_BODY_BYTES + 10)}

    @client.app.post("/__big")
    def big_route():
        return big

    # The route is added after start-up of the middleware stack; rebuild it.
    client.app.middleware_stack = None
    response = client.post("/__big", headers=idem())
    assert response.status_code == 200 and len(response.content) > MAX_STORED_BODY_BYTES
    assert rows(db_path) == []
    again = client.post("/__big", headers=idem())
    assert "Idempotent-Replayed" not in again.headers
