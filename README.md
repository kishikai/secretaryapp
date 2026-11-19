# Secretary Messenger Monitor

Skeleton for a monitoring system that stores messages from authorised Messenger group chats. Messages arrive by clipboard or by OCR of screenshots. This milestone provides the backend, SQLite storage, a React admin view, tests and this guide.

Only process content you are authorised to see. The project does not automate Messenger through unsupported interfaces.

## Layout

```
backend/    FastAPI app, SQLite storage, pytest tests
collector/  Clipboard collector script (runs on your own desktop)
frontend/   React + Vite admin view (proxies /api to the backend)
```

## Backend

Tested with Python 3.13.

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The database is created at `backend/data/secretary.db`. Override it with `SECRETARY_DB_PATH`.

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check, including a database query |
| POST | `/messages` | Store one message (`message_text`, `source` = `clipboard` or `ocr`, optional `chat_id`, `sender`, `timestamp`, `confidence`) |
| POST | `/ocr/screenshots` | Upload a Messenger screenshot (multipart `file`, optional `chat_id`); stores each detected message with `source` = `ocr` |
| GET | `/messages` | List messages, newest first, each with its `sources` and `categories`; optional search and filters (see "Search and filters") |
| GET | `/messages/{id}/variants` | Every extraction recorded for one message (`id`, `source`, `message_text`, `confidence`, `created_at`); 404 if the message does not exist |
| POST | `/rules` | Create a rule (201); see "Rules and notifications" |
| GET | `/rules` | List rules |
| GET | `/rules/{id}` | One rule; 404 if missing |
| PATCH | `/rules/{id}` | Change fields of a rule (merged and re-validated); does not recompute existing matches |
| DELETE | `/rules/{id}` | Delete a rule with its matches and notifications (204) |
| POST | `/rules/{id}/backfill` | Apply the rule to every stored message, without notifications; returns `{"rule_id", "matching_messages"}` |
| GET | `/notifications` | Notifications, newest first (`unread`, `limit`, `before_id`) |
| POST | `/notifications/{id}/read` | Mark one notification read (idempotent) |
| POST | `/notifications/read-all` | Mark every unread notification read; returns `{"marked": n}` |

Run the tests:

```bash
cd backend
python -m pytest -q
```

## Search and filters

`GET /messages` always returns a JSON list ordered by `id` descending. All parameters are optional and are combined with AND.

| Parameter | Meaning |
|---|---|
| `q` | Full-text search, 1–200 characters. Words are matched case-insensitively and ignoring accents, and each word matches as a prefix (`meet` finds `meeting`, `cafe` finds `café`). Several words must all be present. Punctuation and search operators are ignored, so `q` is only ever treated as words; a `q` with no letter or digit returns 422. At most the first 20 words are used. |
| `chat_id` | Exact chat id (max 200 characters) |
| `sender` | Exact sender (max 200 characters) |
| `source` | `clipboard` or `ocr` |
| `category` | Only messages matched by a rule with this exact category (max 50 characters) |
| `since` | Only messages stored at or after this time (ISO 8601 datetime, for example `2024-01-31T09:00:00Z`; no offset means UTC) |
| `until` | Only messages stored before this time (same format); `since` must be earlier than `until`, otherwise 422 |
| `before_id` | Only messages with an `id` below this value (at least 1). To page, pass the last `id` of the previous page |
| `limit` | Page size, 1–500, default 50 |

Times compare against `created_at`, the moment the message was first stored (UTC). In a URL, write a `+` in an offset as `%2B`.

```bash
curl "http://127.0.0.1:8000/messages?q=meeting&chat_id=my-group&source=clipboard&limit=20"
curl "http://127.0.0.1:8000/messages?limit=20&before_id=120"   # next page
```

## Rules and notifications

Rules detect keywords and predefined patterns in stored messages, tag them with a category and can create in-app notifications. Every message goes through the rules once it is stored (see `backend/app/rules.py`).

A rule has a `name` (1-100 characters), a `kind`, an optional `category` (1-50 characters), `notify` (default `false`), `enabled` (default `true`) and an optional `chat_id` (the rule only applies in that chat; without it, in every chat).

**Keyword rules** (`kind` = `keyword`) take `keywords`, 1-50 phrases of up to 100 characters, each with at least one letter or digit. A phrase matches whole words only, ignoring case, accents and punctuation: `meet` does not match `meeting`, `cafe` matches `Café`, and `see you` matches `Sige, see you mamaya`. A rule matches when any of its phrases does; the first matching phrase is recorded as `matched_text`.

