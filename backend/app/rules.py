"""Automation engine: keyword and predefined-pattern rules, categories and notifications.

There are deliberately no user-supplied regular expressions (ReDoS risk while the API has no
authentication). Every pattern below uses bounded quantifiers and no nested quantifiers.
"""

import json
import re
import sqlite3
import unicodedata
from typing import Callable, Optional

BACKFILL_BATCH = 500


def words(text: str) -> list[str]:
    """Case-, accent- and punctuation-insensitive words of a text."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.findall(r"\w+", stripped)


_AMOUNT = r"(?:\d{1,3}(?:,\d{3}){1,6}|\d{1,12})(?:\.\d{1,2})?"
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(source, re.IGNORECASE)
    for name, source in {
        "url": r"(?:https?://|www\.)[^\s<>\"']{1,2048}",
        # The lookbehind makes a run of local-part characters be tried once, not at every position.
        "email": r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}",
        # 10-19 digits here; the post-check below narrows it to 10-15 and drops dates.
        "phone": r"(?<!\w)\+?\d(?:[ -]?\d){9,19}(?!\d)",
        "money": (
            rf"(?:(?:(?<!\w)(?:php|usd|p)|[₱$€£])\s?{_AMOUNT}(?!\d)"
            rf"|(?<![\d,.]){_AMOUNT}\s?(?:pesos?|php|usd|dollars?)(?!\w))"
        ),
        "time": (
            r"(?<![\w:])(?:(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s?[ap]\.?m"
            r"|(?:[01]?\d|2[0-3]):[0-5]\d)(?![\w:])"
        ),
        "date": (
            r"(?<![\w/.-])(?:\d{4}-(?:0[1-9]|1[0-2])-" + _DAY + r"(?!\d)"
            + r"|(?:0?[1-9]|1[0-2])/" + _DAY + r"(?:/(?:\d{4}|\d{2}))?(?![\w/])"
            + r"|" + _MONTH + r"\.?\s?" + _DAY + r"(?!\d))"
        ),
    }.items()
}


def _phone_ok(text: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    return 10 <= sum(ch.isdigit() for ch in text) <= 15


POST_CHECKS: dict[str, Callable[[str], bool]] = {"phone": _phone_ok}


def find_pattern(name: str, text: str) -> Optional[str]:
    """First match of a predefined pattern in text, or None."""
    check = POST_CHECKS.get(name)
    for found in PATTERNS[name].finditer(text):
        if check is None or check(found.group()):
            return found.group()
    return None


def _match_rule(rule: sqlite3.Row, text: str, message_words: str, pattern_cache: dict) -> Optional[str]:
    """matched_text if the rule matches the message, else None. message_words is " ".join(words(text))."""
    if rule["kind"] == "keyword":
        padded = f" {message_words} "
        for phrase in json.loads(rule["keywords"] or "[]"):
            phrase_words = words(phrase)
            if phrase_words and f" {' '.join(phrase_words)} " in padded:
                return phrase
        return None
    name = rule["pattern"]
    if name not in PATTERNS:
        return None
    if name not in pattern_cache:
        pattern_cache[name] = find_pattern(name, text)
    return pattern_cache[name]


def _applies(rule: sqlite3.Row, message: sqlite3.Row) -> bool:
    return bool(rule["enabled"]) and (rule["chat_id"] is None or rule["chat_id"] == message["chat_id"])


def evaluate(conn: sqlite3.Connection, message_id: int, notify: bool = True) -> None:
    """Recompute which rules match one message; notify for newly matched rules.

    The caller owns the transaction.
    """
    message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if message is None:
        return
    rules = conn.execute(
        "SELECT * FROM rules WHERE enabled = 1 AND (chat_id IS NULL OR chat_id = ?)",
        (message["chat_id"],),
    ).fetchall()
    text = message["message_text"]
    message_words = " ".join(words(text))
    pattern_cache: dict = {}
    matching: dict[int, str] = {}
    for rule in rules:
        matched = _match_rule(rule, text, message_words, pattern_cache)
        if matched is not None:
            matching[rule["id"]] = matched

    existing = {
        row["rule_id"]
        for row in conn.execute("SELECT rule_id FROM message_matches WHERE message_id = ?", (message_id,))
    }
    for rule_id in existing - matching.keys():
        conn.execute(
            "DELETE FROM message_matches WHERE message_id = ? AND rule_id = ?", (message_id, rule_id)
        )
    notifying = {rule["id"] for rule in rules if rule["notify"]}
    for rule_id, matched in matching.items():
        inserted = conn.execute(
            "INSERT OR IGNORE INTO message_matches (message_id, rule_id, matched_text) VALUES (?, ?, ?)",
            (message_id, rule_id, matched),
        ).rowcount
        if notify and inserted and rule_id in notifying:
            conn.execute(
                "INSERT OR IGNORE INTO notifications (rule_id, message_id, matched_text) VALUES (?, ?, ?)",
                (rule_id, message_id, matched),
            )


def backfill(conn: sqlite3.Connection, rule_id: int) -> int:
    """Re-evaluate one rule against every message, without notifications.

    Returns how many messages match the rule afterwards. A disabled rule matches nothing,
    so backfilling it clears its tags.
    """
    rule = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if rule is None:
        return 0
    last_id = 0
    while True:
        batch = conn.execute(
            "SELECT id, chat_id, message_text FROM messages WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, BACKFILL_BATCH),
        ).fetchall()
        if not batch:
            break
        for message in batch:
            matched = None
            if _applies(rule, message):
                text = message["message_text"]
                matched = _match_rule(rule, text, " ".join(words(text)), {})
            if matched is None:
                conn.execute(
                    "DELETE FROM message_matches WHERE message_id = ? AND rule_id = ?",
                    (message["id"], rule_id),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO message_matches (message_id, rule_id, matched_text) "
                    "VALUES (?, ?, ?)",
                    (message["id"], rule_id, matched),
                )
        conn.commit()
        last_id = batch[-1]["id"]
    return conn.execute(
        "SELECT COUNT(*) FROM message_matches WHERE rule_id = ?", (rule_id,)
    ).fetchone()[0]


def categories_for(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[str]]:
    """Sorted distinct non-null categories of the rules matching each message (one query)."""
    result: dict[int, list[str]] = {message_id: [] for message_id in message_ids}
    if not message_ids:
        return result
    placeholders = ",".join("?" * len(message_ids))
    for item in conn.execute(
        f"SELECT DISTINCT m.message_id, r.category FROM message_matches m "
        f"JOIN rules r ON r.id = m.rule_id "
        f"WHERE r.category IS NOT NULL AND m.message_id IN ({placeholders}) ORDER BY r.category",
        message_ids,
    ):
        result[item["message_id"]].append(item["category"])
    return result
