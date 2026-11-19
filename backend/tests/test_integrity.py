import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.backup import backup_database
from app.db import connect
from tests.test_ocr import FakeEngine, line, png
from tests.test_rules import keyword_rule, notifications, pattern_rule, post


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def assert_healthy(path):
    """The database file is consistent: no corruption, no orphan rows, FTS index in step."""
    with connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchall()[0][0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # Raises sqlite3.DatabaseError if the index and the content table disagree.
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('integrity-check')")


def run_workload(db_path, make_client, tmp_path):
    """Clipboard and OCR messages with every kind of merge, rules, backfill, notifications."""
    engine = FakeEngine(
        [
            line("Remember to bring P250 for the field trip", 20, 100, 380, 120, 0.8),
            line("Lunch will be served at noon tomorrow", 20, 300, 380, 320, 0.85),
        ]
    )
    client = make_client(db_path, ocr_engine=engine)
    anon = TestClient(client.app)
    with connect(db_path) as conn:
        headers = {"Authorization": f"Bearer {auth.create_api_token(conn, 'collector')}"}

    attendance = keyword_rule(client, "present", "absent", category="attendance", notify=True)
    lunch = keyword_rule(client, "lunch", category="food", notify=True)
    pattern_rule(client, "time", category="schedule")

    matches = [
        post(client, "Present sir, see you at 9:00 am sharp")["match"],
        post(client, "Present sir, see you at 9:00 am sharp")["match"],
        post(client, "present sir see you at 9:00 am sharp!", source="ocr")["match"],
        post(client, "Please confirm attendance for the meeting tomorrow at 9am")["match"],
        post(client, "Please confirm attendance for the meeting tornorrow at 9am", source="ocr")["match"],
    ]
    assert matches == [None, "exact", "normalised", None, "fuzzy"]
    post(client, "Ok", chat_id="other")

    response = anon.post(
        "/ocr/screenshots", files={"file": ("s.png", png(), "image/png")}, headers=headers
    )
    assert response.status_code == 200 and len(response.json()["messages"]) == 2
    post(client, "Remember to bring P250 for the field trip")  # upgrades the OCR row

    # A rule created afterwards is backfilled over what is already stored.
    money = pattern_rule(client, "money", category="money", notify=True)
    assert client.post(f"/rules/{money['id']}/backfill").json()["matching_messages"] == 1

    assert len(notifications(client)) >= 2
    assert client.delete(f"/rules/{lunch['id']}").status_code == 204
    backup = backup_database(db_path, str(tmp_path / "bk"))
    return attendance, lunch, str(backup)


def test_integrity_after_a_mixed_workload(db_path, make_client, tmp_path):
    _, _, backup = run_workload(db_path, make_client, tmp_path)
    assert_healthy(db_path)
    assert_healthy(backup)
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM message_variants").fetchone()[0] > conn.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]


def test_deleting_a_rule_leaves_no_orphans(db_path, make_client):
    client = make_client(db_path)
    rule = keyword_rule(client, "lunch", notify=True)
    post(client, "Lunch at noon")
    post(client, "Lunch is cancelled", chat_id="b")
    assert len(notifications(client)) == 2
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM message_matches").fetchone()[0] == 2

    assert client.delete(f"/rules/{rule['id']}").status_code == 204

    with connect(db_path) as conn:
        for table in ("message_matches", "notifications"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE rule_id = ?", (rule["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert_healthy(db_path)


def test_foreign_keys_are_enforced(db_path, make_client):
    make_client(db_path)
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO message_variants (message_id, source, message_text) VALUES (9999, 'ocr', 'x')"
            )


def test_a_rule_with_matches_cannot_be_deleted_directly(db_path, make_client):
    client = make_client(db_path)
    rule = keyword_rule(client, "lunch", notify=True)
    post(client, "Lunch at noon")
    with connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM rules WHERE id = ?", (rule["id"],))