**Pattern rules** (`kind` = `pattern`) take `pattern`, one of six built-in detectors:

| Pattern | Matches, for example |
|---|---|
| `url` | `https://example.com/a`, `www.example.com` |
| `email` | `juan@example.com` |
| `phone` | `0917 123 4567`, `+63 917 123 4567`, `09171234567` (10-15 digits; dates such as `2026-11-19` are not phones) |
| `money` | `₱1,500`, `PHP 200`, `P50`, `$12.50`, `300 pesos` (not `6pm`) |
| `time` | `6pm`, `6:30pm`, `18:30`, `9:05 AM` |
| `date` | `2026-11-19`, `11/19`, `11/19/2026`, `Nov 19`, `November 19` |

Keywords are required for keyword rules and rejected for pattern rules; `pattern` is the other way round. Patterns are simple heuristics: `1/2` reads as a date and `may 5` matches even in a sentence.

**Categories.** A matching rule with a `category` adds it to the message. `GET /messages` returns each message's sorted `categories` and accepts `?category=` as a filter. `POST /messages` and each OCR item report `categories` too.

**Notifications.** A message that newly matches a rule with `notify` set creates one notification (at most one per rule and message). A duplicate of a message does not create a second one, and a notification never disappears when the message is re-evaluated; it is history. `GET /notifications` lists them newest first with the rule name and message text; `?unread=true` or `false` filters, `limit` is 1-500 (default 50) and `before_id` pages.

**Text corrections.** A message is re-evaluated whenever it is stored, and whenever its text changes: clipboard text replacing an OCR reading, or a higher-confidence OCR reading. An OCR misread such as `chalrs` does not match the keyword `chairs`; when the clipboard copy `chairs` is merged in, the message matches and the notification fires. If the replaced text no longer matches, the match (and category) is removed; its notification stays.

**Backfill.** A new rule only sees messages stored after it was created. `POST /rules/{id}/backfill` applies it to every stored message and returns how many match. Backfill never creates notifications. Only that rule's matches change; a disabled rule matches nothing, so backfilling it clears its tags, and a rule with a `chat_id` only tags messages in that chat.

**Editing and deleting.** `PATCH /rules/{id}` takes any of the fields, merges them into the stored rule and validates the result. It does not recompute existing matches: run backfill afterwards. When you change `kind`, null out the other kind's field: `{"kind": "pattern", "pattern": "url", "keywords": null}`. Disabling a rule (`"enabled": false`) stops new matches and keeps its history. `DELETE /rules/{id}` removes the rule together with its matches and notifications; disable the rule instead if you want to keep them.

```bash
curl -X POST http://127.0.0.1:8000/rules -H 'Content-Type: application/json' \
  -d '{"name": "Chairs", "kind": "keyword", "keywords": ["chairs", "tables"], "category": "setup", "notify": true}'
curl -X POST http://127.0.0.1:8000/rules -H 'Content-Type: application/json' \
  -d '{"name": "Phone numbers", "kind": "pattern", "pattern": "phone", "category": "contact"}'
curl -X POST http://127.0.0.1:8000/rules/1/backfill
curl -X PATCH http://127.0.0.1:8000/rules/1 -H 'Content-Type: application/json' -d '{"enabled": false}'
curl "http://127.0.0.1:8000/messages?category=setup"
curl "http://127.0.0.1:8000/notifications?unread=true&limit=20"
curl -X POST http://127.0.0.1:8000/notifications/3/read
curl -X POST http://127.0.0.1:8000/notifications/read-all
curl -X DELETE http://127.0.0.1:8000/rules/1
```

There are deliberately no custom regular expressions (a pattern that takes exponential time could freeze the server), no outbound webhooks and no email: the API has no authentication yet, so anyone who can reach it could abuse them. They will be added after the Sprint 7 login.

## Backups

`backend/app/backup.py` takes a consistent copy of the database using SQLite's online backup, so it is safe while the server is running. Uncommitted writes are not included. The copy is written to a temporary `.partial` file, checked with `PRAGMA integrity_check`, and only then renamed to `secretary-YYYYMMDDTHHMMSS-ffffffZ.db` (UTC). Afterwards only the newest `--keep` files named `secretary-*.db` in the backup directory are kept; other files are never touched.

```bash
cd backend
python -m app.backup                                   # data/secretary.db -> backups/
python -m app.backup --db data/secretary.db --dir backups --keep 14
```

