# Local Ledger Web App Replay Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reproduce the current as-built local ledger app through deterministic chat rounds for vibe-coding competition replay.

**Architecture:** FastAPI serves Jinja templates, HTMX updates ledger fragments, Chart.js renders review charts, SQLite stores ledger/import data, and repository helpers isolate SQL.

**Tech Stack:** Python, FastAPI, Jinja2, HTMX, Chart.js, SQLite, pytest.

---

## Replay Rules (Use in Competition)

1. Execute tasks in order. Do not skip verification checkpoints.
2. After each task, ask the coding agent to show changed files and run listed tests.
3. Keep single-ledger behavior (no account switching in UI).
4. Preserve compatibility tables/columns for legacy multi-account databases.
5. Before final submission, run full test suite.

---

### Task 1: Project bootstrap and runtime wiring

**Files**
- Create/verify: `requirements.txt`
- Create/verify: `app/settings.py`
- Create/verify: `app/main.py`
- Create/verify: `README.md`

**Replay prompt (send to coding agent)**

```text
Set up a FastAPI local ledger app skeleton. Use app/settings.py for .data/ledger.sqlite config, initialize app in app/main.py, and keep run instructions in README.md.
```

**Verification**

- Run: `python -m uvicorn app.main:app --reload`
- Expected: app boots and serves `GET /` without crash.

---

### Task 2: SQLite schema + migration/repair layer

**Files**
- Create/verify: `app/db.py`

**Required outcomes**

- Create tables: `accounts`, `transactions`, `import_sessions`, `import_rows`, `category_rules`.
- Ensure constraints and triggers.
- Add compatibility repair logic for legacy DB variants.
- Ensure indexes:
  - `idx_transactions_account_date`
  - `idx_transactions_import_batch_id`
  - import rows and rules indexes.

**Replay prompt**

```text
Implement robust init_db() in app/db.py that both creates the modern schema and repairs legacy SQLite schemas (missing columns, weak constraints, old direction check, old unique index on source_txn_id). Keep data safe during rebuild.
```

**Verification**

- Run: `python -m pytest tests/test_db_migration.py -q`
- Expected: all migration tests pass.

---

### Task 3: Input validation utilities

**Files**
- Create/verify: `app/logic.py`
- Create/verify: `tests/test_logic.py`

**Required outcomes**

- `parse_amount_to_cents()` with decimal-safe parsing.
- `validate_direction()` for manual ledger route (`income|expense`).

**Replay prompt**

```text
Add validation helpers in app/logic.py: parse decimal amount strings into integer cents and validate manual direction values. Include tests for valid and invalid cases.
```

**Verification**

- Run: `python -m pytest tests/test_logic.py -q`
- Expected: all tests pass.

---

### Task 4: Repository layer for ledger + summaries

**Files**
- Create/verify: `app/repo.py`
- Create/verify: `tests/test_repo.py`
- Create/verify: `tests/test_repo_summary.py`
- Create/verify: `tests/test_repo_accounts.py`

**Required outcomes**

- Transaction CRUD helpers.
- Date-range listing, category list, summary aggregation.
- Single-ledger behavior (account scoping intentionally ignored in product mode).

**Replay prompt**

```text
Implement repository functions for transactions and summaries. Keep SQL in app/repo.py. Preserve single-ledger behavior by writing new rows to account_id=1 and not filtering by account_id in listing/summary/category helpers.
```

**Verification**

- Run: `python -m pytest tests/test_repo.py tests/test_repo_summary.py tests/test_repo_accounts.py -q`
- Expected: all tests pass.

---

### Task 5: Ledger page (manual add/delete/filter/export)

**Files**
- Update/verify: `app/main.py`
- Update/verify: `templates/index.html`
- Update/verify: `templates/_summary.html`
- Update/verify: `templates/_transactions_table.html`

**Required outcomes**

- Routes:
  - `GET /`
  - `POST /transactions`
  - `POST /transactions/{txn_id}/delete`
  - `GET /export.csv`
- HTMX partial refresh path for create/delete.
- Note normalization to `无` when blank.
- Date-range query support and CSV export with UTF-8 BOM.

**Replay prompt**

```text
Build the ledger UI with Jinja + HTMX: add transaction form, date-range filter, summary card, transaction table, delete action, and CSV export endpoint. Support both full-page and HX partial update responses.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "create_transaction or delete_transaction or export_csv or summary_is_correct or note_"`
- Expected: selected ledger tests pass.

---

### Task 6: Review dashboard page

**Files**
- Update/verify: `app/main.py`
- Create/verify: `templates/review.html`

**Required outcomes**

- Route: `GET /review`.
- Week/month/year tabs.
- Current-window line chart data and expense pie chart data.
- Net consumption metric.

