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
| GET | `/messages?limit=50` | List the newest messages (limit 1–500), each with its `sources` |
| GET | `/messages/{id}/variants` | Every extraction recorded for one message (`id`, `source`, `message_text`, `confidence`, `created_at`); 404 if the message does not exist |

Run the tests:

```bash
cd backend
python -m pytest -q
```

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
- Not yet included: sender names and timestamps (clipboard and OCR), authentication, Docker, rules and notifications.
