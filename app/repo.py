import sqlite3

from .db import connect


# ── Account CRUD (retained for schema compatibility — no UI routes) ──
# These functions preserve the accounts table contract and are exercised
# by tests/test_repo_accounts.py. They are not called from any active
# route. The accounts router was removed in the single-ledger simplification.


def list_accounts(db_path, *, include_archived: bool = False):
    with connect(db_path) as conn:
        if include_archived:
            cur = conn.execute(
                """
                SELECT id, name, archived
                FROM accounts
                ORDER BY archived ASC, id ASC
                """
            )
        else:
            cur = conn.execute(
                """
                SELECT id, name, archived
                FROM accounts
                WHERE archived = 0
                ORDER BY id ASC
                """
            )
        return cur.fetchall()


def get_account(db_path, account_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT id, name, archived
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()


def create_account(db_path, name: str) -> int:
    account_name = name.strip()
    if not account_name:
        raise ValueError("account name required")
    with connect(db_path) as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO accounts(name)
                VALUES (?)
                """,
                (account_name,),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("account name already exists") from exc
        return int(cur.lastrowid)


def rename_account(db_path, account_id: int, name: str) -> None:
    account_name = name.strip()
    if not account_name:
        raise ValueError("account name required")
    with connect(db_path) as conn:
        account = conn.execute(
            "SELECT id, archived FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError("account not found")
        if int(account["archived"]) == 1:
            raise ValueError("archived account is read-only")
        try:
            cur = conn.execute(
                """
                UPDATE accounts
                SET name = ?
                WHERE id = ?
                """,
                (account_name, account_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("account name already exists") from exc
        if cur.rowcount == 0:
            raise ValueError("account not found")


def delete_account(db_path, account_id: int) -> None:
    if account_id == 1:
        raise ValueError("default account cannot be deleted")
    with connect(db_path) as conn:
        account = conn.execute(
            "SELECT id, archived FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError("account not found")
        if int(account["archived"]) == 1:
            raise ValueError("archived account is read-only")

        txn_count = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE account_id = ?",
            (account_id,),
        ).fetchone()["c"]
        if int(txn_count) > 0:
            raise ValueError("account has transactions")

        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def archive_account(db_path, account_id: int) -> None:
    if account_id == 1:
        raise ValueError("default account cannot be archived")
    with connect(db_path) as conn:
        account = conn.execute(
            "SELECT id, archived FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError("account not found")
        if int(account["archived"]) == 1:
            raise ValueError("account already archived")

        conn.execute(
            "UPDATE accounts SET archived = 1 WHERE id = ?",
            (account_id,),
        )


def restore_account(db_path, account_id: int) -> None:
    with connect(db_path) as conn:
        account = conn.execute(
            "SELECT id, archived FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError("account not found")
        if int(account["archived"]) == 0:
            raise ValueError("account is not archived")

        conn.execute(
            "UPDATE accounts SET archived = 0 WHERE id = ?",
            (account_id,),
        )


def create_txn(
    db_path,
    *,
    date_str,
    direction,
    amount_cents,
    category,
    note,
    source_txn_id: str | None = None,
    import_batch_id: str | None = None,
) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions(
              account_id,
              date,
              direction,
              amount_cents,
              category,
              note,
              source_txn_id,
              import_batch_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                date_str,
                direction,
                amount_cents,
                category,
                note,
                source_txn_id,
                import_batch_id,
            ),
        )
        return int(cur.lastrowid)


def _build_bulk_delete_where(filters: dict) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    start = filters.get("start")
    if start:
        clauses.append("date >= ?")
        params.append(start)

    end = filters.get("end")
    if end:
        clauses.append("date <= ?")
        params.append(end)

    direction = filters.get("direction")
    if direction:
        clauses.append("direction = ?")
        params.append(direction)

    category = filters.get("category")
    if category:
        clauses.append("category = ?")
        params.append(category)

    note_contains = filters.get("note_contains")
    if note_contains:
        clauses.append("note LIKE ?")
        params.append(f"%{note_contains}%")

    imported_only = filters.get("imported_only")
    if imported_only is True:
        clauses.append("import_batch_id IS NOT NULL")
    elif imported_only is False:
        clauses.append("import_batch_id IS NULL")

    batch_ids = [
        batch_id.strip()
        for batch_id in (filters.get("batch_ids") or [])
        if isinstance(batch_id, str) and batch_id.strip()
    ]
    if batch_ids:
        placeholders = ",".join(["?"] * len(batch_ids))
        clauses.append(f"import_batch_id IN ({placeholders})")
        params.extend(batch_ids)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)
    return where_sql, params


def list_import_batches(db_path, limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(int(limit), 1000))
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT
              import_batch_id AS batch_id,
              COUNT(*) AS row_count,
              MIN(date) AS min_date,
              MAX(date) AS max_date,
              MIN(created_at) AS min_created_at,
              MAX(created_at) AS max_created_at
            FROM transactions
            WHERE import_batch_id IS NOT NULL AND TRIM(import_batch_id) <> ''
            GROUP BY import_batch_id
            ORDER BY MAX(created_at) DESC, import_batch_id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return [
            {
                "batch_id": row["batch_id"],
                "row_count": int(row["row_count"]),
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "min_created_at": row["min_created_at"],
                "max_created_at": row["max_created_at"],
            }
            for row in cur.fetchall()
        ]


def preview_bulk_delete(db_path, filters: dict, sample_limit: int = 20) -> dict:
    where_sql, params = _build_bulk_delete_where(filters)
    safe_sample_limit = max(0, min(int(sample_limit), 200))
    with connect(db_path) as conn:
        matched_count = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM transactions{where_sql}",
                tuple(params),
            ).fetchone()["c"]
        )

        sample_rows: list[dict] = []
        if safe_sample_limit > 0:
            sample_query = (
                "SELECT id, date, direction, amount_cents, category, note, "
                "import_batch_id, source_txn_id "
                f"FROM transactions{where_sql} "
                "ORDER BY date DESC, id DESC LIMIT ?"
            )
            rows = conn.execute(
                sample_query, tuple(params + [safe_sample_limit])
            ).fetchall()
            sample_rows = [dict(row) for row in rows]

    return {
        "matched_count": matched_count,
        "sample_rows": sample_rows,
    }


def delete_bulk_by_filters(db_path, filters: dict) -> int:
    where_sql, params = _build_bulk_delete_where(filters)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            f"DELETE FROM transactions{where_sql}",
            tuple(params),
        )
        return int(cur.rowcount)


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


def get_txn(db_path, txn_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM transactions
            WHERE id = ?
            """,
            (txn_id,),
        ).fetchone()


