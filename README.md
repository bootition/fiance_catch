# Local Ledger Web App

Desktop-first local ledger app built with FastAPI, Jinja2, HTMX, and SQLite.

## Run locally

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Start the server:

```bash
python -m uvicorn app.main:app --reload
```

3. Open `http://127.0.0.1:8000` in your browser.

## Data location and persistence

- SQLite file path: `.data/ledger.sqlite` (created relative to your current working directory).
- Data is persisted in that file and remains available after server restart.

## Backup and export

- CSV export (for current filter range):
  - Open `/export.csv?start=YYYY-MM-DD&end=YYYY-MM-DD`
  - Example: `/export.csv?start=2026-02-01&end=2026-02-29`
- Full backup:
  - Stop the server.
  - Copy `.data/ledger.sqlite` to your backup location.

## MVP limitations

- Single local ledger file, no user accounts.
- No authentication/authorization.
- No budgets, recurring rules, or reconciliation workflow.

## Upgrade path (multi-account)

- Add an `accounts` table.
- Add `account_id` foreign key to `transactions`.
- Scope list/create/delete/export/summary queries by `account_id`.
- Add account switcher in UI and account-aware routing.
