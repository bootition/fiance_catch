"""退款人工关联与跨期回写（规格 §2.1/§2.3/§4/§6）。

关联成功后：
- refund_links 记录（退款来源流水、原消费、退款金额、关联时间）
- 原消费账本记录金额保持原值（可追溯），实际成本 = 原金额 - 已关联退款，
  统计层按原消费周期计算（跨期退款回写原消费所属周期）
- 待确认项关闭、审计事件、受影响批次 pending_count 同步
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import _add_audit_event

REASON_REFUND_PENDING = "refund_pending"


@dataclass(frozen=True)
class RefundLinkResult:
    refund_link_id: int
    original_ledger_id: int
    refund_amount_cents: int
    net_cost_cents: int  # 原消费实际成本（原金额 - 全部已关联退款）
    review_id: int | None


def _batch_pending_count(conn, batch_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM review_queue
        WHERE status = 'pending'
          AND source_transaction_id IN (
            SELECT id FROM source_transactions WHERE batch_id = ?
          )
        """,
        (batch_id,),
    ).fetchone()
    return int(row["c"])


def _sync_batch_pending(conn, batch_id: int | None) -> None:
    if batch_id is None:
        return
    conn.execute(
        "UPDATE import_batches SET pending_count = ? WHERE id = ?",
        (_batch_pending_count(conn, batch_id), batch_id),
    )


def _find_pending_refund_review(conn, refund_source_id: int):
    return conn.execute(
        """
        SELECT * FROM review_queue
        WHERE status = 'pending'
          AND reason = ?
          AND source_transaction_id = ?
        ORDER BY id ASC LIMIT 1
        """,
        (REASON_REFUND_PENDING, refund_source_id),
    ).fetchone()


def link_refund_to_ledger(
    db_path,
    refund_source_id: int,
    original_ledger_id: int,
) -> RefundLinkResult:
    """人工确认退款与原消费的关联（单事务，失败完整回滚）。

    拒绝条件：
    - 退款来源不存在或不是退款流水
    - 原账本不存在或不是消费
    - 退款总额超过原消费金额（部分退款可多次关联，但不能超额）
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        refund = conn.execute(
            "SELECT * FROM source_transactions WHERE id = ?", (refund_source_id,)
        ).fetchone()
        if refund is None:
            raise ValueError(f"refund source not found: {refund_source_id}")

        entry = conn.execute(
            "SELECT * FROM ledger_entries WHERE id = ?", (original_ledger_id,)
        ).fetchone()
        if entry is None:
            raise ValueError(f"original ledger entry not found: {original_ledger_id}")
        if entry["entry_type"] != "consumption":
            raise ValueError("refund must link to a consumption entry")

        already = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(refund_amount_cents), 0) AS c
                FROM refund_links WHERE original_ledger_id = ?
                """,
                (original_ledger_id,),
            ).fetchone()["c"]
        )
        if already + int(refund["amount_cents"]) > int(entry["amount_cents"]):
            raise ValueError(
                "refund total exceeds original consumption amount "
                f"({already} + {refund['amount_cents']} > {entry['amount_cents']})"
            )

        review = _find_pending_refund_review(conn, refund_source_id)
        review_id = int(review["id"]) if review is not None else None
        if review is not None:
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'resolved',
                    resolved_ledger_id = ?,
                    resolved_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (original_ledger_id, review["id"]),
            )

        cur = conn.execute(
            """
            INSERT INTO refund_links(refund_source_id, original_ledger_id, refund_amount_cents)
            VALUES (?, ?, ?)
            """,
            (refund_source_id, original_ledger_id, refund["amount_cents"]),
        )
        _add_audit_event(
            conn,
            event_type="refund_linked",
            ref_ledger_id=original_ledger_id,
            ref_batch_id=refund["batch_id"],
            detail=f"refund_source:{refund_source_id};amount:{refund['amount_cents']}",
        )
        _sync_batch_pending(conn, refund["batch_id"])
        conn.commit()

        new_total = already + int(refund["amount_cents"])
        return RefundLinkResult(
            refund_link_id=int(cur.lastrowid),
            original_ledger_id=original_ledger_id,
            refund_amount_cents=int(refund["amount_cents"]),
            net_cost_cents=int(entry["amount_cents"]) - new_total,
            review_id=review_id,
        )