Defaults: `--db` is `SECRETARY_DB_PATH` or `data/secretary.db`, `--dir` is `backups` (ignored by git), `--keep` is 14. On success it prints the backup path; on failure it prints one line to stderr and exits with code 1 (a missing database is an error; no empty database is created).

Daily at 02:00 with cron (use absolute paths):

```cron
0 2 * * * cd /path/to/secretaryapp/backend && .venv/bin/python -m app.backup --dir /path/to/backups
```

Daily at 02:00 with Windows Task Scheduler:

```bat
schtasks /Create /SC DAILY /ST 02:00 /TN "SecretaryBackup" /TR "cmd /c cd /d C:\secretaryapp\backend && .venv\Scripts\python.exe -m app.backup --dir C:\secretaryapp\backups"
```

There is deliberately no HTTP endpoint for backups: the API has no authentication yet. Backups contain every stored message, so keep the backup directory private and copy it off the machine as well.

Restore:

1. Stop the server.
2. Keep the current database in case you need it: `mv data/secretary.db data/secretary.db.before-restore`.
3. Delete any `secretary.db-journal`, `secretary.db-wal` or `secretary.db-shm` files next to it. A leftover journal from a crash would otherwise be replayed onto the restored copy and corrupt it.
4. Copy the chosen backup into place: `cp backups/secretary-20240131T020000-000000Z.db data/secretary.db`.
5. Start the server again. Older backups are migrated automatically on start-up.

## How messages are merged

Clipboard and OCR both go through one pipeline (`backend/app/pipeline.py`), so the same chat message seen twice is stored once. An incoming message is compared with stored messages of the same `chat_id`, in three tiers. Sender and timestamp must be compatible: equal, or missing on either side.

1. **exact**: same chat, sender, timestamp and text.
2. **normalised**: same text after ignoring case, punctuation, spaces and Unicode width (`Meet at 6pm, ok?` matches `meet at 6pm ok`).
3. **fuzzy**: a close match (similarity of 0.9 or more) among the 500 newest messages in the chat. This absorbs OCR misreads such as `chairs` read as `chalrs`.

Safety rules (merging two different messages would hide one, so the rules lean towards keeping both):

- Clipboard text is exact, so two clipboard messages only merge when identical; `See you there?` and `See you there.` stay separate.
- Fuzzy matches need identical digits in both texts (`6pm` and `7pm` stay separate messages).
- Fuzzy matches need at least half the words in common and at least 12 letters and digits in each text; shorter messages only match exactly or after normalising.

When a message matches, no row is deleted. Clipboard text replaces OCR text (the row becomes `source` = `clipboard`, confidence cleared). Between two OCR readings, the one with the higher confidence wins. Otherwise the stored row is kept. Every extraction, whichever way it ended up, is kept in the `message_variants` table (see `GET /messages/{id}/variants`), and the stored message lists the distinct `sources` that have seen it. `POST /messages` and each item from `POST /ocr/screenshots` report `match` (`exact`, `normalised`, `fuzzy` or `null`); `duplicate` is true whenever `match` is not null.

## OCR screenshots

`POST /ocr/screenshots` takes a Messenger screenshot (multipart `file`, up to 10 MB, plus an optional `chat_id`), runs OCR, groups the text lines into chat bubbles and stores each bubble as a message with `source` = `ocr` and the OCR confidence. Re-uploading the same screenshot does not add rows; those messages come back with `duplicate: true`, and text that was also copied from the clipboard is merged into the same message (see "How messages are merged"). The response lists the stored messages (with `side`, `left` or `right`), `skipped_lines` (centred text such as timestamps and system notices) and `rejected` (bubbles that failed validation).

```bash
curl -F file=@shot.png -F chat_id=my-group http://127.0.0.1:8000/ocr/screenshots
```

Crop screenshots to the conversation area first: the header and the input bar would be read as messages.

The OCR engine (PaddleOCR) is an optional heavy install, about 1.2 GB, and it downloads its models on first use:

```bash
cd backend
pip install -r requirements-ocr.txt
```

Without it the endpoint returns 503 ("OCR engine not installed; see README"). Models download from Hugging Face by default; if that is blocked, set `PADDLE_PDX_MODEL_SOURCE=bos` to use Paddle's own host (`paddle-model-ecology.bj.bcebos.com`). On CPU a screenshot takes about 2–6 seconds.

