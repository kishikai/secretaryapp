"""Consistent SQLite backups: `python -m app.backup [--db PATH] [--dir DIR] [--keep N]`."""
import argparse
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional

DEFAULT_KEEP = 14


def _readonly_connect(db_path: str) -> sqlite3.Connection:
    # as_uri() percent-escapes spaces, '?' and '#' so they stay part of the path.
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:
        raise FileNotFoundError(f"Cannot open database {db_path}: {exc}") from exc


def backup_database(db_path: str, backup_dir: str, keep: int = DEFAULT_KEEP) -> pathlib.Path:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    directory = pathlib.Path(backup_dir)
    src = _readonly_connect(db_path)
    try:
        # mode=ro still succeeds on some missing paths until first use, so force an open.
        try:
            src.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.OperationalError as exc:
            raise FileNotFoundError(f"Cannot open database {db_path}: {exc}") from exc
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
        final = directory / f"secretary-{stamp}.db"
        partial = directory / f"{final.name}.partial"
        dest = sqlite3.connect(partial)
        try:
            src.backup(dest)
        except BaseException:
            dest.close()
            partial.unlink(missing_ok=True)
            raise
        dest.close()
    finally:
        src.close()

    check = sqlite3.connect(partial)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Backup failed integrity check: {result}")
    os.replace(partial, final)

    backups = sorted(p for p in directory.glob("secretary-*.db") if p.is_file())
    for old in backups[:-keep]:
        old.unlink()
    return final


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the Secretary SQLite database.")
    parser.add_argument("--db", default=os.environ.get("SECRETARY_DB_PATH", "data/secretary.db"))
    parser.add_argument("--dir", default="backups")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args(argv)
    try:
        path = backup_database(args.db, args.dir, args.keep)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Backup failed: {exc}".splitlines()[0], file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
