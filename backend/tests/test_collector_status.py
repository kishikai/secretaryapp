import sqlite3

import pytest

from app import auth
from app.db import connect
from tests.conftest import client_with_role


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "collector.db")


def token(db_path, name="laptop"):
    with connect(db_path) as conn:
        return auth.create_api_token(conn, name)


def test_heartbeat_marks_the_collector_online(db_path):
    secretary = client_with_role(db_path, "secretary")
    first, second = token(db_path, "laptop"), token(db_path, "office")
    assert secretary.get("/collector/status").json()["online"] is False  # never seen
    assert secretary.post("/collector/heartbeat", headers={"Authorization": f"Bearer {first}"}).status_code == 204
    status = secretary.get("/collector/status").json()
    assert status["online"] is True and status["offline_after_minutes"] == 5
    assert [(c["name"], c["online"]) for c in status["collectors"]] == [("laptop", True), ("office", False)]


def test_a_quiet_collector_goes_offline(db_path):
    secretary = client_with_role(db_path, "secretary")
    secret = token(db_path)
    secretary.post("/collector/heartbeat", headers={"Authorization": f"Bearer {secret}"})
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE api_tokens SET last_used_at = '2020-01-01T00:00:00.000Z'")
    assert secretary.get("/collector/status").json()["online"] is False


def test_heartbeat_needs_a_valid_ingest_token_not_a_session(db_path):
    secretary = client_with_role(db_path, "secretary")
    assert secretary.post("/collector/heartbeat").status_code == 401  # a session is not a collector
    assert secretary.post("/collector/heartbeat", headers={"Authorization": "Bearer st_nope"}).status_code == 401
    secret = token(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = '2025-01-01'")
    assert secretary.post("/collector/heartbeat", headers={"Authorization": f"Bearer {secret}"}).status_code == 401
    assert secretary.get("/collector/status").json()["collectors"] == []


@pytest.mark.parametrize("role", ["assistant", "viewer"])
def test_status_is_secretary_only(db_path, role):
    assert client_with_role(db_path, role).get("/collector/status").status_code == 403
