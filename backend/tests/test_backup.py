import re
import sqlite3

import pytest

from app.backup import backup_database, main
from app.db import init_db

NAME = re.compile(r"^secretary-\d{8}T\d{6}-\d{6}Z\.db$")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "src dir" / "secretary.db")
    init_db(path)
    with sqlite3.connect(path) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO messages (message_text, source, content_hash) VALUES (?, 'clipboard', ?)",
                (f"message {i}", f"h{i}"),
            )
    return path


def read_texts(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT message_text FROM messages ORDER BY id")]
    finally:
        conn.close()


def test_backup_is_a_valid_copy(db_path, tmp_path):
    dest = backup_database(db_path, str(tmp_path / "bk"))
    assert NAME.match(dest.name)
    assert dest.parent == tmp_path / "bk"
    assert read_texts(str(dest)) == ["message 0", "message 1", "message 2"]
    conn = sqlite3.connect(dest)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # The FTS index travels with the backup.
    assert conn.execute(
        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'message'"
    ).fetchone()[0] == 3
    conn.close()
    assert list((tmp_path / "bk").glob("*.partial")) == []


def test_two_backups_in_a_row_do_not_collide(db_path, tmp_path):
    first = backup_database(db_path, str(tmp_path / "bk"))
    second = backup_database(db_path, str(tmp_path / "bk"))
    assert first != second and first.exists() and second.exists()


def test_path_with_special_characters(tmp_path):
    path = str(tmp_path / "we?ird #dir" / "my db.db")
    init_db(path)
    dest = backup_database(path, str(tmp_path / "bk"))
    assert read_texts(str(dest)) == []


def test_prune_keeps_newest_and_leaves_other_files(db_path, tmp_path):
    bk = tmp_path / "bk"
    bk.mkdir()
    for i in range(5):
        (bk / f"secretary-20200101T00000{i}-000000Z.db").write_text("old")
    (bk / "notes.txt").write_text("keep me")
    (bk / "other.db").write_text("keep me too")
    (bk / "secretary-extra.txt").write_text("keep me three")
    dest = backup_database(db_path, str(bk), keep=3)
    names = sorted(p.name for p in bk.glob("secretary-*.db"))
    assert names == [
        "secretary-20200101T000003-000000Z.db",
        "secretary-20200101T000004-000000Z.db",
        dest.name,
    ]
    assert (bk / "notes.txt").read_text() == "keep me"
    assert (bk / "other.db").exists()
    assert (bk / "secretary-extra.txt").exists()


def test_missing_source_raises_and_creates_nothing(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        backup_database(str(missing), str(tmp_path / "bk"))
    assert not missing.exists()
    assert list(tmp_path.glob("*.db")) == []
    assert not (tmp_path / "bk").exists() or list((tmp_path / "bk").iterdir()) == []


def test_missing_source_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(str(tmp_path / "no" / "such" / "x.db"), str(tmp_path / "bk"))
    assert not (tmp_path / "no").exists()


def test_keep_below_one_is_value_error(db_path, tmp_path):
    with pytest.raises(ValueError):
        backup_database(db_path, str(tmp_path / "bk"), keep=0)


def test_uncommitted_write_is_not_in_backup(db_path, tmp_path):
    writer = sqlite3.connect(db_path)
    writer.execute(
        "INSERT INTO messages (message_text, source, content_hash) VALUES ('uncommitted', 'ocr', 'hx')"
    )
    try:
        dest = backup_database(db_path, str(tmp_path / "bk"))
    finally:
        writer.rollback()
        writer.close()
    assert read_texts(str(dest)) == ["message 0", "message 1", "message 2"]


def test_cli_success_prints_path(db_path, tmp_path, capsys):
    code = main(["--db", db_path, "--dir", str(tmp_path / "bk"), "--keep", "2"])
    out = capsys.readouterr()
    assert code == 0
    printed = out.out.strip()
    assert NAME.match(printed.rsplit("/", 1)[-1])
    assert read_texts(printed) == ["message 0", "message 1", "message 2"]


def test_cli_uses_env_default(db_path, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SECRETARY_DB_PATH", db_path)
    assert main(["--dir", str(tmp_path / "bk")]) == 0


def test_cli_missing_db_returns_1(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "nope.db"), "--dir", str(tmp_path / "bk")])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert not (tmp_path / "nope.db").exists()


def test_cli_bad_keep_returns_1(db_path, tmp_path, capsys):
    assert main(["--db", db_path, "--dir", str(tmp_path / "bk"), "--keep", "0"]) == 1
