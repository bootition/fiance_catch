# Main.py Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deconstruct `app/main.py` into a highly maintainable Router-based architecture without changing any routes, UI, or business logic.

**Architecture:** Vertical slicing via FastAPI APIRouter, extracting pure functions to services, and global configurations (like Jinja templates and translations) to core modules.

**Tech Stack:** FastAPI, Jinja2, Python

---

### Task 1: Core extraction (i18n and Templates)

**Files:**
- Create: `app/i18n.py`
- Create: `app/templates_core.py`
- Modify: `app/main.py`

**Step 1: Move translations and language helpers**
Extract the `TRANSLATIONS` dictionary and `get_text`, `format_currency` functions from `app/main.py` into `app/i18n.py`.
Create `app/templates_core.py` to instantiate `Jinja2Templates(directory="templates")` and bind `format_currency` and a `get_text_shim` onto its `env.globals`.

**Step 2: Update main.py to use externalized templates**
Import them into `app/main.py`. Replace local `templates` object with `from app.templates_core import templates`. Import `get_text` and `TRANSLATIONS` where needed.

**Step 3: Run existing tests**
Run: `python -m pytest tests -q`
Expected: PASS

**Step 4: Commit**
Run: `git add app/ main.py app/i18n.py app/templates_core.py && git commit -m "refactor: extract i18n and templates from main.py"`

---

### Task 2: Service extraction (Alipay Parser)

**Files:**
- Create: `app/services/alipay_parser.py`
- Modify: `app/main.py`

**Step 1: Move parsing logic**
Extract all `_parse_alipay_*`, `_classify_alipay_*`, `_apply_category_rules`, `_parse_alipay_preview_rows` and `_parse_alipay_rows` functions into `app/services/alipay_parser.py`.

**Step 2: Update main.py imports**
In `app/main.py`, import the necessary functions from `app.services.alipay_parser`.

**Step 3: Run existing tests**
Run: `python -m pytest tests/test_main_routes.py -q -k "import"`
Expected: PASS

**Step 4: Commit**
Run: `git add app/ && git commit -m "refactor: extract alipay parsing logic to service"`

---

### Task 3: Common Dependencies

**Files:**
- Create: `app/dependencies.py`
- Modify: `app/main.py`

**Step 1: Move FastAPI dependencies**
Extract `get_lang(request: Request)` (and any request-based common helper) into `app/dependencies.py`.

**Step 2: Update main.py**
Import `get_lang` from `app.dependencies`.

**Step 3: Run existing tests**
Run: `python -m pytest tests -q`
Expected: PASS

---

### Task 4: Ledger Router

**Files:**
- Create: `app/routers/ledger.py`
- Modify: `app/main.py`

**Step 1: Move Ledger Routes**
Extract `GET /`, `POST /transactions`, `POST /transactions/{txn_id}/delete`, and `GET /export.csv` into `app/routers/ledger.py` using `APIRouter`. Create a router `router = APIRouter(tags=["Ledger"])`. 

**Step 2: Include Router in main**
In `app/main.py`, use `app.include_router(ledger.router)`. Ensure `Depends(get_lang)` and all needed `repo` and `templates` imports are in `ledger.py`.

**Step 3: Run tests**
Run: `python -m pytest tests/test_main_routes.py -q -k "ledger or export"` (or simply run all tests `pytest tests -q`)
Expected: PASS

**Step 4: Commit**
Run: `git add app/ && git commit -m "refactor: extract ledger routes to router"`

---

### Task 5: Review and Accounts Routers

**Files:**
- Create: `app/routers/review.py`
- Create: `app/routers/accounts.py`
- Modify: `app/main.py`

**Step 1: Move Routes**
Extract `GET /review` into `app/routers/review.py`.
Extract all `/accounts/*` (which return 404s mostly) into `app/routers/accounts.py`.

**Step 2: Include Routers**
In `app/main.py`, include both routers.

**Step 3: Run tests**
Run: `python -m pytest tests -q`
Expected: PASS

**Step 4: Commit**
Run: `git add app/ && git commit -m "refactor: extract review and accounts routes"`

---

### Task 6: Importing and Bulk Delete Routers

**Files:**
- Create: `app/routers/importing.py`
- Create: `app/routers/bulk_delete.py`
- Modify: `app/main.py`

**Step 1: Move Routes**
Extract all `/import/*` routes into `app/routers/importing.py`.
Extract all `/transactions/bulk-delete/*` routes into `app/routers/bulk_delete.py`.
*Note:* Be careful with `_bulk_delete_tokens` memory dict—move it to `bulk_delete.py` as well.

**Step 2: Include Routers**
In `app/main.py`, include both routers. Now `app/main.py` should only have FastAPI instantiation, static mounts, and router inclusions!

**Step 3: Final Test Verification**
Run: `python -m pytest tests -q`
Expected: PASS

**Step 4: Commit**
Run: `git add app/ && git commit -m "refactor: extract import and bulk_delete routers; main.py minimized"`
