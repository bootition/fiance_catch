# Engineering History

## Stable Decisions

- The product is desktop-first and local-only.
- The active user-facing surface is `Bookkeeping + Review + Cleanup`.
- Import is retained only as historical metadata support, not as a mounted UI.
- Single-ledger behavior is the product contract; legacy account data remains for
  compatibility rather than active use.

## Major Change Milestones

### Initial Ledger App

- Established FastAPI + Jinja + HTMX + SQLite architecture.
- Implemented manual entry, delete, filtering, summary, and CSV export.

### Main Module Decomposition

- Split the old oversized `app/main.py` into routers and support modules.
- Extracted template setup and translation data into dedicated modules.
- Reduced route and helper coupling by moving shared pieces into
  `app/router_support/`.

### Edit Transaction Flow

- Added HTMX row-fragment editing for transactions.
- Added `get_txn(...)` and `update_txn(...)` to the repository layer.
- Preserved the existing partial refresh model instead of introducing a new page
  flow.

### Review And Cleanup Reshape

- Added dedicated `/cleanup` page.
- Removed mounted import routes from the active product surface.
- Upgraded review to Sunday-anchored weekly buckets and project-aware multi-line
  charting.

### Architecture Simplification And Cleanup

- Anchored static assets and default data path to project paths.
- Rejected blank transaction categories.
- Rejected reversed date ranges in shared parsing and bulk-delete filters.
- Made single-item delete truthful by returning `404` for missing transactions.
- Removed dead mounted-surface residue:
  - `app/router_common.py`
  - `app/routers/accounts.py`
  - `app/routers/importing.py`
  - `templates/import.html`
  - `templates/import_preview.html`
  - `app/services/alipay_parser.py`
- Removed orphaned import preview backend functions once the import UI and route
  surface were gone.

## What Was Intentionally Retained

- `transactions.import_batch_id`
- Cleanup-by-import-batch workflows
- Legacy multi-account database compatibility
- In-memory bulk-delete preview token model for the local app

## Current Follow-Up Candidates

- Remove dead import-specific translation keys from `app/i18n.py`.
- Decide whether to keep or further collapse dormant import schema tables
  (`import_sessions`, `import_rows`, `category_rules`) now that the import UI is
  gone.
- Decide whether to replace process-local bulk-delete preview tokens with a
  persisted or signed token strategy.
