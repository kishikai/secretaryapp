import sqlite3

import pytest
from app.pipeline import digits, match_key


@pytest.fixture
def client(tmp_path, make_client):
    return make_client(str(tmp_path / "test.db"))


def post(client, text, source="clipboard", confidence=None, **extra):
    payload = {"message_text": text, "source": source, **extra}
    if source == "ocr":
        payload["confidence"] = 0.9 if confidence is None else confidence
    response = client.post("/messages", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def rows(client):
    return client.get("/messages").json()


def test_helpers():
    assert match_key("Meet at the cafe, 6pm?") == "meetatthecafe6pm"
    assert match_key("ＡＢ１ ２") == "ab12"
    assert digits("ab１ 2,3x") == "123"


def test_clipboard_then_ocr_is_normalised_match(client):
    first = post(client, "Meet at the cafe at 6pm, ok?")
    second = post(client, "meet at the cafe at 6pm ok", source="ocr", confidence=0.7)
    assert first["match"] is None
    assert second["match"] == "normalised"
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    (row,) = rows(client)
    assert row["message_text"] == "Meet at the cafe at 6pm, ok?"
    assert row["source"] == "clipboard"
    assert row["sources"] == ["clipboard", "ocr"]


def test_ocr_then_clipboard_upgrades_text(client):
    first = post(client, "meet at the cafe at 6pm ok", source="ocr", confidence=0.8)
    second = post(client, "Meet at the cafe at 6pm, ok?")
    assert second["match"] == "normalised"
    assert second["id"] == first["id"]
    (row,) = rows(client)
    assert row["message_text"] == "Meet at the cafe at 6pm, ok?"
    assert row["source"] == "clipboard"
    assert row["confidence"] is None
    variants = client.get(f"/messages/{first['id']}/variants").json()
    assert [(v["source"], v["message_text"], v["confidence"]) for v in variants] == [
        ("ocr", "meet at the cafe at 6pm ok", 0.8),
        ("clipboard", "Meet at the cafe at 6pm, ok?", None),
    ]
    assert set(variants[0]) == {"id", "source", "message_text", "confidence", "created_at"}


@pytest.mark.parametrize("high_first", [True, False])
def test_ocr_fuzzy_merge_keeps_higher_confidence(client, high_first):
    good = ("Bring the extra chairs please", 0.95)
    bad = ("Bring the extra chalrs please", 0.6)
    first, second = (good, bad) if high_first else (bad, good)
    post(client, first[0], source="ocr", confidence=first[1])
    result = post(client, second[0], source="ocr", confidence=second[1])
    assert result["match"] == "fuzzy"
    (row,) = rows(client)
    assert row["message_text"] == good[0]
    assert row["confidence"] == good[1]
    assert row["sources"] == ["ocr"]
    assert len(client.get(f"/messages/{row['id']}/variants").json()) == 2


def test_exact_repeat_is_exact_match(client):
    post(client, "hello there")
    again = post(client, "hello there")
    assert again["match"] == "exact"
    assert len(rows(client)) == 1
    assert len(client.get(f"/messages/{again['id']}/variants").json()) == 1


def test_different_digits_are_not_merged(client):
    post(client, "Meet at the cafe at 6pm tomorrow")
    result = post(client, "Meet at the cafe at 7pm tomorrow", source="ocr")
    assert result["match"] is None
    assert len(rows(client)) == 2


def test_clipboard_vs_clipboard_never_fuzzy(client):
    post(client, "Bring the extra chairs please")
    result = post(client, "Bring the extra chalrs please")
    assert result["match"] is None
    assert len(rows(client)) == 2


def test_short_texts_need_exact_key(client):
    post(client, "okay po", source="ocr")
    result = post(client, "okey po", source="ocr")
    assert result["match"] is None
    assert len(rows(client)) == 2


def test_different_chats_never_merge(client):
    post(client, "Meet at the cafe at 6pm, ok?", chat_id="a")
    result = post(client, "meet at the cafe at 6pm ok", source="ocr", chat_id="b")
    assert result["match"] is None
    assert len(rows(client)) == 2


def test_sender_conflict_not_merged_but_one_sided_sender_merges(client):
    post(client, "Meet at the cafe at 6pm, ok?", sender="Ana")
    conflict = post(client, "meet at the cafe at 6pm ok", source="ocr", sender="Ben")
    assert conflict["match"] is None
    one_sided = post(client, "meet at the cafe at 6pm ok!", source="ocr")
    assert one_sided["match"] == "normalised"
    assert one_sided["id"] == 1
    assert len(rows(client)) == 2


def test_variants_404_for_missing_message(client):
    assert client.get("/messages/999/variants").status_code == 404


def test_migration_from_pre_sprint4_schema(tmp_path, make_client):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            message_text TEXT NOT NULL,
            sender TEXT,
            timestamp TEXT,
            source TEXT NOT NULL CHECK (source IN ('clipboard', 'ocr')),
            confidence REAL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            content_hash TEXT
        );
        CREATE UNIQUE INDEX idx_messages_content_hash ON messages (content_hash);
        INSERT INTO messages (chat_id, message_text, source, confidence, content_hash)
            VALUES ('g', 'Meet at the cafe at 6pm, ok?', 'clipboard', NULL, 'h1'),
                   ('g', 'bring the extra chairs please', 'ocr', 0.7, 'h2');
        """
    )
    conn.commit()
    conn.close()

    client = make_client(path)
    old = {row["id"]: row for row in rows(client)}
    assert old[1]["match_key"] == "meetatthecafeat6pmok"
    assert old[2]["match_key"] == "bringtheextrachairsplease"
    assert old[1]["sources"] == ["clipboard"]
    assert old[2]["sources"] == ["ocr"]

    merged = post(client, "Meet at the cafe at 6pm ok", source="ocr", chat_id="g")
    assert merged["id"] == 1
    assert merged["match"] == "normalised"
    assert merged["sources"] == ["clipboard", "ocr"]
    fuzzy = post(client, "Bring the extra chalrs please", source="ocr", confidence=0.9, chat_id="g")
    assert fuzzy["id"] == 2
    assert fuzzy["match"] == "fuzzy"
    assert len(rows(client)) == 2

    # Starting again on the migrated database is harmless.
    make_client(path)
    assert len(rows(make_client(path))) == 2


def test_ocr_endpoint_returns_match_and_sources(tmp_path, make_client):
    from tests.test_ocr import FakeEngine, line, png

    engine = FakeEngine([line("Meet at the cafe at 6pm ok", 20, 100, 300, 120, conf=0.8)])
    client = make_client(str(tmp_path / "ocr.db"), ocr_engine=engine)
    post(client, "Meet at the cafe at 6pm, ok?", chat_id="g")
    body = client.post(
        "/ocr/screenshots", files={"file": ("s.png", png(), "image/png")}, data={"chat_id": "g"}
    ).json()
    (item,) = body["messages"]
    assert item["match"] == "normalised"
    assert item["duplicate"] is True
    assert item["sources"] == ["clipboard", "ocr"]
    assert item["message_text"] == "Meet at the cafe at 6pm, ok?"


def test_clipboard_copies_differing_only_in_punctuation_stay_separate(client):
    # "Ok?" vs "Ok." style: clipboard text is exact, so these are two different messages.
    post(client, "See you there?", chat_id="g1")
    second = post(client, "See you there.", chat_id="g1")
    assert second["match"] is None
    assert len(rows(client)) == 2


def test_concurrent_insert_of_same_text_is_treated_as_exact_match(tmp_path, monkeypatch):
    # Simulates a race: another request stores the same text after our lookup found nothing.
    import app.pipeline as pipeline
    from app.db import connect, init_db

    db_path = str(tmp_path / "race.db")
    init_db(db_path)
    message = pipeline.MessageIn(message_text="Same text arriving twice", source="ocr", confidence=0.8, chat_id="g1")
    with connect(db_path) as conn:
        first, _ = pipeline.store_message(conn, message)
    monkeypatch.setattr(pipeline, "_find_match", lambda *args: (None, None))
    with connect(db_path) as conn:
        stored, match = pipeline.store_message(conn, message)
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert match == "exact" and stored["id"] == first["id"] and count == 1
