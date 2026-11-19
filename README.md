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
| GET | `/messages?limit=50` | List the newest messages (limit 1–500) |

Run the tests:

```bash
cd backend
python -m pytest -q
```

## OCR screenshots

`POST /ocr/screenshots` takes a Messenger screenshot (multipart `file`, up to 10 MB, plus an optional `chat_id`), runs OCR, groups the text lines into chat bubbles and stores each bubble as a message with `source` = `ocr` and the OCR confidence. Re-uploading the same screenshot does not add rows; those messages come back with `duplicate: true`. The response lists the stored messages (with `side`, `left` or `right`), `skipped_lines` (centred text such as timestamps and system notices) and `rejected` (bubbles that failed validation).

```bash
curl -F file=@shot.png -F chat_id=my-group http://127.0.0.1:8000/ocr/screenshots
```

Crop screenshots to the conversation area first: the header and the input bar would be read as messages.

The OCR engine (PaddleOCR) is an optional heavy install, about 1.2 GB, and it downloads its models on first use:

```bash
cd backend
pip install -r requirements-ocr.txt
```

Without it the endpoint returns 503 ("OCR engine not installed; see README"). Models download from Hugging Face by default; if that is blocked, set `PADDLE_PDX_MODEL_SOURCE=bos` to use Paddle's own host (`paddle-model-ecology.bj.bcebos.com`). On CPU a screenshot takes about 2–4 seconds.

Lines are grouped by the bubble they sit in: the bubble's background colour is traced with OpenCV, so a message with blank lines stays one message, and a blank line inside a bubble comes back as `\n\n`. This works in light and dark mode. Text without a bubble falls back to line spacing. Sender names and timestamps are not extracted yet.

The real-engine test (`tests/test_ocr_paddle.py`) runs only when the OCR install is present.

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
- Not yet included: sender names and timestamps (clipboard and OCR), authentication, Docker, rules and notifications.
