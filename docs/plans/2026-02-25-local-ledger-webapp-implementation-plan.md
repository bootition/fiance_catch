# Local Ledger Web App Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a local desktop-first web app to manually record transactions into SQLite, browse/filter them, see monthly/category summaries, and export CSV.

**Architecture:** FastAPI serves server-rendered Jinja templates; HTMX handles partial updates; SQLite stores transactions.

**Tech Stack:** Python, FastAPI, Jinja2, HTMX, SQLite, pytest.

---

### Task 1: Create minimal project skeleton

**Files:**
- Create: `app/main.py`
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `app/settings.py`
- Create: `templates/index.html`
- Create: `static/app.css`
- Create: `requirements.txt`
- Create: `README.md`

**Step 1: Create requirements**

Put these in `requirements.txt`:

```text
fastapi==0.115.0
uvicorn[standard]==0.30.6
jinja2==3.1.4
python-multipart==0.0.9
pytest==8.3.2
httpx==0.27.2
```

**Step 2: Add settings**

Create `app/settings.py`:

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path


def get_settings() -> Settings:
    data_dir = Path.cwd() / ".data"
    return Settings(data_dir=data_dir, db_path=data_dir / "ledger.sqlite")
```

**Step 3: DB helpers (create tables on startup)**

Create `app/db.py`:

```python
import sqlite3
from contextlib import contextmanager

from .settings import Settings


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              date TEXT NOT NULL,
              direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
              amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
              category TEXT NOT NULL,
              note TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS transactions_updated_at
            AFTER UPDATE ON transactions
            FOR EACH ROW
            BEGIN
              UPDATE transactions SET updated_at = datetime('now') WHERE id = OLD.id;
            END;
            """
        )
```

**Step 4: Minimal FastAPI app + template rendering**

Create `app/main.py`:

```python
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import init_db
from .settings import get_settings


settings = get_settings()
init_db(settings)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "transactions": [],
            "summary": {"income_cents": 0, "expense_cents": 0, "by_category": []},
        },
    )
```

**Step 5: Basic HTML + CSS placeholder**

Create `templates/index.html` (simple form + empty list placeholders).

Create `static/app.css` (minimal readable layout).

**Step 6: Manual run check**

Run:

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Expected: `GET /` renders an HTML page.

---

### Task 2: Add transaction parsing + validation utilities (TDD)

**Files:**
- Create: `app/logic.py`
- Create: `tests/test_logic.py`

**Step 1: Write failing tests**

Create `tests/test_logic.py`:

```python
import pytest

from app.logic import parse_amount_to_cents, validate_direction


@pytest.mark.parametrize(
    "s,expected",
    [
        ("0.01", 1),
        ("1", 100),
        ("1.2", 120),
        ("1.20", 120),
        ("10.05", 1005),
    ],
)
def test_parse_amount_to_cents_ok(s, expected):
    assert parse_amount_to_cents(s) == expected


@pytest.mark.parametrize("s", ["", "-1", "abc", "1.234"])
def test_parse_amount_to_cents_bad(s):
    with pytest.raises(ValueError):
        parse_amount_to_cents(s)


@pytest.mark.parametrize("s", ["income", "expense"])
def test_validate_direction_ok(s):
    assert validate_direction(s) == s


@pytest.mark.parametrize("s", ["in", "out", "", "Income"])
def test_validate_direction_bad(s):
    with pytest.raises(ValueError):
        validate_direction(s)
```

**Step 2: Run tests to confirm failing**

Run: `pytest -q`
Expected: FAIL (module/functions missing).

**Step 3: Implement minimal logic**

Create `app/logic.py`:

```python
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def validate_direction(s: str) -> str:
    if s not in {"income", "expense"}:
        raise ValueError("direction must be income or expense")
    return s


def parse_amount_to_cents(s: str) -> int:
    if not isinstance(s, str) or not s.strip():
        raise ValueError("amount required")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError("amount invalid") from e
    if d < 0:
        raise ValueError("amount must be non-negative")
    cents = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # reject >2 decimals by checking exact cents equivalence
    if (d * 100) != cents:
        raise ValueError("amount supports up to 2 decimals")
    return int(cents)
```

**Step 4: Run tests**

Run: `pytest -q`
Expected: PASS.

---

### Task 3: Implement DB CRUD for transactions (TDD)

**Files:**
- Create: `app/repo.py`
- Create: `tests/test_repo.py`
- Modify: `app/db.py`

**Step 1: Adjust DB connect/init to accept custom db_path for tests**

In `app/db.py`, ensure `connect(db_path)` accepts a `Path` or `str`.

**Step 2: Write failing tests with temporary sqlite file**

Create `tests/test_repo.py`:

```python
from datetime import date

from app.db import init_db
from app.repo import create_txn, list_txns, delete_txn
from app.settings import Settings


def test_create_list_delete(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    init_db(settings)

    tid = create_txn(
        settings.db_path,
        date_str="2026-02-25",
        direction="expense",
        amount_cents=1234,
        category="food",
        note="lunch",
    )
    rows = list_txns(settings.db_path, start="2026-02-01", end="2026-02-28")
    assert len(rows) == 1
    assert rows[0]["id"] == tid
    assert rows[0]["amount_cents"] == 1234

    delete_txn(settings.db_path, tid)
    rows2 = list_txns(settings.db_path, start="2026-02-01", end="2026-02-28")
    assert rows2 == []
```

**Step 3: Run tests to confirm failing**

Run: `pytest -q`
Expected: FAIL.

**Step 4: Implement repository**

Create `app/repo.py`:

```python
from .db import connect


def create_txn(db_path, *, date_str, direction, amount_cents, category, note) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions(date, direction, amount_cents, category, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (date_str, direction, amount_cents, category, note),
        )
        return int(cur.lastrowid)


def list_txns(db_path, *, start: str, end: str):
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM transactions
            WHERE date >= ? AND date <= ?
            ORDER BY date DESC, id DESC
            """,
            (start, end),
        )
        return cur.fetchall()


