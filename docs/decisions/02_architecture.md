---
title: Architecture Overview
status: approved
category: decisions
last-reviewed: 2026-05-27
---

# Architecture Overview

## Product Surface

- Bookkeeping dashboard: `/`
- Review dashboard: `/review`
- Cleanup center: `/cleanup`

The import UI is no longer mounted. Historical import metadata remains in the
database so cleanup workflows can continue to target imported batches safely.

## Runtime Model

- Backend: FastAPI
- Templates: Jinja2
- Partial interactivity: HTMX on ledger flows
- Charts: Chart.js on review page
- Storage: SQLite at project-root `.data/ledger.sqlite`

## Domain Shape

### Ledger

- Single-ledger product behavior
- Manual transaction create, edit, delete
- Date-range filtering
- Summary cards and category totals
- CSV export for current date range

### Review

- Week / month / year windows
- Weekly buckets anchored on Sunday
- Multi-indicator line chart:
  - `income_total`
  - `expense_total`
  - per-category project datasets
- Expense category pie chart built from the filtered dataset
- Project filtering based on `transactions.category`

### Cleanup

- Bulk-delete preview and execute flows
- `DELETE` / `DELETE ALL` confirmation guard
- Matched-count recheck before execution
- Delete by retained `import_batch_id` batches
- Delete by filters: start, end, direction, category, note, imported-only

## Persistence And Compatibility

- SQLite schema keeps legacy multi-account compatibility data.
- Current product behavior ignores account scoping in active user flows.
- Legacy `account_id` values remain readable in reports and listings.
- `transactions.import_batch_id` is retained because cleanup depends on it.

## Key Modules

- `app/main.py`: app assembly and router mounting
- `app/db.py`: schema creation and legacy repair/rebuild logic
- `app/repo.py`: active transaction, summary, cleanup, and import-batch queries
- `app/router_support/`: shared request parsing, navigation, settings access, and
  cleanup token helpers
- `app/routers/ledger.py`: ledger page and transaction CRUD
- `app/routers/review.py`: review aggregation and review page
- `app/routers/cleanup.py`: cleanup page
- `app/routers/bulk_delete.py`: bulk-delete preview and execute endpoints

## Explicit Non-Goals

- No authentication or multi-user support
- No cloud sync
- No mounted import preview/import workflow
- No active account-management UI

## Operational Notes

- Static assets and the default data directory are anchored to project paths, not
  the current working directory.
- Bulk-delete preview tokens are still process-local in-memory state. This is
  acceptable for the local single-user app, but it is not restart-safe or
  multi-worker safe.
