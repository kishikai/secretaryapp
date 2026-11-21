"""Authentication: admin passwords, browser sessions, ingest API tokens and CSRF.

Standard library only. Secrets are never stored: passwords are scrypt hashes and
session and API tokens are kept as SHA-256 digests.
"""
import base64
import binascii
import functools
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import HTTPException, Request

from app.db import connect

SESSION_COOKIE = "secretary_session"
SESSION_LIFETIME = timedelta(hours=12)
SESSION_MAX_AGE = int(SESSION_LIFETIME.total_seconds())
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "secretary"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
API_TOKEN_PREFIX = "st_"
ROLES = ("secretary", "assistant", "viewer")

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SCRYPT_SALT_BYTES = 16
SCRYPT_MAXMEM = 64 * 1024 * 1024

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60


# ---------------------------------------------------------------- time helpers


def stamp(value: datetime) -> str:
    """Format like the database's own timestamps (UTC, millisecond precision)."""
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def now_stamp() -> str:
    return stamp(datetime.now(timezone.utc))


# ------------------------------------------------------------------- passwords


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=SCRYPT_MAXMEM
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        digest = _scrypt(password, salt, int(n), int(r), int(p), len(expected))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(digest, expected)


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("dummy password used for unknown usernames")


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> Optional[sqlite3.Row]:
    """Return the admin row for a correct username and password, else None.

    An unknown username is still checked against a dummy hash, so the response
    time does not reveal which usernames exist.
    """
    admin = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    stored = admin["password_hash"] if admin is not None else _dummy_hash()
    matches = verify_password(password, stored)
    return admin if admin is not None and matches else None


# -------------------------------------------------------------------- sessions


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, admin_id: int) -> str:
    """Store a new session and return its token (only the digest is kept)."""
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (stamp(now),))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, admin_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_digest(token), admin_id, stamp(now), stamp(now + SESSION_LIFETIME)),
    )
    return token


def cookie_secure() -> bool:
    return os.environ.get("SECRETARY_COOKIE_SECURE") == "1"


def _session_admin(conn: sqlite3.Connection, request: Request) -> Optional[sqlite3.Row]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return conn.execute(
        "SELECT a.id, a.username, a.role FROM sessions s JOIN admins a ON a.id = s.admin_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?",
        (token_digest(token), now_stamp()),
    ).fetchone()


# ------------------------------------------------------------------ API tokens


def create_api_token(conn: sqlite3.Connection, name: str) -> str:
    token = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO api_tokens (name, token_hash, scope) VALUES (?, ?, 'ingest')",
        (name, token_digest(token)),
    )
    return token


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _ingest_token_row(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT id FROM api_tokens WHERE token_hash = ? AND scope = 'ingest' AND revoked_at IS NULL",
        (token_digest(token),),
    ).fetchone()


def _valid_ingest_token(conn: sqlite3.Connection, token: str) -> bool:
    row = _ingest_token_row(conn, token)
    if row is None:
        return False
    conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_stamp(), row["id"]))
    return True


# ------------------------------------------------------------------ dependencies

SIGN_IN_REQUIRED = "Sign in required"


def check_csrf_header(request: Request) -> None:
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, f"Missing {CSRF_HEADER} header")


def _check_csrf(request: Request) -> None:
    if request.method not in SAFE_METHODS:
        check_csrf_header(request)


def require_user(request: Request) -> sqlite3.Row:
    """A valid session cookie, any role. State-changing requests also need the CSRF header."""
    with connect(request.app.state.db_path) as conn:
        user = _session_admin(conn, request)
    if user is None:
        raise HTTPException(401, SIGN_IN_REQUIRED)
    _check_csrf(request)
    return user


def require_role(*roles: str) -> Callable[[Request], sqlite3.Row]:
    """A signed-in user whose role is one of `roles` (401 if signed out, then 403)."""
    def dependency(request: Request) -> sqlite3.Row:
        user = require_user(request)
        if user["role"] not in roles:
            raise HTTPException(403, "Your role does not allow this")
        return user
    return dependency


# Messages, OCR, rules and notifications stay secretary-only (decision D10).
require_admin = require_role("secretary")


def require_ingest(request: Request) -> None:
    """An ingest API token (Bearer), or else an admin session."""
    token = _bearer_token(request)
    if token is not None:
        with connect(request.app.state.db_path) as conn:
            if _valid_ingest_token(conn, token):
                return  # Bearer requests carry no ambient credentials, so no CSRF check.
        raise HTTPException(401, SIGN_IN_REQUIRED)
    require_admin(request)


def caller_scope(conn: sqlite3.Connection, request: Request) -> Optional[str]:
    """Who is calling, as a stable string, for Idempotency-Key scoping; None when not authenticated.

    Resolves the caller like require_ingest does (a Bearer header decides on its own, else the
    session cookie) and applies the same CSRF rule to cookie callers, so a request that would be
    refused with 401 or 403 never gets a scope. It does not touch last_used_at.
    """
    token = _bearer_token(request)
    if token is not None:
        row = _ingest_token_row(conn, token)
        return f"token:{row['id']}" if row is not None else None
    user = _session_admin(conn, request)
    if user is None:
        return None
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return None
    return f"admin:{user['id']}"


# ------------------------------------------------------------------ rate limit


class LoginRateLimiter:
    """Sliding-window failure counter per client IP and per lower-cased username.

    State is in memory: the limit resets when the server restarts, and behind a
    reverse proxy every client shares the proxy's IP address.
    """

    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window: float = LOGIN_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], list[float]] = {}

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if t > now - self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until a login attempt is allowed again; 0 when it is allowed now."""
        now = self.clock()
        wait = 0.0
        with self._lock:
            for key in (("ip", ip), ("user", username.lower())):
                recent = self._recent(key, now)
                if len(recent) >= self.max_failures:
                    # Allowed again once enough old failures have left the window.
                    wait = max(wait, recent[len(recent) - self.max_failures] + self.window - now)
        return int(wait) + 1 if wait > 0 else 0

    def record_failure(self, ip: str, username: str) -> None:
        now = self.clock()
        with self._lock:
            if len(self._failures) > 10_000:  # keys are attacker-chosen; drop expired ones
                for key in list(self._failures):
                    self._recent(key, now)
            for key in (("ip", ip), ("user", username.lower())):
                self._recent(key, now)
                self._failures.setdefault(key, []).append(now)

    def clear_username(self, username: str) -> None:
        with self._lock:
            self._failures.pop(("user", username.lower()), None)
