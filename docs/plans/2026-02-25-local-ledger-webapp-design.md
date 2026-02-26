# Local Ledger Web App (Desktop-First) - Design

**Goal:** Build a desktop-first local web app for personal bookkeeping with manual entry and local SQLite storage.

**Non-goals (MVP):** cloud sync/login, multi-account, automatic bank import, receipts OCR.

## User Flow (MVP)

1. Open app at `http://127.0.0.1:8000`
2. Add a transaction (date, income/expense, amount, category, note)
3. See the list update immediately
4. Filter list by date range
5. View summary: this month's income total, expense total, and category breakdown
6. Export CSV for backup

## App Shape

- Local-only HTTP server (FastAPI) + server-rendered HTML (Jinja).
- HTMX is used for partial page updates (e.g., insert a new row after adding).
- SQLite is the single source of truth.

## Data Model (MVP)

### transactions

- `id` (integer primary key)
- `date` (YYYY-MM-DD)
- `direction` ("income" | "expense")
- `amount_cents` (integer, non-negative)
- `category` (text)
- `note` (text)
- `created_at` (timestamp)
- `updated_at` (timestamp)

Notes:
- Store money as integer cents to avoid floating-point rounding.
- Category is free text for MVP; later we can add a category table.

## Pages / Endpoints (MVP)

- `GET /` main page: add form + filter + list + summary
- `POST /transactions` create transaction
- `POST /transactions/{id}` update transaction (simple form submit)
- `POST /transactions/{id}/delete` delete transaction
- `GET /export.csv?start=...&end=...` export current filtered set

## Validation Rules (MVP)

- date required; parseable as ISO date
- direction required; one of income/expense
- amount required; positive number with up to 2 decimals; saved as cents
- category required; non-empty
- note optional (but user listed it as required field; treat as required in UI, non-empty)

## Acceptance Checks (MVP)

- CRUD: can create/edit/delete a transaction
- Filter: can list by date range
- Summary: can show monthly totals + category breakdown
- Persistence: restart app; existing data remains
- Export: exported CSV opens cleanly in Excel
