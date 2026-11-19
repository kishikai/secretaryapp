import base64
import hashlib
import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import admin, auth
from app.db import connect
from app.main import create_app
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, CSRF_HEADERS
from tests.test_ocr import FakeEngine, line, png

PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
}
LOGIN = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
MESSAGE = {"message_text": "hello from a collector", "source": "clipboard"}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(db_path):
    return create_app(db_path, ocr_engine=FakeEngine([line("hi there", 20, 100, 120, 120, 0.9)]))


@pytest.fixture
def admin_client(db_path, make_client):
    return make_client(db_path)


@pytest.fixture
def anon(app):
    """No cookie, no credentials."""
    return TestClient(app)


class FromIP:
    """ASGI wrapper that makes every request look like it came from `ip`."""

    def __init__(self, app, ip):
        self.app, self.ip = app, ip

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (self.ip, 5000)}
        await self.app(scope, receive, send)


def from_ip(app, ip):
    return TestClient(FromIP(app, ip), headers=CSRF_HEADERS)
def new_token(db_path
, name="collector"):
    with connect(db_path) as conn:
        return auth.create_api_token(conn, name)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def query(db_path, sql, *params):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------- passwords


def test_password_hash_format_and_verification():
    stored = auth.hash_password("a long enough password")
    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(digest)) == 64
    assert auth.verify_password("a long enough password", stored)
    assert not auth.verify_password("a long enough passworD", stored)
    assert not auth.verify_password("", stored)
    assert auth.hash_password("a long enough password") != stored  # random salt


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$x$8$1$AAAA$AAAA", "md5$1$2$3$AAAA$AAAA", "scrypt$16384$8$1$!!$!!"])
def test_malformed_stored_hash_never_verifies(stored):
    assert auth.verify_password("anything", stored) is False


def test_unknown_username_is_still_checked_against_a_hash(db_path, monkeypatch):
    create_app(db_path)
    checked = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda pw, stored: checked.append(stored) or real(pw, stored))
    with connect(db_path) as conn:
        assert auth.authenticate(conn, "nobody", "whatever password") is None
    assert len(checked) == 1 and checked[0].startswith("scrypt$16384$8$1$")


# ------------------------------------------------------------- login/session


def test_login_sets_a_hardened_cookie(app, monkeypatch):
    monkeypatch.delenv("SECRETARY_COOKIE_SECURE", raising=False)
    client = signed_in(app)
    response = client.post("/auth/login", json=LOGIN)
    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 200 and response.json() == {"username": ADMIN_USERNAME}
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/" in cookie and "max-age=43200" in cookie
    assert "secure" not in cookie


def test_cookie_is_secure_with_the_env_var(app, monkeypatch):
    monkeypatch.setenv("SECRETARY_COOKIE_SECURE", "1")
    cookie = signed_in(app).post("/auth/login", json=LOGIN).headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie


def signed_in(app):
    """A client for `app` with a ready admin account but no session yet."""
    with connect(app.state.db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash) VALUES (?, ?)",
            (ADMIN_USERNAME, auth.hash_password(ADMIN_PASSWORD)),
        )
    return TestClient(app, headers=CSRF_HEADERS)


def test_me_and_only_a_digest_is_stored(admin_client, db_path):
    assert admin_client.get("/auth/me").json() == {"username": ADMIN_USERNAME}
    token = admin_client.cookies[auth.SESSION_COOKIE]
    [row] = query(db_path, "SELECT * FROM sessions")
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest() != token
    lifetime = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
    assert lifetime == timedelta(hours=12)


def test_me_needs_a_session(anon):
    response = anon.get("/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in required"}


def test_logout_invalidates_the_session(admin_client, db_path):
    token = admin_client.cookies[auth.SESSION_COOKIE]
    response = admin_client.post("/auth/logout")
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert query(db_path, "SELECT * FROM sessions") == []
    replay = TestClient(admin_client.app)
    replay.cookies.set(auth.SESSION_COOKIE, token)
    assert replay.get("/auth/me").status_code == 401


