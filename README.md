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

- CSV export (for selected account and current filter range):
  - Open `/export.csv?account_id=<id>&start=YYYY-MM-DD&end=YYYY-MM-DD`
  - Example: `/export.csv?account_id=1&start=2026-02-01&end=2026-02-29`
- Full backup:
  - Stop the server.
  - Copy `.data/ledger.sqlite` to your backup location.

## Multi-account foundation

- `accounts` table is included with a default account (`id=1`, name `Default`).
- Transactions are scoped by `account_id` for list/create/delete/summary/export.
- UI supports account switching and account creation.

## MVP limitations

- Local single-user app (no login/auth, account is a bookkeeping scope only).
- Account management supports create + switch only (no rename/delete yet).
- No authentication/authorization.
- No budgets, recurring rules, or reconciliation workflow.

## Next upgrade path

- Add account rename/delete with safety checks.
- Add transfer transactions between accounts.
- Add account-level opening balance and archived status.
