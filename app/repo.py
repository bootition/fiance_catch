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
