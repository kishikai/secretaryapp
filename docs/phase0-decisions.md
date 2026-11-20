# Phase 0: baseline and domain decisions

Source plan: *Branch Fit and Change Plan*. This file records what exists today, what must keep working, and the decisions Phases 1 to 7 build on. Items marked **Proposed** need sign-off before code is written.

## 1. Baseline (2026-11-20, branch `claude/relaxed-lovelace-2rvf5r`, commit `95f6011`)

| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend && python -m pytest -q` | 252 passed, 1 skipped (PaddleOCR not installed) |
| Collector tests | `cd collector && python -m pytest -q` | 27 passed |
| Frontend build | `cd frontend && npm ci && npm run build` | builds |

### What exists

- **Tables** (`backend/app/db.py`): `messages`, `message_variants`, `messages_fts`, `rules`, `message_matches`, `notifications`, `admins`, `sessions`, `api_tokens`.
- **Migrations:** `init_db` runs at startup. It uses `CREATE ... IF NOT EXISTS` and guarded `ALTER TABLE ADD COLUMN` (checked through `PRAGMA table_info`). There is no version table. `PRAGMA foreign_keys = ON` is set on every connection.
- **Routes:** `/health` (public); `/messages` and `/ocr/screenshots` POST (ingest token or session); `/messages`, `/messages/{id}/variants`, `/stats` and `/chats` (session); `/auth/login`, `/auth/logout` and `/auth/me`; `/rules*` and `/notifications*` (session, router-level).
- **Auth:** `admins` table (scrypt hashes), created by CLI only (`python -m app.admin create-user`). 12-hour session cookie, stored as a hash. CSRF is the header `X-Requested-With: secretary`. Ingest tokens have scope `ingest`. Logins are rate-limited.
- **Frontend:** hash-routed tabs `overview`, `messages`, `notifications`, `rules` (`frontend/src/App.jsx`). The `api()` helper is in `frontend/src/api.js`. Styling is plain CSS. New top-level API paths must be added to the proxy list in `frontend/vite.config.js`.

### Behaviour to preserve

1. Existing admin accounts can still sign in and keep full access.
2. Ingest tokens keep working and stay limited to message intake.
3. Message intake, dedup and merge, search, rules and notifications behave as now.
4. An existing populated database upgrades in place at startup without losing data.
5. Every non-public route rejects anonymous requests (`test_every_non_public_route_requires_credentials`). New routes are added to that audit.

## 2. Technical approach

- **Migrations:** keep the `init_db` pattern. New tables use `CREATE TABLE IF NOT EXISTS`, and new columns use guarded `ALTER`. Add a test that upgrades a database built from the current schema.
- **Users and roles:** add a `role` column to `admins`, defaulting to `secretary` so existing accounts keep full access. Add one dependency, `require_role(...)`, next to `require_admin`. Accounts stay CLI-created (`create-user --role ...`). In-app user management can come later.
- **API:** new routers (`members_api.py`, `events_api.py` and so on), mounted like `rules_router`, at top-level paths (`/members`, `/groups`, `/events`) to match the existing style. Each new path is added to `vite.config.js` and to the route audit test.
- **Frontend:** new hash tabs in `App.jsx`, using the same `api()` helper and CSS.
- **Rollback:** take a backup before upgrading (`python -m app.backup`). Migrations only add things, so the old code still runs against an upgraded database.

## 3. Domain decisions (Proposed)

### D1. Organisation
Single organisation. No organisation table in the MVP.

### D2. Member fields
`id` (stable integer), `full_name` (required), `email`, `phone`, `member_code` (optional external ID, unique when set), `notes`, `active` (default yes), `joined_on`, `left_on`. Members are deactivated, never deleted once they have any attendance.

### D3. Groups
`name` (unique), `description`. A member can be in many groups.

### D4. Spreadsheet import
CSV only in the MVP: Excel users choose "Save as CSV", which avoids adding a dependency. The flow is upload, then preview with per-row validation, then review duplicates, then confirm. Nothing is written until confirmation. A row is a duplicate candidate when `member_code` or email matches, or the normalised phone or normalised full name matches. For each candidate the secretary chooses **skip**, **create anyway** or **update existing**.

### D5. Event lifecycle
`draft`, then `scheduled`, then `completed`. Draft and scheduled events can be `cancelled`. Fields: `name`, `starts_at`, `ends_at` (optional), `location`, `category`, `notes`, `status`. Only a draft event with no attendance can be deleted.

### D6. Expected attendees
Expected attendees are a snapshot. Picking a group adds its current active members as individual rows, which the secretary can then adjust. Later group changes don't alter existing events.

### D7. Attendance statuses
One record per expected member per event: `not_recorded` (default), `present`, `late`, `absent`, `excused`. `late` counts as attended.

### D8. Finalising and corrections
- The register can be saved part-way through.
- Finalising locks it. Finalising needs no `not_recorded` rows left.
- Changing a finalised register means reopening it, which needs a reason.
- Every change after the first save is audited with actor, time, old value, new value and reason (if any).

### D9. Absence rule (used in Phase 4)
- Only finalised registers of events that aren't cancelled count.
- **Absence rate** = absent / (present + late + absent). Excused is shown but isn't counted in the rate.
- A member is only counted for events they were expected at, so the D6 snapshot already handles members joining or leaving.

### D10. Roles

| Action | secretary | assistant | viewer |
|---|---|---|---|
| View members, events, registers, history | ✓ | ✓ | ✓ |
| Edit members, groups, events, expected lists | ✓ | ✓ | |
| Import members | ✓ | | |
| Record attendance (save progress) | ✓ | ✓ | |
| Finalise / reopen register | ✓ | | |
| Delete or deactivate | ✓ | | |
| Messages, rules, notifications, evidence review | ✓ | | |
| Exports | ✓ | ✓ | ✓ |

### D11. Retention
- Members, events, attendance and audit rows are kept indefinitely in the MVP. Members are deactivated, not deleted.
- Message retention is unchanged.
- A purge tool waits until there's an agreed policy.

## 4. Phase 1 acceptance criteria (first slice users can see)

1. A secretary can add, edit, search, deactivate and reactivate members in the browser.
2. A secretary can create groups and assign members to them.
3. A secretary can upload a CSV, see per-row errors and duplicate candidates, pick an action for each, and confirm. Re-uploading the same file creates no duplicates.
4. Assistants can edit members but cannot import or deactivate. Viewers can only read. The backend enforces both, and tests cover it.
5. Existing admins become secretaries and lose nothing. Every baseline test still passes.
