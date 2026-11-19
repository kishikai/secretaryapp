import sqlite3

import pytest


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path, make_client):
    return make_client(db_path)


def post(client, text, source="clipboard", confidence=None, **extra):
    payload = {"message_text": text, "source": source, **extra}
    if source == "ocr":
        payload["confidence"] = 0.9 if confidence is None else confidence
    response = client.post("/messages", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def search(client, q, **params):
    response = client.get("/messages", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [m["message_text"] for m in response.json()]


def test_word_prefix_case_and_accent(client):
    post(client, "Team meeting at the café")
    post(client, "Something unrelated")
    assert search(client, "café") == ["Team meeting at the café"]
    assert search(client, "meet") == ["Team meeting at the café"]
    assert search(client, "MEETING") == ["Team meeting at the café"]
    assert search(client, "cafe") == ["Team meeting at the café"]
    assert search(client, "CAFÉ") == ["Team meeting at the café"]


def test_several_words_are_anded(client):
    post(client, "buy milk and eggs")
    post(client, "buy bread")
    assert search(client, "buy milk") == ["buy milk and eggs"]
    assert search(client, "milk bread") == []


def test_no_match_returns_empty_list(client):
    post(client, "hello world")
    assert search(client, "zebra") == []


def test_only_punctuation_is_422(client):
    for q in ("!!!", "  ", '"*"', "()"):
        response = client.get("/messages", params={"q": q})
        assert response.status_code == 422
    assert client.get("/messages", params={"q": "!!!"}).json()["detail"] == (
        "Search needs at least one letter or digit"
    )
    assert client.get("/messages", params={"q": ""}).status_code == 422
    assert client.get("/messages", params={"q": "a" * 201}).status_code == 422


@pytest.mark.parametrize(
    "q", ['"', "*", "AND", "OR", "NOT", "NEAR(", "-x", "col:val", "(", 'a" OR "b', "message_text:hello", "hello*"]
)
def test_fts_syntax_never_errors(client, q):
    post(client, "hello AND goodbye or near")
    assert client.get("/messages", params={"q": q}).status_code in (200, 422)
    if any(ch.isalnum() for ch in q):
        assert client.get("/messages", params={"q": q}).status_code == 200


def test_many_tokens_are_capped(client):
    post(client, "alpha beta")
    q = "alpha " + " ".join(f"w{i}" for i in range(30))
    assert client.get("/messages", params={"q": q}).status_code == 200
    # Tokens past the 20th are ignored, so a later non-matching word does not exclude the row.
    q = " ".join(["alpha"] * 20 + ["nomatch"])
    assert search(client, q) == ["alpha beta"]


def test_search_follows_normalised_upgrade(client):
    post(client, "meet at the cafe at 6pm ok", source="ocr", confidence=0.8)
    post(client, "Meet at the cafe at 6pm, ok?")
    assert len(client.get("/messages").json()) == 1
    assert search(client, "meet") == ["Meet at the cafe at 6pm, ok?"]
    assert search(client, "6pm") == ["Meet at the cafe at 6pm, ok?"]


def test_search_follows_fuzzy_upgrade(client):
    post(client, "Bring the extra chalrs please", source="ocr", confidence=0.6)
    assert search(client, "chalrs") == ["Bring the extra chalrs please"]
    result = post(client, "Bring the extra chairs please", source="ocr", confidence=0.95)
    assert result["match"] == "fuzzy"
    assert search(client, "chairs") == ["Bring the extra chairs please"]
    assert search(client, "chalrs") == []


def test_clipboard_upgrade_replaces_ocr_text_in_index(client):
    post(client, "Bring the extra chalrs please", source="ocr", confidence=0.6)
    result = post(client, "Bring the extra chairs please")
    assert result["match"] == "fuzzy"
    assert search(client, "chairs") == ["Bring the extra chairs please"]
    assert search(client, "chalrs") == []


def seed(client):
    post(client, "plan one", chat_id="a", sender="ann")
    post(client, "plan two", chat_id="b", sender="bob")
    post(client, "plan three here", source="ocr", chat_id="a", sender="bob")
    post(client, "other stuff", chat_id="a")


def test_filters(client):
    seed(client)
    def texts(**params):
        response = client.get("/messages", params=params)
        assert response.status_code == 200
        return [m["message_text"] for m in response.json()]

    assert texts(chat_id="a") == ["other stuff", "plan three here", "plan one"]
    assert texts(source="ocr") == ["plan three here"]
    assert texts(sender="bob") == ["plan three here", "plan two"]
    assert texts(chat_id="a", sender="bob", source="ocr") == ["plan three here"]
    assert texts(q="plan", chat_id="a") == ["plan three here", "plan one"]
    assert texts(q="plan", chat_id="nope") == []
    assert client.get("/messages", params={"source": "email"}).status_code == 422
    assert client.get("/messages", params={"chat_id": "x" * 201}).status_code == 422
    assert client.get("/messages", params={"sender": "x" * 201}).status_code == 422
    assert client.get("/messages", params={"before_id": 0}).status_code == 422


def test_filter_values_are_not_sql(client):
    seed(client)
    response = client.get("/messages", params={"chat_id": "a' OR '1'='1"})
    assert response.status_code == 200 and response.json() == []


def test_since_until(client, db_path):
    for text in ("one", "two", "three"):
        post(client, text)
    stamps = ["2024-01-01T10:00:00.000Z", "2024-01-01T11:00:00.500Z", "2024-01-01T12:00:00.000Z"]
    with sqlite3.connect(db_path) as conn:
        for message_id, stamp in enumerate(stamps, start=1):
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (stamp, message_id))

    def texts(**params):
        response = client.get("/messages", params=params)
        assert response.status_code == 200, response.text
        return [m["message_text"] for m in response.json()]

    assert texts(since="2024-01-01T11:00:00Z") == ["three", "two"]
    assert texts(since="2024-01-01T11:00:00.501Z") == ["three"]
    assert texts(until="2024-01-01T11:00:00.500Z") == ["one"]
    assert texts(until="2024-01-01T11:00:00.501Z") == ["two", "one"]
    assert texts(since="2024-01-01T10:30:00Z", until="2024-01-01T12:00:00Z") == ["two"]
    # Naive datetimes are UTC; offsets are converted.
    assert texts(since="2024-01-01T11:00:00") == ["three", "two"]
    assert texts(since="2024-01-01T13:00:00+02:00", until="2024-01-01T13:30:00+02:00") == ["two"]
    assert texts(q="two", since="2024-01-01T00:00:00Z") == ["two"]
    assert texts(q="two", since="2024-01-02T00:00:00Z") == []


@pytest.mark.parametrize(
    "since, until",
    [
        ("2024-01-02T00:00:00Z", "2024-01-01T00:00:00Z"),
        ("2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        ("2024-01-01T00:00:00", "2024-01-01T02:00:00+02:00"),
    ],
)
def test_since_not_before_until_is_422(client, since, until):
    assert client.get("/messages", params={"since": since, "until": until}).status_code == 422


def test_invalid_datetime_is_422(client):
    assert client.get("/messages", params={"since": "yesterday"}).status_code == 422


def test_before_id_pagination_walks_everything(client):
    for i in range(23):
        post(client, f"item number {i}")
    seen, before = [], None
    while True:
        params = {"limit": 5}
        if before is not None:
            params["before_id"] = before
        page = client.get("/messages", params=params).json()
        if not page:
            break
        seen.extend(m["id"] for m in page)
        before = page[-1]["id"]
    assert seen == list(range(23, 0, -1))


def test_response_shape_has_sources(client):
    post(client, "shape check")
    (row,) = client.get("/messages", params={"q": "shape"}).json()
    assert row["sources"] == ["clipboard"]
    assert {"id", "message_text", "chat_id", "created_at", "source"} <= set(row)


V03_SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    message_text TEXT NOT NULL,
    sender TEXT,
    timestamp TEXT,
    source TEXT NOT NULL CHECK (source IN ('clipboard', 'ocr')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    content_hash TEXT,
    match_key TEXT
);
CREATE TABLE message_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    source TEXT NOT NULL,
    message_text TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (message_id, source, message_text)
);
CREATE UNIQUE INDEX idx_messages_content_hash ON messages (content_hash);
CREATE INDEX idx_messages_chat_match ON messages (chat_id, match_key);
"""


def test_v03_database_is_migrated_and_searchable(db_path, make_client):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(V03_SCHEMA)
        conn.execute(
            "INSERT INTO messages (message_text, source, content_hash, match_key) "
            "VALUES ('Old café meeting', 'clipboard', 'h1', 'oldcafemeeting')"
        )
        conn.execute(
            "INSERT INTO messages (message_text, source, content_hash, match_key) "
            "VALUES ('Another old note', 'ocr', 'h2', 'anotheroldnote')"
        )
    client = make_client(db_path)
    assert search(client, "cafe") == ["Old café meeting"]
    assert search(client, "old") == ["Another old note", "Old café meeting"]
    post(client, "brand new message")
    assert search(client, "brand") == ["brand new message"]
    # Starting again must not duplicate index entries.
    client = make_client(db_path)
    assert search(client, "old") == ["Another old note", "Old café meeting"]
    with sqlite3.connect(db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_messages_created", "idx_messages_chat_id", "messages_fts_ai"} <= names