Lines are grouped by the bubble they sit in: the bubble's background colour is traced with OpenCV, so a message with blank lines stays one message, and a blank line inside a bubble comes back as `\n\n`. The bubble also decides the side, so the short last line of your own (right) message stays with it. Only short centred text on the page background, outside any bubble, is skipped as a timestamp or notice. This works in light and dark mode. Text without a bubble falls back to line spacing. Sender names and timestamps are not extracted yet.

The real-engine test (`tests/test_ocr_paddle.py`) runs only when the OCR install is present.

### Accuracy

`backend/eval/ocr_eval.py` renders 32 synthetic Messenger-style screenshots (light and dark, PNG and JPEG, English and Taglish) with known text, runs the real pipeline and writes [`docs/ocr-eval.md`](docs/ocr-eval.md). Latest run: 32/32 screenshots grouped correctly, character error rate 0.03%, 97% of messages exact, all paragraph breaks and timestamps handled. Synthetic data is an optimistic upper bound; real screenshots are needed before relying on these numbers.

```bash
cd backend
python -m eval.ocr_eval   # about 3 minutes; needs requirements-ocr.txt
```

## Database changes

All changes are applied automatically on start-up; existing databases are migrated in place.

- v0.2 (Sprint 2): `messages.content_hash` column and its unique index `idx_messages_content_hash`.
- v0.3 (Sprint 4): `messages.match_key` column and index `idx_messages_chat_match (chat_id, match_key)`; new `message_variants` table. Existing rows get `match_key` computed, and one variant each from their current text and source.

- v0.4 (Sprint 5): full-text search. `messages_fts` is an FTS5 external-content index over `messages.message_text` (tokenizer `unicode61 remove_diacritics 2`), kept in sync by the triggers `messages_fts_ai`, `messages_fts_ad` and `messages_fts_au`, so text that is replaced when clipboard text upgrades an OCR reading is re-indexed too. If the index did not exist at start-up, it is rebuilt from the existing rows once (`INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')`). New indexes `idx_messages_created (created_at)` and `idx_messages_chat_id (chat_id, id)` serve the list filters.

- v0.5 (Sprint 6): new tables `rules`, `message_matches` (unique per message and rule, index `idx_matches_rule`) and `notifications` (unique per rule and message, index `idx_notifications_unread (read_at, id)`). Created on start-up if missing; existing messages are not tagged until you run a rule's backfill.

## Frontend

Requires Node.js 22 or newer and the backend running on port 8000.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api is proxied to the backend
npm run build    # production build in dist/
```

Set `BACKEND_URL` to point the dev proxy at a different backend.

## Clipboard collector

A small script that runs on your own desktop (never inside the backend). It watches the clipboard and posts each newly copied text to `POST /messages` with `source` = `clipboard` (and your `--chat-id`, if given).

```bash
cd collector
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python clipboard_collector.py --api-url http://127.0.0.1:8000 --chat-id my-group --interval 1.0
```

All options are optional; defaults are `http://127.0.0.1:8000` and 1 second. Press Ctrl+C to stop. Text already on the clipboard when the script starts is not sent. If the backend is unreachable or returns a 5xx error, the same text is retried on the next poll; a 4xx rejection (for example text over 10,000 characters) is logged and not retried.

It only sends text that you copy, and it does not log message contents (only length, id and duplicate flag). **While it runs, everything you copy is sent**, so stop it before copying passwords or other private text. There is no authentication yet, so only point it at a backend you trust.

Run the tests (no clipboard or `pyperclip` needed):

```bash
cd collector
pip install pytest
python -m pytest -q
```

## Status

- Sprint 1 foundation: backend, database connection, starter endpoints, frontend, tests.
- Included: clipboard collector, text normalisation and duplicate detection.
- Sprint 3: OCR of screenshots into stored messages (`POST /ocr/screenshots`, pluggable engine, PaddleOCR optional).
- Sprint 4: one pipeline for clipboard and OCR that merges the same message across sources (exact, normalised and fuzzy matching), keeps every extraction in `message_variants`, and migrates existing databases.
- Sprint 5: full-text search and list filters on `GET /messages` (`q`, `chat_id`, `sender`, `source`, `since`, `until`, `before_id`), and database backups with retention (`python -m app.backup`).
- Sprint 6: automation engine: keyword and predefined-pattern rules, categories, in-app notifications, backfill (`/rules`, `/notifications`, `category` filter on `GET /messages`).
- Not yet included: sender names and timestamps (clipboard and OCR), authentication, Docker, custom regexes, webhooks and email, a frontend for rules and notifications.
