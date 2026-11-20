import io
import sqlite3
import sys

import pytest

from app import admin
from app.db import AUTH_SCHEMA, init_db
from tests.conftest import client_with_role


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "roles.db")


def test_existing_admins_become_secretaries_on_upgrade(db_path):
    with sqlite3.connect(db_path) as conn:  # an auth table from before roles (v0.6)
        conn.executescript(AUTH_SCHEMA)
        conn.execute("INSERT INTO admins (username, password_hash) VALUES ('old', 'x')")
    init_db(db_path)
    init_db(db_path)  # idempotent
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT role FROM admins WHERE username = 'old'").fetchone() == ("secretary",)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO admins (username, password_hash, role) VALUES ('x', 'x', 'owner')")


@pytest.mark.parametrize("role", ["secretary", "assistant", "viewer"])
def test_me_reports_the_role(db_path, role):
    assert client_with_role(db_path, role).get("/auth/me").json() == {"username": role, "role": role}


@pytest.mark.parametrize("role", ["assistant", "viewer"])
@pytest.mark.parametrize(
    "method,path",
    [("GET", "/messages"), ("GET", "/stats"), ("GET", "/rules"), ("POST", "/messages"), ("GET", "/notifications")],
)
def test_message_features_are_secretary_only(db_path, role, method, path):
    client = client_with_role(db_path, role)
    kwargs = {"json": {"message_text": "hi", "source": "clipboard"}} if method == "POST" else {}
    assert client.request(method, path, **kwargs).status_code == 403


def test_secretary_keeps_message_features(db_path):
    client = client_with_role(db_path, "secretary")
    assert client.get("/messages").status_code == 200
    assert client.get("/rules").status_code == 200


def run_cli(db_path, *args, stdin=""):
    real, sys.stdin = sys.stdin, io.StringIO(stdin)
    try:
        return admin.main(["--db", db_path, *args])
    finally:
        sys.stdin = real


def test_cli_creates_users_with_a_role_and_changes_it(db_path):
    assert run_cli(db_path, "create-user", "amy", "--role", "viewer", "--password-stdin", stdin="a long password!\n") == 0
    assert run_cli(db_path, "create-user", "sam", "--password-stdin", stdin="a long password!\n") == 0
    assert run_cli(db_path, "set-role", "amy", "assistant") == 0
    assert run_cli(db_path, "set-role", "nobody", "viewer") == 1
    with sqlite3.connect(db_path) as conn:
        roles = dict(conn.execute("SELECT username, role FROM admins"))
    assert roles == {"amy": "assistant", "sam": "secretary"}
