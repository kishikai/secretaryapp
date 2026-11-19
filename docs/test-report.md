# Test report

Sprint 8 (testing and optimisation) for the Secretary Messenger monitor. Measured on 19 November 2026 on a 4-core Linux container with Python 3.13 and SQLite 3.53.

## Summary

| Area | Result |
|---|---|
| Backend tests | 253 passed (including the real PaddleOCR test) |
| Collector tests | 27 passed |
| Line coverage | 96% backend, 89% collector |
| OCR accuracy (synthetic) | 32/32 screenshots grouped correctly, 0.03% character errors |
| Slowest API call at 100,000 messages | `GET /stats`, 63 ms median |
| Known vulnerabilities in dependencies | none (`pip-audit`, `npm audit`) |
| Bugs found and fixed across all sprints | 17 (listed below) |

## How to run

```bash
cd backend
python -m pytest -q                      # all backend tests
python -m eval.ocr_eval                  # OCR accuracy, writes docs/ocr-eval.md (needs requirements-ocr.txt)
python -m eval.perf_bench                # latency on a 100,000-message history
cd ../collector && python -m pytest -q   # collector tests
cd ../frontend && npm run build          # dashboard build
```

Set `PADDLE_PDX_MODEL_SOURCE=bos` if Hugging Face is unreachable for the OCR models. The real-OCR test skips when PaddleOCR is not installed.

## Test inventory

Test functions per file; parametrised cases make the 253 total.

| File | Functions | Covers |
|---|---|---|
| `test_api.py` | 9 | Message API, validation, normalisation, duplicates, migration from the first schema |
| `test_pipeline.py` | 15 | Exact, normalised and fuzzy merges, safety rules, clipboard-beats-OCR, variants, concurrent insert |
| `test_ocr.py` | 19 | Bubble grouping (light and dark mode, paragraph breaks, own-message last lines, timestamps), upload endpoint |
| `test_ocr_paddle.py` | 1 | The real PaddleOCR engine end to end through the upload endpoint |
| `test_upload_limits.py` | 6 | Header size checks, decompression bombs refused before decoding, PNG/JPEG only |
| `test_search.py` | 17 | Full-text search, accents, prefixes, hostile search input, filters, pagination, index migration |
| `test_rules.py` | 26 | Keywords, the six patterns and their worst-case speed, categories, notifications, backfill, rule API |
| `test_auth.py` | 50 | Sign-in, sessions, lock-out, CSRF, ingest tokens, route audit, security headers, admin CLI, stats |
| `test_backup.py` | 12 | Consistent backups, pruning, missing database, CLI |
| `test_integrity.py` | 4 | `integrity_check`, `foreign_key_check` and the search-index check after a mixed workload and on a backup |
| `test_e2e.py` | 1 | The whole workflow through HTTP: rules, token ingest, OCR merge, search, stats, notifications, backup, sign-out |
| `collector/test_clipboard_collector.py` | 27 | Polling, retries, rejected tokens, no message text in logs, real HTTP server |

Manual browser test (Chromium, Sprint 7): sign-in with a wrong and a right password, search, rule creation and backfill, a notification arriving and being marked read, phone width without sideways scrolling, sign-out. No page errors and no Content-Security-Policy violations.

## Coverage

```
app/admin.py           92%     app/ocr.py            92%
app/auth.py            99%     app/ocr_paddle.py     92%
app/auth_api.py       100%     app/pipeline.py       98%
app/backup.py          86%     app/rules.py          96%
app/db.py             100%     app/rules_api.py     100%
app/main.py            98%     TOTAL                 96%
collector/clipboard_collector.py                     89%
```

The uncovered lines are mostly error branches for disk failures (backup), interactive password prompts (admin CLI) and the collector's long-running loop.

## OCR accuracy

`docs/ocr-eval.md` has the full results. On 32 synthetic screenshots (light and dark mode, PNG and JPEG, English and Taglish):

- grouping 32/32;
- character error rate 0.03%;
- 97% of messages exact;
- every paragraph break and timestamp handled.

Every message scored at least 0.97 confidence, so these runs give no evidence for a "needs review" threshold. Real screenshots are still needed: synthetic data is an optimistic upper bound.

## Performance

`python -m eval.perf_bench`: 100,000 messages in 20 chats (5,000 per chat) and three backfilled rules. Each figure is the median of 7 signed-in calls.

| Call | Before Sprint 8 | After |
|---|---|---|
| `GET /messages` (newest 50) | 8 ms | 10 ms |
| `GET /messages?q=chairs` | 12 ms | 11 ms |
| `GET /messages?q=sige mamaya` | 13 ms | 12 ms |
| `GET /messages?chat_id=…` | 7 ms | 8 ms |
| `GET /messages?category=money` | 10 ms | 10 ms |
| `GET /messages?before_id=50000` | 8 ms | 8 ms |
| `GET /stats` | 86 ms | 63 ms |
| `GET /chats` | 118 ms | 14 ms |
| `GET /notifications` | 5 ms | 5 ms |
| `POST /messages` into a 5,000-message chat | 11 ms | 10 ms |

Other timings:

