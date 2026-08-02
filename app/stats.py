"""概览与流水统计查询（规格 §2.3/§3.1/§3.4）。

消费统计以退款后的实际成本为准：跨期退款按原消费 txn_date 周期回写。
日常环比仅计算日常三餐、出行交通、书籍学习、日常娱乐、日常缴费；
旅游、副业和收入单独展示，不混入日常消费环比。
"""

from datetime import date

from .db import connect
from .decisions.constants import (
    CATEGORY_DAILY_EXPENSES,
    CATEGORY_DAILY_MEALS,
    CATEGORY_ENTERTAINMENT,
    CATEGORY_LEARNING,
    CATEGORY_SIDE_COST,
    CATEGORY_SIDE_INCOME,
    CATEGORY_TRANSPORT,
    CATEGORY_TRAVEL,
    TYPE_CONSUMPTION,
    TYPE_INCOME,
)

DAILY_CATEGORIES = (
    CATEGORY_DAILY_MEALS,
    CATEGORY_TRANSPORT,
    CATEGORY_LEARNING,
    CATEGORY_ENTERTAINMENT,
    CATEGORY_DAILY_EXPENSES,
)


def _month_bounds(year_month: str) -> tuple[str, str]:
    year, month = year_month.split("-")
    start = f"{year}-{month}-01"
    if month == "12":
        end = f"{int(year) + 1}-01-01"
    else:
        end = f"{year}-{int(month) + 1:02d}-01"
    return start, end


def _prev_month(year_month: str) -> str:
    year, month = year_month.split("-")
    month = int(month)
    if month == 1:
        return f"{int(year) - 1}-12"
    return f"{year}-{month - 1:02d}"


def _category_net(conn, start: str, end: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT le.category,
               SUM(le.amount_cents) - COALESCE(SUM(rl.refund_amount_cents), 0) AS net
        FROM ledger_entries AS le
        LEFT JOIN refund_links AS rl ON rl.original_ledger_id = le.id
        WHERE le.entry_type = 'consumption'
          AND le.txn_date >= ? AND le.txn_date < ?
        GROUP BY le.category
        """,
        (start, end),
    ).fetchall()
    return {row["category"]: int(row["net"]) for row in rows}


def _income_net(conn, start: str, end: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT le.category,
               SUM(le.amount_cents) AS total
        FROM ledger_entries AS le
        WHERE le.entry_type = 'income'
          AND le.txn_date >= ? AND le.txn_date < ?
        GROUP BY le.category
        """,
        (start, end),
    ).fetchall()
    return {row["category"]: int(row["total"]) for row in rows}


def overview_stats(db_path, year_month: str | None = None) -> dict:
    """本月概览指标（规格 §2.3/§3.1）。"""
    if year_month is None:
        today = date.today()
        year_month = f"{today.year}-{today.month:02d}"
    start, end = _month_bounds(year_month)
    prev = _prev_month(year_month)
    prev_start, prev_end = _month_bounds(prev)

    with connect(db_path) as conn:
        consumption = _category_net(conn, start, end)
        income = _income_net(conn, start, end)
        prev_consumption = _category_net(conn, prev_start, prev_end)

        total_consumption = sum(consumption.values())
        total_income = sum(income.values())
        side_income = income.get(CATEGORY_SIDE_INCOME, 0)
        side_cost = consumption.get(CATEGORY_SIDE_COST, 0)
        travel = consumption.get(CATEGORY_TRAVEL, 0)

        daily_current = sum(consumption.get(c, 0) for c in DAILY_CATEGORIES)
        daily_prev = sum(prev_consumption.get(c, 0) for c in DAILY_CATEGORIES)
        if daily_prev == 0:
            daily_change_pct = None
        else:
            daily_change_pct = round((daily_current - daily_prev) / daily_prev * 100, 1)

        pending_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM review_queue WHERE status = 'pending'"
            ).fetchone()["c"]
        )
        non_daily = {
            cat: amount
            for cat, amount in consumption.items()
            if cat not in DAILY_CATEGORIES
        }
        ranking = sorted(
            consumption.items(), key=lambda item: -item[1]
        )[:8]

    return {
        "year_month": year_month,
        "total_income_cents": total_income,
        "total_consumption_cents": total_consumption,
        "side_income_cents": side_income,
        "side_cost_cents": side_cost,
        "travel_cents": travel,
        "daily_current_cents": daily_current,
        "daily_prev_cents": daily_prev,
        "daily_change_pct": daily_change_pct,
        "pending_count": pending_count,
        "ranking": ranking,
        "non_daily": non_daily,
    }


def list_entries_filtered(
    db_path,
    *,
    start: str,
    end: str,
    entry_type: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    batch_id: int | None = None,
    manual_only: bool = False,
    limit: int = 500,
) -> list[dict]:
    """流水筛选（规格 §3.4）：日期、类型、分类、平台、批次、人工改动。"""
    clauses = ["le.txn_date >= ?", "le.txn_date < ?"]
    params: list = [start, end]
    if entry_type:
        clauses.append("le.entry_type = ?")
        params.append(entry_type)
    if category:
        clauses.append("le.category = ?")
        params.append(category)
    if platform:
        clauses.append("st.platform = ?")
        params.append(platform)
    if batch_id is not None:
        clauses.append("le.batch_id = ?")
        params.append(batch_id)
    if manual_only:
        clauses.append("le.manual_edited = 1")

    safe_limit = max(1, min(int(limit), 2000))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
              le.*,
              st.platform,
              st.counterparty,
              st.item_desc,
              st.status_text,
              COALESCE(SUM(rl.refund_amount_cents), 0) AS refunded_cents
            FROM ledger_entries AS le
            LEFT JOIN source_transactions AS st ON st.id = le.source_transaction_id
            LEFT JOIN refund_links AS rl ON rl.original_ledger_id = le.id
            WHERE {' AND '.join(clauses)}
            GROUP BY le.id
            ORDER BY le.txn_date DESC, le.id DESC
            LIMIT ?
            """,
            tuple(params + [safe_limit]),
        ).fetchall()
    return [dict(row) for row in rows]


def list_categories_used(db_path) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT category FROM ledger_entries
            WHERE TRIM(category) <> ''
            ORDER BY category ASC
            """
        ).fetchall()
        return [row["category"] for row in rows]


def list_batches(db_path) -> list[dict]:
    """批次列表（规格 §3.6）：来源、时间、计数、状态。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM import_batches
            ORDER BY imported_at DESC, id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
