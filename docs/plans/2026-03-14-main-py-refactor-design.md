# Main Refactoring Design (Router Architecture)

## 1. Context and Goals
- **Current State**: `app/main.py` is >2100 lines long, mixing routes, logic, parsing, translations, and global configuration. 
- **Goal**: Deconstruct `main.py` to drastically improve maintainability, focusing entirely on a clean structure for subsequent features—especially for the primary manual accounting use cases.
- **Constraints**: 100% route contract and UI compatibility. No changes to the database structure or frontend templating paths.

## 2. Selected Approach: Business Domain Routers
Instead of splitting strictly by technology (i.e. one huge `views.py`), we will cut vertically by business domain using FastAPI's `APIRouter`.

## 3. Component Architecture

### 3.1 New Core Assets
- **`app/main.py`** 
  - Responsibility: Absolute minimum. App instantiation, static files, and `app.include_router(...)`.
- **`app/dependencies.py`**
  - Extract global helpers: getting the language from requests (`get_lang`), path parameters formatting.
- **`app/templates_core.py`** (or `app/render.py`)
  - Construct the global `Jinja2Templates` instance and mount the translation dictionary and helper functions (e.g. `format_currency`).
- **`app/i18n.py`**
  - Hold the massive `TRANSLATIONS` dictionary and the string resolver.

### 3.2 Routers (`app/routers/`)
- **`ledger.py`**
  - Manual tracking, rendering `index.html`, HTMX creation, HTMX deletion, CSV exports. (Top focus area).
- **`review.py`**
  - Dashboard routes covering week/month/year calculations and chart formatting.
- **`importing.py`**
  - Alipay CSV upload flows, preview lifecycle (`/import/alipay/*`, `/import/preview/*`).
- **`bulk_delete.py`**
  - Mass operation workflows `/transactions/bulk-delete/*`.
- **`accounts.py`**
  - Legacy scaffolding and 404 shields (`/accounts/*`).

### 3.3 Services (`app/services/`)
- **`alipay_parser.py`**
  - Pure functions stripped from main.py dealing with CSV decoding, classification dicts, and tuple conversions: `_parse_alipay_*` series.

## 4. Execution Rules
1. **No Logic Change**: Do not upgrade validators to Pydantic just yet; maintain exactly the `request.form()` mechanics currently employed.
2. **Move, Don't Rewrite**: Git changes should strictly be code moving.
3. **Continuous Testing**: The test suite (`pytest`) is the arbiter of correctness since we are explicitly maintaining 100% backward compatibility.