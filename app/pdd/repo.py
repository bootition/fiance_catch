"""PDD v8 仓储函数。所有写函数在调用方事务内执行。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def create_sync_run(
    conn: sqlite3.Connection,
    *,
    source: str = '',
    script_sha256: str = '',
    security_ok: bool = False,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO pdd_sync_runs(source, script_sha256, started_at, security_ok, status)
        VALUES (?, ?, ?, ?, 'running')
        """,
        (source, script_sha256, now(), int(security_ok)),
    )
    return int(cur.lastrowid)


def finish_sync_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    raw_count: int,
    order_count: int,
    status: str = 'ok',
    report_path: str = '',
) -> None:
    conn.execute(
        """
        UPDATE pdd_sync_runs
        SET finished_at = ?, raw_count = ?, order_count = ?, status = ?, report_path = ?
        WHERE id = ?
        """,
        (now(), raw_count, order_count, status, report_path, run_id),
    )


def upsert_order(conn: sqlite3.Connection, row: dict, *, fetched_run_id: int | None = None) -> bool:
    """返回 True 表示新增，False 表示更新已有订单。"""
    existing = conn.execute(
        "SELECT id FROM pdd_orders WHERE order_sn = ?", (row['order_sn'],)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO pdd_orders(
              order_sn, parent_order_sn, order_type, order_time, pay_time,
              display_amount_cents, order_amount_cents, discount_amount_cents,
              status_text, mall_name, goods_json, raw_path, fetched_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row['order_sn'], row['parent_order_sn'], row['order_type'],
                row['order_time'], row['pay_time'], row['display_amount_cents'],
                row['order_amount_cents'], row['discount_amount_cents'],
                row['status_text'], row['mall_name'],
                json.dumps(row['goods'], ensure_ascii=False),
                row.get('raw_path', ''), fetched_run_id,
            ),
        )
        return True
    conn.execute(
        """
        UPDATE pdd_orders SET
          parent_order_sn = ?, order_type = ?, order_time = ?, pay_time = ?,
          display_amount_cents = ?, order_amount_cents = ?, discount_amount_cents = ?,
          status_text = ?, mall_name = ?, goods_json = ?, raw_path = ?,
          fetched_run_id = COALESCE(?, fetched_run_id), updated_at = ?
        WHERE order_sn = ?
        """,
        (
            row['parent_order_sn'], row['order_type'], row['order_time'], row['pay_time'],
            row['display_amount_cents'], row['order_amount_cents'], row['discount_amount_cents'],
            row['status_text'], row['mall_name'], json.dumps(row['goods'], ensure_ascii=False),
            row.get('raw_path', ''), fetched_run_id, now(), row['order_sn'],
        ),
    )
    return False


def upsert_enrichment(
    conn: sqlite3.Connection,
    *,
    source_transaction_id: int,
    product_desc: str,
    method: str,
    confidence: str,
    status: str = 'active',
) -> None:
    conn.execute(
        """
        INSERT INTO pdd_order_enrichments(
          source_transaction_id, product_desc, method, confidence, status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_transaction_id) DO UPDATE SET
          product_desc = excluded.product_desc,
          method = excluded.method,
          confidence = excluded.confidence,
          status = excluded.status,
          updated_at = excluded.updated_at
        """,
        (source_transaction_id, product_desc, method, confidence, status, now()),
    )


def add_enrichment_item(
    conn: sqlite3.Connection,
    *,
    source_transaction_id: int,
    order_sn: str,
    amount_cents: int,
) -> None:
    conn.execute(
        """
        INSERT INTO pdd_order_enrichment_items(enrichment_id, order_sn, amount_cents)
        VALUES (?, ?, ?)
        """,
        (source_transaction_id, order_sn, amount_cents),
    )


def clear_enrichment_items(conn: sqlite3.Connection, source_transaction_id: int) -> None:
    conn.execute(
        "DELETE FROM pdd_order_enrichment_items WHERE enrichment_id = ?",
        (source_transaction_id,),
    )


def get_enrichment_text(conn: sqlite3.Connection, source_transaction_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT product_desc FROM pdd_order_enrichments
        WHERE source_transaction_id = ? AND status = 'active'
        """,
        (source_transaction_id,),
    ).fetchone()
    return None if row is None else str(row['product_desc'])


def upsert_refund_order_link(
    conn: sqlite3.Connection,
    *,
    refund_source_transaction_id: int,
    order_sn: str,
    match_method: str,
    confidence: str,
    amount_cents: int,
    time_diff_seconds: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO pdd_refund_order_links(
          refund_source_transaction_id, order_sn, match_method, confidence,
          amount_cents, time_diff_seconds, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(refund_source_transaction_id) DO UPDATE SET
          order_sn = excluded.order_sn,
          match_method = excluded.match_method,
          confidence = excluded.confidence,
          amount_cents = excluded.amount_cents,
          time_diff_seconds = excluded.time_diff_seconds
        """,
        (refund_source_transaction_id, order_sn, match_method, confidence,
         amount_cents, time_diff_seconds, now()),
    )


def list_pdd_orders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM pdd_orders ORDER BY order_time DESC"
    ).fetchall())


def latest_sync_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM pdd_sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
