# Local Ledger Web App

Desktop-first local ledger app built with FastAPI, Jinja2, HTMX, and SQLite.

## Current product surface

- Monthly overview: `/`
- Bill import: `/imports/new`
- Pending review queue: `/inbox`
- Ledger entries: `/transactions`
- Classification rules: `/rules`
- Import batches and revocation: `/imports`

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
- Before importing real bills, check `docs/STATUS.md`: the current stage-seven red-team review has release blockers.
