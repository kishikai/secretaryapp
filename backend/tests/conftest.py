import functools

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import connect
from app.main import create_app

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "correct horse battery"
CSRF_HEADERS = {auth.CSRF_HEADER: auth.CSRF_VALUE}


@functools.lru_cache(maxsize=1)
def _admin_hash() -> str:
    return auth.hash_password(ADMIN_PASSWORD)  # scrypt is slow on purpose; hash once per run


def signed_in_client(db_path, ocr_engine=None) -> TestClient:
    """An admin is created in the test database and signed in through /auth/login.

    The client keeps the session cookie and sends X-Requested-With by default.
    Calling it again on the same database (a "restart") reuses the admin.
    """
    app = create_app(db_path, ocr_engine=ocr_engine)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash) VALUES (?, ?)",
            (ADMIN_USERNAME, _admin_hash()),
        )
    client = TestClient(app, headers=CSRF_HEADERS)
    response = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def client_with_role(db_path, role: str) -> TestClient:
    """A user named after `role` (secretary, assistant or viewer), signed in."""
    app = create_app(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash, role) VALUES (?, ?, ?)",
            (role, _admin_hash(), role),
        )
    client = TestClient(app, headers=CSRF_HEADERS)
    response = client.post("/auth/login", json={"username": role, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def make_client():
    """Factory: make_client(db_path, ocr_engine=None) -> signed-in TestClient."""
    return signed_in_client


@pytest.fixture(autouse=True)
def no_frontend_dist(tmp_path, monkeypatch):
    """Keep tests independent of whether frontend/dist has been built."""
    monkeypatch.setenv("SECRETARY_FRONTEND_DIST", str(tmp_path / "no-frontend-dist"))
