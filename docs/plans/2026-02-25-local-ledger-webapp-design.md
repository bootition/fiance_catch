# Local Ledger Web App (As-Built) - Design

This document describes the current implemented system (not an early MVP draft).
It is written for replay in a vibe-coding competition, where reviewers reproduce
the project by following chat prompts and checkpoints.

## 1) Product Positioning

- Desktop-first local bookkeeping web app.
- Local-only storage with SQLite at `.data/ledger.sqlite`.
- Primary entry mode: manual transaction entry.
- Secondary modes: Alipay CSV import (direct and preview workflow), review dashboard.
- Current product behavior is **single-ledger mode** (no account switching in UI).

## 2) Implemented Scope

### Core ledger scope

- Add transaction: date, direction, amount, category, note.
- Delete transaction.
- Date-range filter.
- Summary cards: income total, expense total, category totals.
- CSV export for selected date range.
- HTMX partial refresh for summary + table after create/delete.

### Review scope

- `/review` page with week/month/year windows.
- Current-window trend chart (income and expense line chart).
- Expense category pie chart (top 8 + other bucket).
- Net consumption metric.

### Import scope

- `/import` upload entry for Alipay CSV.
- Direct import endpoint (`/import/alipay`).
- Preview import workflow (`/import/alipay/preview`) with session storage:
  - per-row edit (category, note, selected/deleted)
  - bulk set category
  - bulk add tag
  - create category rule from selected rows or explicit pattern
  - commit or discard preview session
- Import result counters:
  - imported
  - skipped_status (business-skip)
  - skipped_non_cashflow
  - invalid
  - deleted

### Safety scope

- Bulk delete center with preview token and confirmation phrase.
- Conditions supported: date range, direction, category, note contains,
  imported-only filter, batch IDs.
- Delete-all requires explicit opt-in and `DELETE ALL` confirmation.
- Count-mismatch guard returns HTTP 409.

### Internationalization scope

- Supported locales: `en` and `zh-CN`.
- Invalid `lang` falls back to `en`.

## 3) Architecture

- Backend: FastAPI (`app/main.py`) for routes, parsing, validation, orchestration.
- Persistence: SQLite with migration/repair logic (`app/db.py`).
- Data access: repository functions in `app/repo.py`.
- Templates: Jinja (`templates/*.html`).
- Partial interactivity: HTMX in ledger page.
- Charts: Chart.js in review page.

### Module responsibilities

- `app/main.py`
  - request parsing, locale and range resolution
  - import parsing and status classification
  - route composition and redirects
  - bulk-delete preview token lifecycle
- `app/db.py`
  - schema creation
  - legacy schema normalization/rebuild
  - triggers and indexes
- `app/repo.py`
  - transaction CRUD and summaries
  - import session/read/write/commit/discard
  - category rule CRUD
  - bulk delete preview and execution queries

## 4) Data Model (Current)

### `accounts`

- `id`, `name` (unique), `archived` (0/1), timestamps.
- Exists for compatibility and future expansion.

### `transactions`

- `id`, `account_id`, `date`, `direction`, `amount_cents`, `category`, `note`.
- Optional import metadata: `source_txn_id`, `import_batch_id`.
- Direction check includes `income`, `expense`, `neutral`.

### `import_sessions`

- Session metadata for preview imports:
  - source name, language, status (`active|committed|discarded`), include_neutral.

### `import_rows`

- Parsed preview rows with parse status, selected/deleted flags, editable fields,
  and optional tag.

### `category_rules`

- Mapping rules: `match_pattern -> target_category`, with enable flag.

## 5) Single-Ledger Compatibility Strategy

- System stores `account_id` in DB for backward compatibility.
- Current product behavior intentionally ignores account scoping in listing,
  summaries, category list, and export.
- New manual/import writes use default account (`account_id = 1`) in this mode.
- Legacy rows from non-default accounts remain visible and included in totals.
- Account management HTTP routes exist but are intentionally disabled (return 404).

## 6) Route Surface (Current)

- Ledger pages:
  - `GET /`
  - `POST /transactions`
  - `POST /transactions/{txn_id}/delete`
  - `GET /export.csv`
- Review:
  - `GET /review`
- Import:
  - `GET /import`
  - `POST /import/alipay`
  - `POST /import/alipay/preview`
  - `GET /import/preview/{session_id}`
  - `POST /import/preview/{session_id}/row/{row_id}`
  - `POST /import/preview/{session_id}/bulk-update`
  - `POST /import/preview/{session_id}/bulk-delete`
  - `POST /import/preview/{session_id}/commit`
  - `POST /import/preview/{session_id}/discard`
  - `POST /import/batches/{batch_id}/delete`
- Bulk delete center:
  - `POST /transactions/bulk-delete/preview`
  - `POST /transactions/bulk-delete/execute`

## 7) Validation and Business Rules

- Date values must be strict `YYYY-MM-DD`.
- Manual direction accepts only `income` and `expense`.
- Amount parsing is decimal-safe and stored as integer cents.
- Empty/missing manual note is normalized to `无`.
- Import parser:
  - supports flexible Alipay headers and preface lines
  - classifies parse states into valid / skipped_status / invalid
  - optional exclusion of neutral rows
  - no trade-no dedup (same source txn ID can be imported multiple times)

## 8) Migration and Reliability Notes

- `init_db()` repairs/normalizes legacy schemas:
  - adds missing columns (`account_id`, `source_txn_id`, `import_batch_id`)
  - rebuilds tables to enforce FK/check constraints when needed
  - normalizes invalid legacy account IDs and archived flags
  - upgrades direction check to include `neutral`
  - drops legacy unique index on `source_txn_id`
- Triggers keep `updated_at` fresh.
- Indexes exist for transaction range queries and import workflows.

## 9) Accepted Constraints (Current Product)

- No authentication/authorization.
- No cloud sync.
- No transaction edit route for ledger entries (create + delete only).
- Account management UI/workflow intentionally hidden and server-disabled.

## 10) Replay Acceptance Baseline

When replaying via chat, treat this as done when:

- `python -m pytest -q` passes all tests.
- Key user flows pass manually:
  - add/delete/filter/export
  - review charts render
  - import preview edit/commit/discard works
  - bulk delete preview+confirm flow works

Reference verification snapshot from current codebase:

- `74 passed` in test suite.