**Replay prompt**

```text
Add /review page with period tabs (week/month/year), current-window trend line chart (income+expense), expense category pie chart, and net consumption metric. Use Chart.js in template and compute data server-side.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "review_page"`
- Expected: review tests pass for both en and zh-CN.

---

### Task 7: Alipay CSV parser and direct import endpoint

**Files**
- Update/verify: `app/main.py`

**Required outcomes**

- Parse flexible Alipay CSV headers and optional preface lines.
- Decode encodings (`utf-8-sig`, `gb18030`, fallback `utf-8`).
- Classify statuses (importable vs skipped_status).
- Optional include/exclude neutral rows.
- Route: `POST /import/alipay`.

**Replay prompt**

```text
Implement Alipay CSV parsing in app/main.py with tolerant header detection, status classification, flexible date parsing, and include_neutral toggle. Add direct import route /import/alipay and return import counters in redirect query params.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "import_alipay"`
- Expected: import parser tests pass.

---

### Task 8: Import preview session workflow

**Files**
- Update/verify: `app/main.py`
- Create/verify: `templates/import.html`
- Create/verify: `templates/import_preview.html`

**Required outcomes**

- Routes:
  - `GET /import`
  - `POST /import/alipay/preview`
  - `GET /import/preview/{session_id}`
  - `POST /import/preview/{session_id}/row/{row_id}`
  - `POST /import/preview/{session_id}/bulk-update`
  - `POST /import/preview/{session_id}/bulk-delete`
  - `POST /import/preview/{session_id}/commit`
  - `POST /import/preview/{session_id}/discard`
- Preview rows are stored in DB before commit.
- Commit writes selected valid rows only.

**Replay prompt**

```text
Add an import preview workflow with DB-backed sessions. Preview should not write transactions immediately. Users can edit rows, select rows, bulk update, then commit/discard the session. Commit inserts only valid selected rows.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "import_preview"`
- Expected: preview lifecycle tests pass.

---

### Task 9: Category rules and repeatable import behavior

**Files**
- Update/verify: `app/repo.py`
- Update/verify: `app/main.py`
- Update/verify: `templates/import_preview.html`

**Required outcomes**

- Category rule persistence and application on future imports.
- Support creating rules from selected rows and optional explicit pattern.
- Keep intentional non-dedup import behavior by `source_txn_id`.

**Replay prompt**

```text
Implement category rules so import preview can map raw categories to target categories and persist these mappings for future imports. Keep duplicate source_txn_id rows importable (no dedup).
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "create_rule or dedup or same_trade_no"`
- Expected: rule and duplicate behavior tests pass.

---

### Task 10: Bulk delete center with safety guardrails

**Files**
- Update/verify: `app/main.py`
- Update/verify: `app/repo.py`
- Update/verify: `templates/import.html`

**Required outcomes**

- Preview route issues temporary delete token.
- Execute route validates:
  - confirm text (`DELETE` or `DELETE ALL`)
  - expected count consistency
  - explicit allow-delete-all when filters are empty
- Support delete by import batch and by conditional filters.

**Replay prompt**

```text
Build a safe bulk delete center: preview first, issue a short-lived token, require explicit confirm text, reject stale expected counts with 409, and block delete-all unless allow_delete_all is explicitly enabled.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "bulk_delete or delete_import_batch"`
- Expected: bulk delete safety and batch delete tests pass.

---

### Task 11: Single-ledger freeze and account-route shutdown

**Files**
- Update/verify: `app/main.py`
- Update/verify: `README.md`

**Required outcomes**

- Keep account management routes present but returning 404 with fixed detail.
- Remove account selectors from UI pages.
- Ensure list/summary/export still include legacy non-default account rows.

**Replay prompt**

```text
Finalize product mode as single-ledger: disable account management endpoints with 404 responses, keep compatibility data readable, and document this behavior in README.
```

**Verification**

- Run: `python -m pytest tests/test_main_routes.py -q -k "single_ledger or account_management_routes_are_disabled or legacy_multi_account"`
- Expected: single-ledger compatibility tests pass.

---

### Task 12: End-to-end verification for submission

**Files**
- Verify all above files only; avoid unrelated edits.

**Replay prompt**

```text
Run full verification and report concise evidence: test summary, key route checks, and any warnings. Do not change behavior unless tests fail.
```

**Verification**

- Run: `python -m pytest -q`
- Expected: full suite pass (`74 passed` baseline).

---

## Final Replay Checklist (Competition Upload)

- Design doc is as-built, not aspirational.
- Plan tasks map one-to-one to existing modules/routes.
- Route list and constraints match current behavior.
- Verification command and expected output are included.
