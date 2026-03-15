import sqlite3
from uuid import uuid4

from .db import connect


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
    account_id: int = 1,
    date_str,
    direction,
    amount_cents,
    category,
    note,
    source_txn_id: str | None = None,
    import_batch_id: str | None = None,
) -> int:
    _ = account_id
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


def create_import_txn(
    db_path,
    *,
    date_str: str,
    direction: str,
    amount_cents: int,
    category: str,
    note: str,
    source_txn_id: str | None,
    import_batch_id: str,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
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
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_str,
                direction,
                amount_cents,
                category,
                note,
                source_txn_id,
                import_batch_id,
            ),
        )


def create_import_session(
    db_path,
    *,
    source_name: str,
    lang: str,
    include_neutral: bool,
) -> str:
    session_id = uuid4().hex
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO import_sessions(
              id,
              source_name,
              lang,
              status,
              include_neutral
            )
            VALUES (?, ?, ?, 'active', ?)
            """,
            (
                session_id,
                source_name.strip() or "alipay.csv",
                lang,
                1 if include_neutral else 0,
            ),
        )
    return session_id


def get_import_session(db_path, session_id: str):
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT
              id,
              created_at,
              source_name,
              lang,
              status,
              include_neutral
            FROM import_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()


def insert_import_rows(db_path, session_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """
            INSERT INTO import_rows(
              session_id,
              row_no,
              date,
              direction,
              amount_cents,
              category,
              raw_category,
              note,
              status_text,
              parse_status,
              parse_error,
              source_txn_id,
              tag,
              selected,
              deleted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session_id,
                    int(row["row_no"]),
                    row.get("date"),
                    row.get("direction"),
                    row.get("amount_cents"),
                    row.get("category"),
                    row.get("raw_category"),
                    row.get("note"),
                    row.get("status_text") or "",
                    row["parse_status"],
                    row.get("parse_error") or "",
                    row.get("source_txn_id"),
                    row.get("tag") or "",
                    int(row.get("selected", 0)),
                    int(row.get("deleted", 0)),
                )
                for row in rows
            ],
        )
    return len(rows)


