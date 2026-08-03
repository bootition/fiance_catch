# Local Ledger Web App

Desktop-first local ledger app built with FastAPI, Jinja2, HTMX, and SQLite.

## Current product surface

- Monthly overview: `/`
- Bill import: `/imports/new` (Alipay CSV / WeChat XLSX, original files never stored)
- Pending review queue: `/inbox` — high-risk items resolved one by one (refund linking with candidates, withdrawal purpose, person-transfer classification), classification groups bulk-confirmed
- Ledger entries: `/transactions` — filters, simple manual entry, detail page `/transactions/{id}` with source/batch/refund/audit traceability and editing
- Classification rules: `/rules`
- Import batches and revocation: `/imports` — revoke lists blocked (edited/refund-linked) records explicitly

`/review`, `/cleanup`, and `/export.csv` are legacy routes and are not part of the current product surface.

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

## Backup

- Full backup:
  - Stop the server.
  - Copy `.data/ledger.sqlite` to your backup location.

## MVP limitations

- Local single-user app (no login/auth).
- No authentication/authorization.
- No budgets, recurring rules, or reconciliation workflow.
- Before importing real bills, check `docs/STATUS.md`: the current stage-seven repair is awaiting final red-team re-review before release.
