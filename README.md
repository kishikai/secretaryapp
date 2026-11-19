# Secretary Messenger Monitor

Skeleton for a monitoring system that stores messages from authorised Messenger group chats. Messages arrive by clipboard or OCR (OCR comes in a later sprint). This milestone provides the backend, SQLite storage, a React admin view, tests and this guide.

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
| GET | `/messages?limit=50` | List the newest messages (limit 1–500) |

Run the tests:

```bash
cd backend
python -m pytest -q
```

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
- Not yet included: timestamps from the clipboard, authentication, Docker, OCR extraction, rules and notifications.
