"""Admin CLI: `python -m app.admin <command>`; see the README ("Sign-in and security")."""
import argparse
import getpass
import os
import sqlite3
import sys
from typing import Optional

from app import auth
from app.db import connect, init_db

MIN_PASSWORD_LENGTH = 12
MAX_NAME_LENGTH = 100


class CliError(Exception):
    pass


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CliError("The passwords do not match")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CliError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 1000:
        raise CliError("The password must be at most 1000 characters")
    return password


def check_name(value: str, what: str) -> str:
    if not value.strip() or len(value) > MAX_NAME_LENGTH:
        raise CliError(f"The {what} must be 1-{MAX_NAME_LENGTH} characters")
    return value


def find_admin(conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()


def create_user(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    username = check_name(args.username, "username")
    # Logins match the exact username; refusing look-alikes that differ only in case avoids confusion.
    if conn.execute("SELECT 1 FROM admins WHERE lower(username) = lower(?)", (username,)).fetchone():
        raise CliError(f"The user {username!r} already exists")
    password = read_password(args.password_stdin)
    conn.execute(
        "INSERT INTO admins (username, password_hash, role) VALUES (?, ?, ?)",
        (username, auth.hash_password(password), args.role),
    )
    print(f"Created {args.role} {username!r}")


def set_role(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if conn.execute("UPDATE admins SET role = ? WHERE username = ?", (args.role, args.username)).rowcount == 0:
        raise CliError(f"There is no user {args.username!r}")
    print(f"{args.username!r} is now a {args.role}")


def set_password(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    admin = find_admin(conn, args.username)
    if admin is None:
        raise CliError(f"There is no user {args.username!r}")
    password = read_password(args.password_stdin)
    conn.execute(
        "UPDATE admins SET password_hash = ? WHERE id = ?", (auth.hash_password(password), admin["id"])
    )
    ended = conn.execute("DELETE FROM sessions WHERE admin_id = ?", (admin["id"],)).rowcount
    print(f"Password changed for {args.username!r}; {ended} session(s) ended")


def create_token(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    token = auth.create_api_token(conn, check_name(args.name, "token name"))
    print(token)
    print("This token is shown once and is not stored; copy it now.", file=sys.stderr)


def list_tokens(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows = conn.execute(
        "SELECT id, name, scope, created_at, last_used_at, revoked_at FROM api_tokens ORDER BY id"
    ).fetchall()
    print("\t".join(["id", "name", "scope", "created_at", "last_used_at", "revoked"]))
    for row in rows:
        print(
            "\t".join(
                [
                    str(row["id"]),
                    row["name"],
                    row["scope"],
                    row["created_at"],
                    row["last_used_at"] or "-",
                    "yes" if row["revoked_at"] else "no",
                ]
            )
        )


def revoke_token(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = conn.execute("SELECT revoked_at FROM api_tokens WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise CliError(f"There is no token with id {args.id}")
    if row["revoked_at"]:
        print(f"Token {args.id} was already revoked")
        return
    conn.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ?", (auth.now_stamp(), args.id))
    print(f"Revoked token {args.id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description="Manage Secretary admins and tokens.")
    parser.add_argument(
        "--db",
        default=os.environ.get("SECRETARY_DB_PATH", "data/secretary.db"),
        help="database file (default: SECRETARY_DB_PATH or data/secretary.db)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    # Also accept --db after the command; SUPPRESS keeps the value given before it.
    db_option = argparse.ArgumentParser(add_help=False)
    db_option.add_argument("--db", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    for name, handler, help_text in (
        ("create-user", create_user, "create an admin account"),
        ("set-password", set_password, "change an admin's password and end their sessions"),
    ):
        command = commands.add_parser(name, help=help_text, parents=[db_option])
        command.add_argument("username")
        command.add_argument(
            "--password-stdin", action="store_true", help="read the password from one line of stdin"
        )
        command.set_defaults(handler=handler)
    commands.choices["create-user"].add_argument("--role", choices=auth.ROLES, default="secretary")

    command = commands.add_parser("set-role", help="change a user's role", parents=[db_option])
    command.add_argument("username")
    command.add_argument("role", choices=auth.ROLES)
    command.set_defaults(handler=set_role)

    command = commands.add_parser("create-token", help="create an ingest token (printed once)", parents=[db_option])
    command.add_argument("name")
    command.set_defaults(handler=create_token)

    command = commands.add_parser("list-tokens", help="list API tokens (never the secret)", parents=[db_option])
    command.set_defaults(handler=list_tokens)

    command = commands.add_parser("revoke-token", help="revoke an API token", parents=[db_option])
    command.add_argument("id", type=int)
    command.set_defaults(handler=revoke_token)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        init_db(args.db)
        with connect(args.db) as conn:
            args.handler(conn, args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