def list_import_rows(db_path, session_id: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              id,
              session_id,
              row_no,
              date,
              direction,
              amount_cents,
              category,
              raw_category,
              note,
              status_text,
              parse_status,
              parse_error,
              source_txn_id,
              tag,
              selected,
              deleted
            FROM import_rows
            WHERE session_id = ?
            ORDER BY row_no ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_import_preview_counts(db_path, session_id: str) -> dict[str, int]:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN parse_status = 'valid' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS valid_count,
              COALESCE(SUM(CASE WHEN parse_status = 'skipped_status' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS skipped_status_count,
              COALESCE(SUM(CASE WHEN parse_status = 'invalid' AND deleted = 0 THEN 1 ELSE 0 END), 0) AS invalid_count,
              COALESCE(SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END), 0) AS deleted_count,
              COALESCE(SUM(CASE WHEN selected = 1 AND deleted = 0 THEN 1 ELSE 0 END), 0) AS selected_count
            FROM import_rows
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        return {
            "valid_count": 0,
            "skipped_status_count": 0,
            "invalid_count": 0,
            "deleted_count": 0,
            "selected_count": 0,
        }

    return {
        "valid_count": int(row["valid_count"]),
        "skipped_status_count": int(row["skipped_status_count"]),
        "invalid_count": int(row["invalid_count"]),
        "deleted_count": int(row["deleted_count"]),
        "selected_count": int(row["selected_count"]),
    }


def update_import_row(
    db_path,
    *,
    session_id: str,
    row_id: int,
    category: str,
    note: str,
    selected: bool,
    deleted: bool,
) -> bool:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active_session = conn.execute(
            """
            SELECT 1
            FROM import_sessions
            WHERE id = ? AND status = 'active'
            """,
            (session_id,),
        ).fetchone()
        if active_session is None:
            return False

        cur = conn.execute(
            """
            UPDATE import_rows
            SET
              category = ?,
              note = ?,
              selected = ?,
              deleted = ?
            WHERE id = ? AND session_id = ?
            """,
            (
                category,
                note,
                1 if selected else 0,
                1 if deleted else 0,
                row_id,
                session_id,
            ),
        )
        return int(cur.rowcount) > 0


def bulk_set_category_for_selected_rows(
    db_path,
    *,
    session_id: str,
    target_category: str,
) -> int:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE import_rows
            SET category = ?
            WHERE
              session_id = ?
              AND selected = 1
              AND deleted = 0
              AND parse_status = 'valid'
            """,
            (target_category, session_id),
        )
        return int(cur.rowcount)


def bulk_set_tag_for_selected_rows(
    db_path,
    *,
    session_id: str,
    tag: str,
) -> int:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE import_rows
            SET tag = ?
            WHERE
              session_id = ?
              AND selected = 1
              AND deleted = 0
            """,
            (tag, session_id),
        )
        return int(cur.rowcount)


def bulk_delete_selected_import_rows(db_path, *, session_id: str) -> int:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE import_rows
            SET deleted = 1, selected = 0
            WHERE
              session_id = ?
              AND selected = 1
              AND deleted = 0
            """,
            (session_id,),
        )
        return int(cur.rowcount)


def list_category_rules(db_path, *, enabled_only: bool = True) -> list[dict]:
    with connect(db_path) as conn:
        if enabled_only:
            rows = conn.execute(
                """
                SELECT id, match_pattern, target_category, enabled, created_at
                FROM category_rules
                WHERE enabled = 1
                ORDER BY id ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, match_pattern, target_category, enabled, created_at
                FROM category_rules
                ORDER BY enabled DESC, id ASC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def create_category_rule(db_path, *, match_pattern: str, target_category: str) -> None:
    normalized_pattern = match_pattern.strip()
    normalized_category = target_category.strip()
    if not normalized_pattern:
        raise ValueError("match_pattern required")
    if not normalized_category:
        raise ValueError("target_category required")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO category_rules(match_pattern, target_category, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(match_pattern, target_category)
            DO UPDATE SET enabled = 1
            """,
            (normalized_pattern, normalized_category),
        )


def create_category_rules_from_selected_rows(
    db_path,
    *,
    session_id: str,
    target_category: str,
) -> int:
    normalized_category = target_category.strip()
    if not normalized_category:
        raise ValueError("target_category required")

    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(raw_category) AS pattern
            FROM import_rows
            WHERE
              session_id = ?
              AND selected = 1
              AND deleted = 0
              AND parse_status = 'valid'
              AND TRIM(raw_category) <> ''
            ORDER BY pattern ASC
            """,
            (session_id,),
        ).fetchall()

        patterns = [str(row["pattern"]) for row in rows]
        if not patterns:
            return 0

        conn.executemany(
            """
            INSERT INTO category_rules(match_pattern, target_category, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(match_pattern, target_category)
            DO UPDATE SET enabled = 1
            """,
            [(pattern, normalized_category) for pattern in patterns],
        )
        return len(patterns)


def commit_import_session(db_path, *, session_id: str) -> dict:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")

        session = conn.execute(
            """
            SELECT id, status
            FROM import_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("import session not found")
        if str(session["status"]) != "active":
            raise ValueError("import session not active")

        summary = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END), 0) AS deleted_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND parse_status = 'valid' AND selected = 1 THEN 1 ELSE 0 END), 0) AS selected_valid_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND NOT (parse_status = 'valid' AND selected = 1) THEN 1 ELSE 0 END), 0) AS skipped_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND parse_status = 'skipped_status' THEN 1 ELSE 0 END), 0) AS skipped_status_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND parse_status = 'invalid' THEN 1 ELSE 0 END), 0) AS invalid_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND parse_status = 'valid' THEN 1 ELSE 0 END), 0) AS valid_count,
              COALESCE(SUM(CASE WHEN deleted = 0 AND selected = 1 THEN 1 ELSE 0 END), 0) AS selected_count
            FROM import_rows
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT date, direction, amount_cents, category, note, source_txn_id
            FROM import_rows
            WHERE
              session_id = ?
              AND parse_status = 'valid'
              AND selected = 1
              AND deleted = 0
            ORDER BY row_no ASC, id ASC
            """,
            (session_id,),
        ).fetchall()

        import_batch_id: str | None = None
        if rows:
            import_batch_id = uuid4().hex
            conn.executemany(
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
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["date"],
                        row["direction"],
                        int(row["amount_cents"]),
                        row["category"],
                        row["note"],
                        row["source_txn_id"],
                        import_batch_id,
                    )
                    for row in rows
                ],
            )

        conn.execute(
            """
            UPDATE import_sessions
            SET status = 'committed'
            WHERE id = ?
            """,
            (session_id,),
        )

    assert summary is not None
    return {
        "imported_count": len(rows),
        "skipped_count": int(summary["skipped_count"]),
        "deleted_count": int(summary["deleted_count"]),
        "valid_count": int(summary["valid_count"]),
        "selected_count": int(summary["selected_count"]),
        "skipped_status_count": int(summary["skipped_status_count"]),
        "invalid_count": int(summary["invalid_count"]),
        "import_batch_id": import_batch_id,
    }


def discard_import_session(db_path, *, session_id: str) -> int:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute(
            """
            SELECT id, status
            FROM import_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("import session not found")
        if str(session["status"]) != "active":
            raise ValueError("import session not active")

        deleted_rows = conn.execute(
            "DELETE FROM import_rows WHERE session_id = ?",
            (session_id,),
        ).rowcount
        conn.execute(
            """
            UPDATE import_sessions
            SET status = 'discarded'
            WHERE id = ?
            """,
            (session_id,),
        )
        return int(deleted_rows)


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


def delete_bulk_by_batch_ids(db_path, batch_ids: list[str]) -> int:
    return delete_bulk_by_filters(db_path, {"batch_ids": batch_ids})


def delete_txns_by_import_batch(db_path, batch_id: str) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM transactions WHERE import_batch_id = ?",
            (batch_id,),
        )
        return int(cur.rowcount)


def list_txns(db_path, *, account_id: int | None = None, start: str, end: str):
    _ = account_id
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


def get_txn(db_path, txn_id: int, *, account_id: int | None = None):
    _ = account_id
    with connect(db_path) as conn:
        return conn.execute(
            """
            SELECT * FROM transactions
            WHERE id = ?
            """,
            (txn_id,),
        ).fetchone()


def list_categories(db_path, *, account_id: int = 1) -> list[str]:
    _ = account_id
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
    account_id: int | None = None,
    date_str: str,
    direction: str,
    amount_cents: int,
    category: str,
    note: str,
) -> bool:
    _ = account_id
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


def delete_txn(db_path, txn_id: int, *, account_id: int | None = None) -> None:
    _ = account_id
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM transactions WHERE id = ?",
            (txn_id,),
        )


def get_summary(
    db_path, *, account_id: int | None = None, start: str, end: str
) -> dict:
    _ = account_id
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