def list_categories(db_path) -> list[str]:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT category
            FROM transactions
            WHERE TRIM(category) <> ''
            ORDER BY category ASC
            """,
        )
        return [str(row["category"]) for row in cur.fetchall()]


def update_txn(
    db_path,
    txn_id: int,
    *,
    date_str: str,
    direction: str,
    amount_cents: int,
    category: str,
    note: str,
) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE transactions
            SET
              date = ?,
              direction = ?,
              amount_cents = ?,
              category = ?,
              note = ?,
              updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                date_str,
                direction,
                amount_cents,
                category,
                note,
                txn_id,
            ),
        )
        return int(cur.rowcount) > 0


def delete_txn(db_path, txn_id: int) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM transactions WHERE id = ?",
            (txn_id,),
        )
        return int(cur.rowcount) > 0


def get_summary(db_path, *, start: str, end: str) -> dict:
    with connect(db_path) as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN direction = 'income' THEN amount_cents END), 0) AS income_cents,
              COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount_cents END), 0) AS expense_cents
            FROM transactions
            WHERE date >= ? AND date <= ?
            """,
            (start, end),
        ).fetchone()
        by_category_rows = conn.execute(
            """
            SELECT category, SUM(amount_cents) AS amount_cents
            FROM transactions
            WHERE direction = 'expense' AND date >= ? AND date <= ?
            GROUP BY category
            ORDER BY amount_cents DESC, category ASC
            """,
            (start, end),
        ).fetchall()

    return {
        "income_cents": int(totals["income_cents"]),
        "expense_cents": int(totals["expense_cents"]),
        "by_category": [
            {"category": row["category"], "amount_cents": int(row["amount_cents"])}
            for row in by_category_rows
        ],
    }