def delete_txn(db_path, txn_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
```

**Step 5: Run tests**

Run: `pytest -q`
Expected: PASS.

---

### Task 4: Wire up create + list + delete in the web UI

**Files:**
- Modify: `app/main.py`
- Modify: `templates/index.html`
- Create: `templates/_transactions_table.html`
- Create: `templates/_summary.html`

**Step 1: Add form POST /transactions**

- Parse form fields.
- Validate direction/amount.
- Insert into DB.
- Re-render list + summary.

**Step 2: Add date-range filter (query params)**

- Default range: current month.
- Allow `?start=YYYY-MM-DD&end=YYYY-MM-DD`.

**Step 3: Add delete button**

- `POST /transactions/{id}/delete` then return updated partial.

**Step 4: HTMX partial updates**

- On create/delete, return partial HTML for the table + summary.

**Step 5: Manual verification**

- Start server.
- Add 3 transactions.
- Delete 1.
- Verify list updates without full refresh.

---

### Task 5: Add summary queries (monthly totals + by-category)

**Files:**
- Modify: `app/repo.py`
- Add tests: `tests/test_repo_summary.py`

**Step 1: Write failing tests**

- Insert known rows.
- Assert income/expense totals.
- Assert by-category aggregation for expenses.

**Step 2: Implement SQL aggregation**

- Totals: sum by direction.
- By category: sum expense by category order desc.

**Step 3: Wire summary into `/`**

- Render `templates/_summary.html`.

---

### Task 6: CSV export

**Files:**
- Modify: `app/main.py`

**Step 1: Add `GET /export.csv`**

- Use current filter start/end.
- Return `text/csv` with UTF-8 BOM if needed for Excel compatibility.

**Step 2: Manual verification**

- Download CSV.
- Open with Excel.

---

### Task 7: Persistence + restart check

**Files:**
- None (behavior)

**Steps:**

1. Add several transactions.
2. Stop server.
3. Start server.
4. Confirm data still present.
5. Document DB file location in `README.md`.

---

### Task 8: Documentation and polish

**Files:**
- Modify: `README.md`
- Modify: `static/app.css`

**Steps:**

- Add run instructions.
- Add backup/export instructions.
- Explain MVP limitations and upgrade path (multi-account).
