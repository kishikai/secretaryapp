"""Latency of the main API calls on a large message history.

Run: cd backend && python -m eval.perf_bench [--messages 100000]
Seeds a throwaway database directly (fast), then times each call through the real,
signed-in API with TestClient and prints the median of several runs.
"""
import argparse
import os
import random
import statistics
import tempfile
import time

os.environ.setdefault("SECRETARY_FRONTEND_DIST", "/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import connect  # noqa: E402
from app.main import create_app  # noqa: E402
from app.pipeline import MessageIn, match_key  # noqa: E402

PHRASES = [
    "Sige, see you mamaya", "Nasa'n na kayo?", "Meet at 6:30pm sa cafe", "Present na ako",
    "Bring 2 extra chairs!", "Lunch is ₱250 each", "Pay by Friday please", "Late ako ng 10 minutes",
    "Who's coming tomorrow?", "Thank you po!", "Ok noted", "Practice moved to Nov 21",
    "Absent si Sam today", "Pasabay naman", "Reminder: meeting at 9 AM", "Send the files sa drive",
]


def seed(db_path: str, count: int, chats: int) -> None:
    rng = random.Random(7)
    with connect(db_path) as conn:
        for i in range(count):
            text = f"{rng.choice(PHRASES)} {rng.choice(PHRASES)} #{i}"
            source = "ocr" if i % 4 == 0 else "clipboard"
            message = MessageIn(message_text=text, source=source, chat_id=f"chat-{i % chats}",
                                confidence=0.95 if source == "ocr" else None)
            row_id = conn.execute(
                "INSERT INTO messages (chat_id, message_text, source, confidence, content_hash, match_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message.chat_id, message.message_text, source, message.confidence,
                 message.content_hash(), match_key(text)),
            ).lastrowid
            conn.execute(
                "INSERT INTO message_variants (message_id, source, message_text, confidence) VALUES (?, ?, ?, ?)",
                (row_id, source, text, message.confidence),
            )
        auth_conn = conn
        auth_conn.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                          ("bench", auth.hash_password("benchmark password")))


def timed(fn, runs: int = 7) -> float:
    fn()  # warm-up
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--chats", type=int, default=20)
    args = parser.parse_args()

    db_path = os.path.join(tempfile.mkdtemp(), "bench.db")
    app = create_app(db_path)
    start = time.perf_counter()
    seed(db_path, args.messages, args.chats)
    print(f"Seeded {args.messages:,} messages in {args.chats} chats in {time.perf_counter() - start:.1f}s")

    client = TestClient(app, headers={"X-Requested-With": "secretary"})
    assert client.post("/auth/login", json={"username": "bench", "password": "benchmark password"}).status_code == 200
    rules = [
        {"name": "Attendance", "kind": "keyword", "keywords": ["present", "absent"], "category": "attendance", "notify": True},
        {"name": "Money", "kind": "pattern", "pattern": "money", "category": "money"},
        {"name": "Times", "kind": "pattern", "pattern": "time", "category": "schedule"},
    ]
    for rule in rules:
        rule_id = client.post("/rules", json=rule).json()["id"]
        start = time.perf_counter()
        client.post(f"/rules/{rule_id}/backfill")
        print(f"Backfill {rule['name']:<10} {time.perf_counter() - start:6.1f}s")

    def get(path, **params):
        return lambda: client.get(path, params=params).raise_for_status()

    counter = iter(range(10**9))
    checks = [
        ("GET /messages (newest 50)", get("/messages")),
        ("GET /messages q=chairs", get("/messages", q="chairs")),
        ("GET /messages q=sige mamaya", get("/messages", q="sige mamaya")),
        ("GET /messages chat_id=chat-3", get("/messages", chat_id="chat-3")),
        ("GET /messages category=money", get("/messages", category="money")),
        ("GET /messages source=ocr q=late", get("/messages", source="ocr", q="late")),
        ("GET /messages before_id=50000", get("/messages", before_id=50000)),
        ("GET /stats", get("/stats")),
        ("GET /chats", get("/chats")),
        ("GET /notifications", get("/notifications")),
        ("POST /messages (new, 5k-msg chat)",
         lambda: client.post("/messages", json={"message_text": f"Present na kami lahat {next(counter)}",
                                                "source": "clipboard", "chat_id": "chat-1"}).raise_for_status()),
    ]
    print(f"\n{'Call':<36}{'median ms':>10}")
    for name, fn in checks:
        print(f"{name:<36}{timed(fn):>10.1f}")


if __name__ == "__main__":
    main()
