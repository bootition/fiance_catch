# Local Ledger Web App

Desktop-first local ledger app built with FastAPI, Jinja2, HTMX, and SQLite.

## Current product surface

- Bookkeeping dashboard: `/`
- Review dashboard: `/review`
- Cleanup center: `/cleanup`

Import is no longer mounted as a user-facing mode. Historical import metadata remains in the database so cleanup workflows can continue to target imported batches safely.

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

- SQLite file path: project-root `.data/ledger.sqlite`.
- Data is persisted in that file and remains available after server restart.

## Backup and export

- CSV export (for current date range in single-ledger mode):
  - Open `/export.csv?start=YYYY-MM-DD&end=YYYY-MM-DD`
  - Example: `/export.csv?start=2026-02-01&end=2026-02-29`
- Full backup:
  - Stop the server.
  - Copy `.data/ledger.sqlite` to your backup location.

## Single-ledger mode

- Product behavior is single-ledger: all transactions are shown together, with date-range filtering.
- Existing multi-account databases are still supported without destructive migration.
- Legacy `account_id` and `accounts` data remain in SQLite for compatibility and historical data retention.
- New transactions are written in single-ledger mode and core flows remain: add/delete/filter/summary/export/review/cleanup.

## Review and cleanup notes

- Review supports week/month/year windows with multi-indicator line charts (`income_total` / `expense_total` plus per-category project breakdowns), pie chart by expense category, and project filtering based on transaction category values.
- Weekly buckets are Sunday-anchored.
- Cleanup keeps bulk-delete safety controls (`DELETE` / `DELETE ALL`, preview token, matched-count recheck) and supports import-batch based deletion via retained `import_batch_id` metadata.

## MVP limitations

- Local single-user app (no login/auth).
- No authentication/authorization.
- No budgets, recurring rules, or reconciliation workflow.
