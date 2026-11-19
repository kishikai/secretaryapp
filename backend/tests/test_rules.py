import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.rules import PATTERNS, find_pattern, words


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


def post(client, text, source="clipboard", confidence=None, **extra):
    payload = {"message_text": text, "source": source, **extra}
    if source == "ocr":
        payload["confidence"] = 0.9 if confidence is None else confidence
    response = client.post("/messages", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def make_rule(client, **fields):
    response = client.post("/rules", json={"name": "rule", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def keyword_rule(client, *keywords, **fields):
    return make_rule(client, kind="keyword", keywords=list(keywords), **fields)


def pattern_rule(client, pattern, **fields):
    return make_rule(client, kind="pattern", pattern=pattern, **fields)


def notifications(client, **params):
    response = client.get("/notifications", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def match_rows(db_path, rule_id=None):
    with sqlite3.connect(db_path) as conn:
        if rule_id is None:
            return conn.execute("SELECT message_id, rule_id FROM message_matches").fetchall()
        return conn.execute(
            "SELECT message_id, rule_id FROM message_matches WHERE rule_id = ?", (rule_id,)
        ).fetchall()


# --- words and patterns -------------------------------------------------------------------


def test_words_fold_case_and_accents():
    assert words("Café, NAÏVE  Résumé!") == ["cafe", "naive", "resume"]
    assert words("Sige, see you mamaya") == ["sige", "see", "you", "mamaya"]
    assert words("!!!") == []


MUST_MATCH = {
    "url": ["see https://example.com/a?b=1 now", "go to www.example.com", "http://x.io"],
    "email": ["mail juan.dela-cruz+tag@example.co.ph please", "a@b.com"],
    "phone": ["call 0917 123 4567", "+63 917 123 4567", "09171234567", "text 0917-123-4567"],
    "money": ["₱1,500", "PHP 200", "P50", "$12.50", "300 pesos", "pay 1,500.00 PHP", "5 dollars"],
    "time": ["6pm", "6 pm", "6:30pm", "18:30", "9:05 AM", "at 6:30"],
    "date": ["2026-11-19", "11/19", "11/19/2026", "Nov 19", "November 19", "nov. 19", "on NOV 5!"],
}
MUST_NOT_MATCH = {
    "url": ["example.com", "http:// nothing"],
    "email": ["a@b", "@example.com"],
    "phone": ["2026-11-19", "6:30", "12345", "1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6"],
    "money": ["6pm", "Present:", "Step 5", "I have 300 apples"],
    "time": ["13pm", "25:00", "18:60", "2026-11-19"],
    "date": ["Novel 19", "13/45", "6:30"],
}


@pytest.mark.parametrize("name, text", [(n, t) for n, ts in MUST_MATCH.items() for t in ts])
def test_pattern_matches(name, text):
    assert find_pattern(name, text) is not None


@pytest.mark.parametrize("name, text", [(n, t) for n, ts in MUST_NOT_MATCH.items() for t in ts])
def test_pattern_does_not_match(name, text):
    assert find_pattern(name, text) is None


def test_pattern_matched_text_is_first_match():
    assert find_pattern("money", "pay ₱1,500 or $20") == "₱1,500"
    assert find_pattern("phone", "call +63 917 123 4567 today") == "+63 917 123 4567"
    assert find_pattern("url", "x https://a.io/b y") == "https://a.io/b"


ADVERSARIAL = [
    "1 " * 5000,
    "a" * 10000 + "@",
    "http://" + "a" * 9990,
    "1," * 5000,
    "a@" + "b." * 4000,
    "1-" * 5000,
    "6" * 10000,
    "P " * 5000,
    "www." * 2500,
    "Nov " * 2500,
    "1/" * 5000,
    "$" + "1," * 5000,
]


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_patterns_are_fast_on_adversarial_input(name):
    for text in ADVERSARIAL:
        assert len(text) <= 10001
        start = time.perf_counter()
        find_pattern(name, text)
        assert time.perf_counter() - start < 0.2, (name, text[:20])


# --- keyword rules ------------------------------------------------------------------------


def test_keyword_whole_words_and_phrases(client):
    meet = keyword_rule(client, "meet", category="meetings")
    see = keyword_rule(client, "see you")
    first = post(client, "Team meeting at 6")
    second = post(client, "Sige, see you mamaya")
    third = post(client, "Let's MEET tomorrow")
    fourth = post(client, "See? Not you")
    assert first["categories"] == []
    assert second["categories"] == []
    assert third["categories"] == ["meetings"]
    with sqlite3.connect(client.app.state.db_path) as conn:
        matches = conn.execute("SELECT message_id, rule_id, matched_text FROM message_matches").fetchall()
    assert sorted(matches) == sorted(
        [(second["id"], see["id"], "see you"), (third["id"], meet["id"], "meet")]
    )
    assert fourth["id"] not in {m[0] for m in matches}


def test_keyword_ignores_accents_and_returns_stored_phrase(client):
    rule = keyword_rule(client, "Café", "other")
    post(client, "meet at the CAFE")
    with sqlite3.connect(client.app.state.db_path) as conn:
        assert conn.execute("SELECT rule_id, matched_text FROM message_matches").fetchall() == [
            (rule["id"], "Café")
        ]


def test_pattern_rule_matches_message(client):
    pattern_rule(client, "phone", category="contact")
    stored = post(client, "call me on 0917 123 4567")
    assert stored["categories"] == ["contact"]


# --- validation ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "kind": "keyword"},
        {"name": "x", "kind": "keyword", "keywords": []},
        {"name": "x", "kind": "keyword", "keywords": ["a"], "pattern": "url"},
        {"name": "x", "kind": "pattern"},
        {"name": "x", "kind": "pattern", "pattern": "url", "keywords": ["a"]},
        {"name": "x", "kind": "pattern", "pattern": "regex"},
        {"name": "x", "kind": "keyword", "keywords": ["!!!"]},
        {"name": "x", "kind": "keyword", "keywords": ["ok", "  "]},
        {"name": "x", "kind": "keyword", "keywords": ["a"] * 51},
        {"name": "x", "kind": "keyword", "keywords": ["a" * 101]},
        {"name": "", "kind": "keyword", "keywords": ["a"]},
        {"name": "x", "kind": "keyword", "keywords": ["a"], "category": ""},
        {"name": "x", "kind": "keyword", "keywords": ["a"], "category": "c" * 51},
        {"name": "x", "kind": "keyword", "keywords": ["a"], "chat_id": "c" * 201},
        {"name": "x", "kind": "regex", "keywords": ["a"]},
        {"name": "x", "kind": "keyword", "keywords": ["a"], "regex": ".*"},
    ],
)
def test_invalid_rules_are_422(client, payload):
    assert client.post("/rules", json=payload).status_code == 422


def test_rule_response_shape(client):
    rule = keyword_rule(client, "chairs", notify=True, chat_id="g1", category="setup")
    assert rule["keywords"] == ["chairs"]
    assert rule["notify"] is True and rule["enabled"] is True
    assert rule["pattern"] is None and rule["chat_id"] == "g1" and rule["created_at"]
    assert client.get(f"/rules/{rule['id']}").json() == rule
    assert client.get("/rules").json() == [rule]
    assert client.get("/rules/999").status_code == 404


# --- notifications ------------------------------------------------------------------------


def test_notify_rule_creates_one_notification(client):
    rule = keyword_rule(client, "chairs", notify=True, name="Chairs")
    first = post(client, "Bring the extra chairs please", chat_id="g1")
    again = post(client, "Bring the extra chairs please", chat_id="g1")
    assert again["duplicate"] is True
    (item,) = notifications(client)
    assert item["rule_id"] == rule["id"] and item["rule_name"] == "Chairs"
    assert item["message_id"] == first["id"]
    assert item["message_text"] == "Bring the extra chairs please"
    assert item["chat_id"] == "g1" and item["matched_text"] == "chairs"
    assert item["created_at"] and item["read_at"] is None


def test_non_notify_rule_tags_without_notification(client):
    keyword_rule(client, "chairs", category="setup")
    stored = post(client, "Bring the extra chairs please")
    assert stored["categories"] == ["setup"]
    assert notifications(client) == []


def test_ocr_misread_then_clipboard_fix_matches_and_notifies_once(client):
    keyword_rule(client, "chairs", notify=True, category="setup")
    ocr = post(client, "Bring the extra chalrs please", source="ocr")
    assert ocr["categories"] == []
    assert notifications(client) == []
    clip = post(client, "Bring the extra chairs please")
    assert clip["match"] == "fuzzy" and clip["id"] == ocr["id"]
    assert clip["categories"] == ["setup"]
    assert len(notifications(client)) == 1
    post(client, "Bring the extra chairs please")
    assert len(notifications(client)) == 1


def test_replaced_text_that_stops_matching_removes_match_but_keeps_notification(client, db_path):
    keyword_rule(client, "chairs", notify=True, category="setup")
    ocr = post(client, "Bring the extra chairs please", source="ocr", confidence=0.5)
    assert ocr["categories"] == ["setup"]
    assert len(notifications(client)) == 1
    better = post(client, "Bring the extra chalrs please", source="ocr", confidence=0.9)
    assert better["id"] == ocr["id"] and better["message_text"].endswith("chalrs please")
    assert better["categories"] == []
    assert match_rows(db_path) == []
    assert len(notifications(client)) == 1


def test_unchanged_match_does_not_reevaluate(client, monkeypatch):
    import app.rules as rules_module

    post(client, "Bring the extra chairs please", source="ocr", confidence=0.7)
    calls = []
    monkeypatch.setattr(rules_module, "evaluate", lambda *args, **kwargs: calls.append(args))
    post(client, "Bring the extra chairs please", source="ocr", confidence=0.5)  # lower confidence
    post(client, "Bring the extra chairs please", source="ocr", confidence=0.9)  # same text, better
    post(client, "Bring the extra chairs please")  # clipboard replaces OCR with identical text
    assert calls == []
    post(client, "Bring the extra chairs please!")  # new exact text
    assert len(calls) == 1


def test_chat_scoped_and_disabled_rules(client):
    keyword_rule(client, "chairs", chat_id="g1", category="scoped")
    keyword_rule(client, "chairs", enabled=False, category="off")
    keyword_rule(client, "chairs", category="all")
    assert post(client, "extra chairs", chat_id="g1")["categories"] == ["all", "scoped"]
    assert post(client, "more chairs", chat_id="g2")["categories"] == ["all"]
    assert post(client, "more chairs, no chat")["categories"] == ["all"]


# --- backfill, PATCH, DELETE ----------------------------------------------------------------


def test_backfill_tags_old_messages_without_notifications(client, db_path):
    post(client, "bring chairs")
    post(client, "nothing here")
    post(client, "more chairs", chat_id="g1")
    rule = keyword_rule(client, "chairs", notify=True, category="setup")
    assert [m["categories"] for m in client.get("/messages").json()] == [[], [], []]
    response = client.post(f"/rules/{rule['id']}/backfill")
    assert response.status_code == 200
    assert response.json() == {"rule_id": rule["id"], "matching_messages": 2}
    assert [m["categories"] for m in client.get("/messages").json()] == [["setup"], [], ["setup"]]
    assert notifications(client) == []
    # Idempotent, and a later new message still notifies.
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 2
    post(client, "yet more chairs")
    assert len(notifications(client)) == 1


def test_backfill_respects_chat_and_removes_stale_matches(client):
    post(client, "chairs a", chat_id="g1")
    post(client, "chairs b", chat_id="g2")
    rule = keyword_rule(client, "chairs", chat_id="g1")
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 1
    client.patch(f"/rules/{rule['id']}", json={"chat_id": "g2"})
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 1
    assert [m["message_text"] for m in client.get("/messages", params={"category": "x"}).json()] == []
    client.patch(f"/rules/{rule['id']}", json={"enabled": False})
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 0


def test_backfill_unknown_rule_is_404(client):
    assert client.post("/rules/999/backfill").status_code == 404


def test_patch_merges_and_does_not_recompute(client):
    rule = keyword_rule(client, "chairs", category="setup")
    post(client, "bring chairs")
    patched = client.patch(f"/rules/{rule['id']}", json={"keywords": ["tables"], "notify": True})
    assert patched.status_code == 200
    body = patched.json()
    assert body["keywords"] == ["tables"] and body["notify"] is True and body["category"] == "setup"
    # Existing matches stay until a backfill.
    assert client.get("/messages").json()[0]["categories"] == ["setup"]
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 0
    assert client.get("/messages").json()[0]["categories"] == []
    cleared = client.patch(f"/rules/{rule['id']}", json={"category": None}).json()
    assert cleared["category"] is None


def test_patch_revalidates_and_404s(client):
    rule = keyword_rule(client, "chairs")
    url = f"/rules/{rule['id']}"
    assert client.patch(url, json={"kind": "pattern"}).status_code == 422
    assert client.patch(url, json={"kind": "pattern", "pattern": "url"}).status_code == 422  # keywords remain
    assert client.patch(url, json={"keywords": ["!!"]}).status_code == 422
    assert client.patch(url, json={"name": None}).status_code == 422
    assert client.patch(url, json={"bogus": 1}).status_code == 422
    assert client.get(url).json() == rule
    switched = client.patch(url, json={"kind": "pattern", "pattern": "url", "keywords": None})
    assert switched.status_code == 200
    assert switched.json()["pattern"] == "url" and switched.json()["keywords"] is None
    assert client.patch("/rules/999", json={"name": "x"}).status_code == 404


def test_delete_removes_matches_and_notifications(client, db_path):
    rule = keyword_rule(client, "chairs", notify=True, category="setup")
    other = keyword_rule(client, "chairs", notify=True, category="other")
    post(client, "bring chairs")
    assert len(notifications(client)) == 2
    assert client.delete(f"/rules/{rule['id']}").status_code == 204
    assert client.get(f"/rules/{rule['id']}").status_code == 404
    assert match_rows(db_path, rule["id"]) == []
    assert [n["rule_id"] for n in notifications(client)] == [other["id"]]
    assert client.get("/messages").json()[0]["categories"] == ["other"]
    assert client.delete(f"/rules/{rule['id']}").status_code == 404


# --- notifications API ----------------------------------------------------------------------


def test_notification_read_flow(client):
    keyword_rule(client, "chairs", notify=True)
    for n in range(3):
        post(client, f"chairs {n}")
    items = notifications(client)
    assert len(items) == 3 and [i["id"] for i in items] == sorted((i["id"] for i in items), reverse=True)
    target = items[1]["id"]
    first = client.post(f"/notifications/{target}/read")
    assert first.status_code == 200 and first.json()["read_at"]
    time.sleep(0.01)
    second = client.post(f"/notifications/{target}/read")
    assert second.json()["read_at"] == first.json()["read_at"]
    assert [i["id"] for i in notifications(client, unread="true")] == [items[0]["id"], items[2]["id"]]
    assert [i["id"] for i in notifications(client, unread="false")] == [target]
    assert client.post("/notifications/read-all").json() == {"marked": 2}
    assert client.post("/notifications/read-all").json() == {"marked": 0}
    assert notifications(client, unread="true") == []
    assert client.post("/notifications/999/read").status_code == 404


def test_notification_pagination(client):
    keyword_rule(client, "chairs", notify=True)
    for n in range(5):
        post(client, f"chairs {n}")
    page = notifications(client, limit=2)
    assert len(page) == 2
    rest = notifications(client, limit=10, before_id=page[-1]["id"])
    assert [i["id"] for i in page + rest] == sorted((i["id"] for i in page + rest), reverse=True)
    assert len(page + rest) == 5
    assert client.get("/notifications", params={"limit": 0}).status_code == 422
    assert client.get("/notifications", params={"limit": 501}).status_code == 422


# --- GET /messages ------------------------------------------------------------------------


def test_messages_categories_and_filter(client):
    keyword_rule(client, "chairs", category="setup")
    keyword_rule(client, "chairs", category="alpha")
    keyword_rule(client, "chairs")  # no category
    pattern_rule(client, "time", category="schedule")
    post(client, "chairs at 6pm")
    post(client, "just chairs")
    post(client, "hello")
    rows = {m["message_text"]: m["categories"] for m in client.get("/messages").json()}
    assert rows == {
        "chairs at 6pm": ["alpha", "schedule", "setup"],
        "just chairs": ["alpha", "setup"],
        "hello": [],
    }

    def texts(**params):
        return [m["message_text"] for m in client.get("/messages", params=params).json()]

    assert texts(category="schedule") == ["chairs at 6pm"]
    assert texts(category="setup") == ["just chairs", "chairs at 6pm"]
    assert texts(category="setup", q="6pm") == ["chairs at 6pm"]
    assert texts(category="nope") == []
    assert client.get("/messages", params={"category": "c" * 51}).status_code == 422


# --- migration ----------------------------------------------------------------------------

V04_SCHEMA = """
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
CREATE INDEX idx_messages_created ON messages (created_at);
CREATE INDEX idx_messages_chat_id ON messages (chat_id, id);
CREATE VIRTUAL TABLE messages_fts USING fts5(
    message_text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, message_text) VALUES (new.id, new.message_text); END;
"""


def test_v04_database_migrates_and_backfills(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(V04_SCHEMA)
        conn.execute(
            "INSERT INTO messages (message_text, source, content_hash, match_key) "
            "VALUES ('Old note: bring chairs', 'clipboard', 'h1', 'oldnotebringchairs')"
        )
        conn.execute(
            "INSERT INTO messages (message_text, source, content_hash, match_key) "
            "VALUES ('Unrelated', 'ocr', 'h2', 'unrelated')"
        )
    client = TestClient(create_app(db_path))
    client = TestClient(create_app(db_path))  # starting twice is harmless
    rule = keyword_rule(client, "chairs", notify=True, category="setup")
    assert client.post(f"/rules/{rule['id']}/backfill").json()["matching_messages"] == 1
    old = {m["message_text"]: m["categories"] for m in client.get("/messages").json()}
    assert old == {"Old note: bring chairs": ["setup"], "Unrelated": []}
    assert notifications(client) == []
    post(client, "new chairs")
    assert len(notifications(client)) == 1