- seeding the 100,000 messages took 6 s;
- each rule backfill took about 2 s;
- fuzzy matching of a 2,000-character message against the 500 newest in its chat took 0.13 s, against more than 5 minutes before the Sprint 4 fix.

The per-day count is the largest part of `/stats`. The benchmark is its worst case, because all 100,000 messages are dated today.

## Security testing

- **Dependencies:**
  - `pip-audit` found 8 published advisories in Starlette 0.38.6, the version FastAPI 0.115.0 pulled in, and one in pytest. The stack was upgraded (FastAPI 0.162.0, Starlette 1.10.0, Uvicorn 0.54.0, Pydantic 2.14.2, pytest 9.2.0). The audit is now clean.
  - `npm audit` reports no vulnerabilities.
- **Route audit:** a test lists every route from both the route tree and the OpenAPI schema and checks each answers 401 without credentials. After the upgrade it caught that included routers had become invisible to its old route listing, and it now covers all 15 protected routes.
- **Input map:** done by the Haiku search helper and checked by hand.
  - No request value reaches SQL text; all queries use fixed fragments with bound parameters.
  - Search input is reduced to quoted words.
  - No file paths come from requests, and there are no shell commands, `eval` or unsafe deserialisation.
  - The dashboard renders all message text as escaped text.
- **Upload limits:** a 1.1 MB PNG declaring 20,000 × 20,000 pixels used 2.3 GB of memory before it was rejected. Uploads are now limited to PNG and JPEG, and their declared size is checked from the header before decoding (25 megapixels, 10,000 px per side). OpenCV's own pixel limit is set as a second layer. Peak memory for the same file is now 55 MB.
- **Authentication review** (Sprint 7): scrypt password hashes, timing-safe sign-in, hashed session and API tokens, SameSite=Strict HttpOnly cookies, CSRF header, lock-out, ingest tokens unable to read, API docs disabled, security headers.
- **Database integrity:** foreign keys are now enforced on every connection, and connections are always closed. The integrity tests check corruption, orphan rows and the search index after a mixed workload and on a backup.

## Bugs found and fixed

| # | Sprint | Severity | Bug | Found by | Fix |
|---|---|---|---|---|---|
| 1 | 2 | High | A collector API URL without `http://` marked every copied message as sent and dropped it | Code review | `941eecb` |
| 2 | 3 | High | A dark-mode bubble with blank lines was split into several messages | The real screenshot | `c4249a1` |
| 3 | 3 | Medium | Blank lines inside a bubble were lost, so OCR text never matched the clipboard copy | Real-engine run | `c4249a1` |
| 4 | 3 | High | The short last line of an own (right) message was labelled "left" or dropped as a timestamp; 39 lines lost on 32 screenshots | OCR evaluation | `41e342a` |
| 5 | 3 | Medium | PaddleOCR crashed in its CPU acceleration (oneDNN) | First real run | `c4249a1` (acceleration off) |
| 6 | 4 | Medium | Two requests with the same text at once crashed with a 500 | Code review | `50c6d35` |
| 7 | 4 | Medium | Two clipboard copies differing only in punctuation ("Ok?", "Ok.") were merged, hiding one | Code review | `50c6d35` |
| 8 | 4 | Medium | Fuzzy matching took more than 5 minutes against 500 long messages | Benchmark | `50c6d35` |
| 9 | 5 | High | Restore steps left a stale journal that could corrupt the restored database | Code review | `613ccaa` |
| 10 | 7 | Low | New rules notified by default, so a broad rule would notify on most messages | Browser test | `5456ffd` |
| 11 | 7 | Low | The rule form's checkbox ran into the Create button | Browser test | `5456ffd` |
| 12 | 8 | High | Starlette 0.38.6 had 8 published vulnerabilities | `pip-audit` | Sprint 8 |
| 13 | 8 | High | A small image file could make the server decode gigabytes | Security pass | Sprint 8 |
| 14 | 8 | Medium | After the upgrade the route audit no longer saw included routers | The audit's own sanity check | Sprint 8 |
| 15 | 8 | Low | Database connections were committed but not closed | Coverage warnings | Sprint 8 |
| 16 | 8 | Low | SQLite foreign keys were declared but not enforced | Integrity tests | Sprint 8 |
| 17 | 8 | Low | `/chats` read every message (118 ms at 100,000) | Benchmark | Sprint 8 |

## Known limitations

- **Real-world data:** OCR accuracy and the patterns were tested on synthetic data. The collector was tested with fakes; the real desktop clipboard has not been tried.
- **Senders and timestamps:** neither is extracted yet. Two people sending the same text in one chat count as one message.
- **Login lock-out:**
  - It is kept in memory, so it resets on restart.
  - Behind a reverse proxy every client shares the proxy's IP.
  - A burst of simultaneous attempts can get a few past the limit before the failures are recorded.
- **Uploads before sign-in checks:** a screenshot upload (at most 10 MB) is read before the sign-in check rejects it.
- **Backfill during deletion:** backfilling a rule while it is being deleted can return a 500, with no data harm.
- **"No chat" filter:** it is applied in the browser, so pages can look sparse.
- **Pattern false positives:** for example, "1/2" reads as a date.
- **No Docker image:** this environment has no Docker daemon to build or test one.
