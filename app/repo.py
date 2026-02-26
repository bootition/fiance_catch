import sqlite3

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
        if (
            conn.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("account not found")
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
            "SELECT id FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError("account not found")

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
) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions(account_id, date, direction, amount_cents, category, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, date_str, direction, amount_cents, category, note),
        )
        return int(cur.lastrowid)


def list_txns(db_path, *, account_id: int = 1, start: str, end: str):
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT * FROM transactions
            WHERE account_id = ? AND date >= ? AND date <= ?
            ORDER BY date DESC, id DESC
            """,
            (account_id, start, end),
        )
        return cur.fetchall()


def delete_txn(db_path, txn_id: int, *, account_id: int = 1) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM transactions WHERE id = ? AND account_id = ?",
            (txn_id, account_id),
        )


def get_summary(db_path, *, account_id: int = 1, start: str, end: str) -> dict:
    with connect(db_path) as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN direction = 'income' THEN amount_cents END), 0) AS income_cents,
              COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount_cents END), 0) AS expense_cents
            FROM transactions
            WHERE account_id = ? AND date >= ? AND date <= ?
            """,
            (account_id, start, end),
        ).fetchone()
        by_category_rows = conn.execute(
            """
            SELECT category, SUM(amount_cents) AS amount_cents
            FROM transactions
            WHERE account_id = ? AND direction = 'expense' AND date >= ? AND date <= ?
            GROUP BY category
            ORDER BY amount_cents DESC, category ASC
            """,
            (account_id, start, end),
        ).fetchall()

    return {
        "income_cents": int(totals["income_cents"]),
        "expense_cents": int(totals["expense_cents"]),
        "by_category": [
            {"category": row["category"], "amount_cents": int(row["amount_cents"])}
            for row in by_category_rows
        ],
    }