def test_logout_without_a_session_is_fine(anon):
    assert anon.post("/auth/logout", headers=CSRF_HEADERS).status_code == 204


def test_expired_session_is_rejected(admin_client, db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00.000Z'")
    assert admin_client.get("/auth/me").status_code == 401
    assert admin_client.get("/messages").status_code == 401


def test_new_login_removes_expired_sessions(app, db_path):
    client = signed_in(app)
    client.post("/auth/login", json=LOGIN)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00.000Z'")
    client.post("/auth/login", json=LOGIN)
    assert len(query(db_path, "SELECT * FROM sessions")) == 1


def test_wrong_password_and_unknown_user_look_the_same(app):
    client = signed_in(app)
    wrong = client.post("/auth/login", json={**LOGIN, "password": "not the password"})
    unknown = client.post("/auth/login", json={"username": "nobody", "password": "not the password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Wrong username or password"}
    assert auth.SESSION_COOKIE not in wrong.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "payload",
    [{}, {"username": "", "password": "x"}, {"username": "a", "password": ""}, {"username": "a" * 101, "password": "x"}, {"username": "a", "password": "x" * 1001}],
)
def test_login_body_is_validated(anon, payload):
    assert anon.post("/auth/login", json=payload, headers=CSRF_HEADERS).status_code == 422


# ---------------------------------------------------------------- rate limit


def fail(client, username="admin"):
    return client.post("/auth/login", json={"username": username, "password": "wrong password"})


def test_sixth_failure_is_rate_limited(app):
    client = signed_in(app)
    assert [fail(client).status_code for _ in range(5)] == [401] * 5
    blocked = fail(client)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 15 * 60
    # Even the right password is refused while the limit holds.
    assert client.post("/auth/login", json=LOGIN).status_code == 429


def test_limit_applies_per_ip_across_usernames(app):
    client = signed_in(app)
    assert [fail(client, f"user{i}").status_code for i in range(5)] == [401] * 5
    assert fail(client, "another").status_code == 429


def test_limit_applies_per_username_across_ips(app):
    signed_in(app)
    codes = [fail(from_ip(app, f"10.0.0.{i}")).status_code for i in range(6)]
    assert codes == [401] * 5 + [429]
    # Username matching ignores case.
    assert fail(from_ip(app, "10.0.1.1"), "ADMIN").status_code == 429


def test_window_slides_and_clears(app):
    now = [1000.0]
    app.state.login_limiter.clock = lambda: now[0]
    client = signed_in(app)
    for _ in range(5):
        fail(client)
    assert fail(client).status_code == 429
    now[0] += 15 * 60 + 1
    assert fail(client).status_code == 401
    assert client.post("/auth/login", json=LOGIN).status_code == 200


def test_success_clears_the_username_failures(app):
    signed_in(app)
    assert [fail(from_ip(app, f"10.0.0.{i}")).status_code for i in range(4)] == [401] * 4
    assert from_ip(app, "10.0.0.50").post("/auth/login", json=LOGIN).status_code == 200
    # Four more failures would be 8 in total without the reset.
    assert [fail(from_ip(app, f"10.0.1.{i}")).status_code for i in range(4)] == [401] * 4


# ------------------------------------------------------------- route audit


def _walk(routes):
    # FastAPI may wrap included routers (e.g. _IncludedRouter.original_router), so recurse.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)


def audited_routes(app):
    """Every (method, path) the app answers, from two sources so neither can hide a route:
    the route tree (catches include_in_schema=False) and the OpenAPI schema (catches routers
    however FastAPI wraps them)."""
    found = {(m, r.path) for r in _walk(app.routes) for m in r.methods - {"HEAD", "OPTIONS"}}
    app.openapi_schema = None  # rebuild so routes added after a first call are included
    for path, operations in app.openapi()["paths"].items():
        found |= {(method.upper(), path) for method in operations}
    for method, path in sorted(found):
        if (method, path) not in PUBLIC_ROUTES:
            yield method, path


def test_every_non_public_route_requires_credentials(anon, app):
    audited = []
    for method, path in audited_routes(app):
        url = path.replace("{message_id}", "1").replace("{rule_id}", "1").replace("{notification_id}", "1")
        assert "{" not in url, f"give {path} a dummy value in this audit"
        kwargs = {} if method == "GET" else {"json": {}}
        response = anon.request(method, url, **kwargs)
        assert response.status_code == 401, f"{method} {path} answered {response.status_code} without credentials"
        audited.append((method, path))
    assert ("POST", "/messages") in audited and ("GET", "/stats") in audited
    assert ("DELETE", "/rules/{rule_id}") in audited and len(audited) >= 15


def test_audit_catches_an_unprotected_route(app):
    @app.get("/oops")
    def oops():
        return {}

    assert ("GET", "/oops") in set(audited_routes(app))
    assert TestClient(app).get("/oops").status_code == 200  # which the audit above would flag


def test_public_routes_stay_open(anon):
    assert anon.get("/health").status_code == 200
    assert anon.post("/auth/login", json=LOGIN, headers=CSRF_HEADERS).status_code == 401  # reachable, wrong credentials


def test_interactive_docs_are_not_exposed(admin_client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert admin_client.get(path).status_code == 404


# --------------------------------------------------------------------- CSRF


def test_cookie_write_needs_the_csrf_header(admin_client):
    cookie_only = TestClient(admin_client.app, cookies=admin_client.cookies)
    response = cookie_only.post("/messages", json=MESSAGE)
    assert response.status_code == 403
    assert response.json() == {"detail": "Missing X-Requested-With header"}
    assert cookie_only.post("/messages", json=MESSAGE, headers={"X-Requested-With": "other"}).status_code == 403
    assert cookie_only.post("/rules/1/backfill").status_code == 403
    assert cookie_only.delete("/rules/1").status_code == 403
    assert cookie_only.post("/messages", json=MESSAGE, headers=CSRF_HEADERS).status_code == 201


def test_cookie_reads_do_not_need_the_header(admin_client):
    cookie_only = TestClient(admin_client.app, cookies=admin_client.cookies)
    assert cookie_only.get("/messages").status_code == 200


def test_unauthenticated_requests_get_401_before_the_csrf_check(anon):
    assert anon.post("/messages", json=MESSAGE).status_code == 401


def test_bearer_write_does_not_need_the_header(anon, db_path):
    response = anon.post("/messages", json=MESSAGE, headers=bearer(new_token(db_path)))
    assert response.status_code == 201


@pytest.mark.parametrize("path", ["/auth/login", "/auth/logout"])
def test_login_and_logout_need_the_header(anon, path):
    response = anon.post(path, json=LOGIN)
    assert response.status_code == 403
    assert response.json() == {"detail": "Missing X-Requested-With header"}


# -------------------------------------------------------------- ingest tokens


def test_ingest_token_can_ingest(anon, db_path):
    headers = bearer(new_token(db_path))
    assert anon.post("/messages", json=MESSAGE, headers=headers).status_code == 201
    response = anon.post(
        "/ocr/screenshots", files={"file": ("s.png", png(), "image/png")}, headers=headers
    )
    assert response.status_code == 200
    assert [m["message_text"] for m in response.json()["messages"]] == ["hi there"]


def test_ingest_token_cannot_read(anon, db_path):
    headers = bearer(new_token(db_path))
    for path in ("/messages", "/rules", "/stats", "/chats", "/notifications", "/messages/1/variants"):
        assert anon.get(path, headers=headers).status_code == 401, path
    assert anon.post("/rules", json={}, headers=headers).status_code == 401


def test_tokens_are_stored_hashed_and_prefixed(db_path):
    create_app(db_path)
    token = new_token(db_path)
    assert token.startswith("st_") and len(token) > 40
    [row] = query(db_path, "SELECT * FROM api_tokens")
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in tuple(row)
    assert row["scope"] == "ingest" and row["last_used_at"] is None and row["revoked_at"] is None


def test_unknown_malformed_or_basic_credentials_are_rejected(anon, db_path):
    new_token(db_path)
    for headers in (bearer("st_nope"), {"Authorization": "Bearer"}, {"Authorization": "Basic abc"}, bearer("")):
        assert anon.post("/messages", json=MESSAGE, headers=headers).status_code == 401


def test_revoked_token_is_rejected(anon, db_path):
    token = new_token(db_path)
    assert anon.post("/messages", json=MESSAGE, headers=bearer(token)).status_code == 201
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = '2024-01-01T00:00:00.000Z'")
    response = anon.post("/messages", json=MESSAGE, headers=bearer(token))
    assert response.status_code == 401


def test_last_used_at_is_updated(anon, db_path):
    token = new_token(db_path)
    anon.post("/messages", json=MESSAGE, headers=bearer("st_wrong"))
    assert query(db_path, "SELECT last_used_at FROM api_tokens")[0][0] is None
    anon.post("/messages", json=MESSAGE, headers=bearer(token))
    assert query(db_path, "SELECT last_used_at FROM api_tokens")[0][0].endswith("Z")


def test_admin_session_can_ingest_too(admin_client):
    assert admin_client.post("/messages", json=MESSAGE).status_code == 201


def test_ingest_token_scope_is_constrained_by_the_schema(db_path):
    create_app(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO api_tokens (name, token_hash, scope) VALUES ('x', 'h', 'admin')")


# ----------------------------------------------------------------------- CLI


def run_cli(db_path, *args, stdin=""):
    import sys

    real = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        return admin.main(["--db", db_path, *args])
    finally:
        sys.stdin = real


def test_cli_create_user_and_login(app, db_path, capsys):
    assert run_cli(db_path, "create-user", "bob", "--password-stdin", stdin="bobs long password\n") == 0
    client = TestClient(app, headers=CSRF_HEADERS)
    assert client.post("/auth/login", json={"username": "bob", "password": "bobs long password"}).status_code == 200
    assert "bob" in capsys.readouterr().out


def test_cli_create_user_rejects_short_password(db_path, capsys):
    assert run_cli(db_path, "create-user", "bob", "--password-stdin", stdin="short\n") == 1
    assert "at least 12" in capsys.readouterr().err
    assert query(db_path, "SELECT * FROM admins") == []


def test_cli_create_user_refuses_a_duplicate(db_path, capsys):
    assert run_cli(db_path, "create-user", "bob", "--password-stdin", stdin="bobs long password\n") == 0
    assert run_cli(db_path, "create-user", "bob", "--password-stdin", stdin="another long password\n") == 1
    assert run_cli(db_path, "create-user", "BOB", "--password-stdin", stdin="another long password\n") == 1
    assert "already exists" in capsys.readouterr().err
    assert len(query(db_path, "SELECT * FROM admins")) == 1


def test_cli_prompts_twice_without_stdin_flag(db_path, monkeypatch):
    answers = iter(["a good long password", "a good long password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert admin.main(["--db", db_path, "create-user", "carol"]) == 0
    mismatch = iter(["a good long password", "different long password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(mismatch))
    assert admin.main(["--db", db_path, "create-user", "dave"]) == 1
    assert [r["username"] for r in query(db_path, "SELECT username FROM admins")] == ["carol"]


def test_cli_db_option_defaults_to_the_environment(tmp_path, monkeypatch):
    path = str(tmp_path / "env.db")
    monkeypatch.setenv("SECRETARY_DB_PATH", path)
    assert admin.main(["list-tokens"]) == 0
    assert admin.build_parser().parse_args(["list-tokens"]).db == path
    assert admin.build_parser().parse_args(["list-tokens", "--db", "x.db"]).db == "x.db"


def test_cli_set_password_ends_sessions(app, admin_client, db_path):
    assert admin_client.get("/auth/me").status_code == 200
    assert run_cli(db_path, "set-password", ADMIN_USERNAME, "--password-stdin", stdin="a brand new password\n") == 0
    assert admin_client.get("/auth/me").status_code == 401
    assert query(db_path, "SELECT * FROM sessions") == []
    fresh = TestClient(app, headers=CSRF_HEADERS)
    assert fresh.post("/auth/login", json=LOGIN).status_code == 401
    assert fresh.post("/auth/login", json={"username": ADMIN_USERNAME, "password": "a brand new password"}).status_code == 200


def test_cli_set_password_checks_user_and_length(db_path, capsys):
    assert run_cli(db_path, "set-password", "ghost", "--password-stdin", stdin="a long enough one\n") == 1
    run_cli(db_path, "create-user", "bob", "--password-stdin", stdin="bobs long password\n")
    assert run_cli(db_path, "set-password", "bob", "--password-stdin", stdin="short\n") == 1
    assert capsys.readouterr().err.count("error:") == 2


def test_cli_token_lifecycle(anon, db_path, capsys):
    assert run_cli(db_path, "create-token", "desktop") == 0
    captured = capsys.readouterr()
    token = captured.out.strip()
    assert token.startswith("st_") and "not stored" in captured.err
    assert anon.post("/messages", json=MESSAGE, headers=bearer(token)).status_code == 201

    assert run_cli(db_path, "list-tokens") == 0
    listing = capsys.readouterr().out
    assert "desktop" in listing and "ingest" in listing and "no" in listing.splitlines()[1].split("\t")[-1]
    assert token not in listing and hashlib.sha256(token.encode()).hexdigest() not in listing

    assert run_cli(db_path, "revoke-token", "1") == 0
    assert anon.post("/messages", json=MESSAGE, headers=bearer(token)).status_code == 401
    capsys.readouterr()
    run_cli(db_path, "list-tokens")
    assert capsys.readouterr().out.splitlines()[1].endswith("yes")
    assert run_cli(db_path, "revoke-token", "99") == 1
    assert "no token" in capsys.readouterr().err


# ---------------------------------------------------- headers and static files


def test_security_headers_on_every_response(anon):
    for response in (anon.get("/health"), anon.get("/messages")):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Content-Security-Policy"] == (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
    assert "access-control-allow-origin" not in anon.options("/health", headers={"Origin": "http://evil.example"}).headers


def test_static_frontend_is_served_and_api_wins(tmp_path, db_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Dashboard</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("SECRETARY_FRONTEND_DIST", str(dist))
    client = TestClient(create_app(db_path))
    home = client.get("/")
    assert home.status_code == 200 and "Dashboard" in home.text
    assert home.headers["X-Frame-Options"] == "DENY"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/messages").status_code == 401  # the API route, not a static 404
    assert client.get("/auth/me").status_code == 401
    assert client.post("/messages", json=MESSAGE).status_code == 401


def test_no_static_mount_without_a_dist_directory(tmp_path, db_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_FRONTEND_DIST", str(tmp_path / "missing"))
    client = TestClient(create_app(db_path))
    assert client.get("/").status_code == 404
    assert client.get("/health").status_code == 200


# ------------------------------------------------------------ stats and chats


def set_created(db_path, message_id, created_at):
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (created_at, message_id))


def at(days_ago, hour=12):
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return f"{day.isoformat()}T{hour:02d}:00:00.000Z"


def test_stats_on_an_empty_database(admin_client):
    body = admin_client.get("/stats").json()
    assert body["total_messages"] == 0 and body["latest_message_at"] is None
    assert body["by_source"] == {"clipboard": 0, "ocr": 0} and body["by_category"] == []
    assert body["unread_notifications"] == 0 and body["enabled_rules"] == 0
    assert len(body["per_day"]) == 14 and all(d["count"] == 0 for d in body["per_day"])


def test_stats_values(admin_client, db_path):
    client = admin_client
    ids = {}
    for key, text, source, chat in [
        ("a", "chairs for sunday", "clipboard", "g1"),
        ("b", "tables for sunday", "clipboard", "g1"),
        ("c", "chairs again please", "ocr", "g2"),
        ("d", "an old note", "ocr", None),
        ("e", "an ancient note", "clipboard", None),
    ]:
        body = {"message_text": text, "source": source, "chat_id": chat}
        if source == "ocr":
            body["confidence"] = 0.9
        ids[key] = client.post("/messages", json=body).json()["id"]
    set_created(db_path, ids["a"], at(0, 8))
    set_created(db_path, ids["b"], at(0, 23))
    set_created(db_path, ids["c"], at(2))
    set_created(db_path, ids["d"], at(13))
    set_created(db_path, ids["e"], at(14))  # outside the 14-day chart, still counted in the total

    client.post("/rules", json={"name": "Setup", "kind": "keyword", "keywords": ["chairs", "tables"], "category": "setup", "notify": True})
    client.post("/rules", json={"name": "Old", "kind": "keyword", "keywords": ["note"], "category": "archive"})
    client.post("/rules", json={"name": "Off", "kind": "keyword", "keywords": ["x"], "enabled": False})
    for rule_id in (1, 2):
        client.post(f"/rules/{rule_id}/backfill")
    with sqlite3.connect(db_path) as conn:  # backfill makes no notifications
        conn.execute("INSERT INTO notifications (rule_id, message_id, matched_text) VALUES (1, 1, 'chairs'), (1, 3, 'chairs')")
        conn.execute("UPDATE notifications SET read_at = '2024-01-01T00:00:00.000Z' WHERE message_id = 3")

    body = client.get("/stats").json()
    assert body["total_messages"] == 5
    assert body["by_source"] == {"clipboard": 3, "ocr": 2}
    assert body["by_category"] == [{"category": "setup", "count": 3}, {"category": "archive", "count": 2}]
    assert body["unread_notifications"] == 1
    assert body["enabled_rules"] == 2
    assert body["latest_message_at"] == at(0, 23)

    per_day = body["per_day"]
    assert len(per_day) == 14
    today = datetime.now(timezone.utc).date()
    assert [d["day"] for d in per_day] == [(today - timedelta(days=n)).isoformat() for n in range(13, -1, -1)]
    counts = {d["day"]: d["count"] for d in per_day}
    assert counts[today.isoformat()] == 2
    assert counts[(today - timedelta(days=2)).isoformat()] == 1
    assert counts[(today - timedelta(days=13)).isoformat()] == 1
    assert sum(counts.values()) == 4
    assert per_day[-1]["count"] == 2 and per_day[0]["count"] == 1 and per_day[5]["count"] == 0


def test_chats(admin_client, db_path):
    client = admin_client
    ids = [
        client.post("/messages", json={"message_text": text, "source": "clipboard", "chat_id": chat}).json()["id"]
        for text, chat in [("one", "g1"), ("two", "g1"), ("three", "g2"), ("four", None)]
    ]
    for message_id, created in zip(ids, [at(5), at(1), at(3), at(0)]):
        set_created(db_path, message_id, created)
    assert client.get("/chats").json() == [
        {"chat_id": None, "count": 1, "latest_at": at(0)},
        {"chat_id": "g1", "count": 2, "latest_at": at(1)},
        {"chat_id": "g2", "count": 1, "latest_at": at(3)},
    ]


def test_chats_empty(admin_client):
    assert admin_client.get("/chats").json() == []
