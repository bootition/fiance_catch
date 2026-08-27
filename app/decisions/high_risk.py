"""高风险待办逐笔处理（规格 §2.1/§3.3/§5）。

高风险区（退款/提现/人际转账/其他中性资金流）禁止进入批量确认，
由本服务逐笔定性，单事务完成：创建账本记录、关闭待办、写审计事件、
同步批次待确认数。失败完整回滚。

- 退款：走受约束的退款关联服务（app/refunds/linking.py），不在此处
- 提现到银行卡：必须逐笔选用途（未追踪账户调拨/投资/现金消费/其他）
- 人际转账、红包、收款：人工定性为消费/收入/调拨
- 其他不计收支资金流：同人际，人工定性
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import _add_audit_event, _create_ledger_entry
from .constants import (
    REASON_OTHER_NEUTRAL,
    REASON_PERSON_TRANSFER,
    REASON_WITHDRAWAL,
    TRANSFER_CATEGORIES,
    TYPE_CONSUMPTION,
    TYPE_INCOME,
    TYPE_TRANSFER,
)

# 提现用途（规格 §2.1：未追踪账户调拨、投资、现金消费或其他）
WITHDRAWAL_PURPOSE_TRANSFER = "transfer"  # 未追踪账户调拨
WITHDRAWAL_PURPOSE_INVESTMENT = "investment"  # 投资
WITHDRAWAL_PURPOSE_CASH_EXPENSE = "cash_expense"  # 现金消费
WITHDRAWAL_PURPOSE_OTHER = "other"  # 其他
WITHDRAWAL_PURPOSES = frozenset(
    {
        WITHDRAWAL_PURPOSE_TRANSFER,
        WITHDRAWAL_PURPOSE_INVESTMENT,
        WITHDRAWAL_PURPOSE_CASH_EXPENSE,
        WITHDRAWAL_PURPOSE_OTHER,
    }
)

# 用途 → 账本类型：调拨/投资/其他均不计收支（transfer），现金消费计消费
WITHDRAWAL_PURPOSE_TO_TYPE = {
    WITHDRAWAL_PURPOSE_TRANSFER: TYPE_TRANSFER,
    WITHDRAWAL_PURPOSE_INVESTMENT: TYPE_TRANSFER,
    WITHDRAWAL_PURPOSE_CASH_EXPENSE: TYPE_CONSUMPTION,
    WITHDRAWAL_PURPOSE_OTHER: TYPE_TRANSFER,
}

# 人际转账/其他中性资金流允许的人工定性类型
PERSON_NEUTRAL_TYPES = frozenset(
    {TYPE_CONSUMPTION, TYPE_INCOME, TYPE_TRANSFER}
)

HIGH_RISK_REASONS = frozenset(
    {REASON_WITHDRAWAL, REASON_PERSON_TRANSFER, REASON_OTHER_NEUTRAL}
)


@dataclass(frozen=True)
class ResolveResult:
    review_id: int
    entry_id: int
    reason: str
    entry_type: str


def _sync_batch_pending(conn, batch_id: int | None) -> None:
    if batch_id is None:
        return
    count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_queue
            WHERE status = 'pending'
              AND source_transaction_id IN (
                SELECT id FROM source_transactions WHERE batch_id = ?
              )
            """,
            (batch_id,),
        ).fetchone()["c"]
    )
    conn.execute(
        "UPDATE import_batches SET pending_count = ? WHERE id = ?",
        (count, batch_id),
    )


def resolve_high_risk_review(
    db_path,
    review_id: int,
    *,
    entry_type: str = "",
    category: str = "",
    purpose: str = "",
) -> ResolveResult:
    """逐笔定性一条高风险待办（单事务，失败完整回滚）。

    提现（withdrawal）只接受规格允许的用途，由用途决定账本类型；
    人际转账（person_transfer）/其他中性资金流（other_neutral）
    只接受消费/收入/调拨三种人工定性。
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute(
            """
            SELECT * FROM review_queue
            WHERE id = ? AND status = 'pending'
            """,
            (review_id,),
        ).fetchone()
        if review is None:
            raise ValueError(f"no pending review: {review_id}")
        reason = str(review["reason"])

        if reason == REASON_WITHDRAWAL:
            if purpose not in WITHDRAWAL_PURPOSES:
                raise ValueError(f"invalid withdrawal purpose: {purpose!r}")
            entry_type = WITHDRAWAL_PURPOSE_TO_TYPE[purpose]
        elif reason in (REASON_PERSON_TRANSFER, REASON_OTHER_NEUTRAL):
            if entry_type not in PERSON_NEUTRAL_TYPES:
                raise ValueError(
                    f"invalid entry_type for {reason}: {entry_type!r}"
                )
        else:
            raise ValueError(f"not a high-risk review reason: {reason}")

        category = (category or "").strip()
        if entry_type == TYPE_TRANSFER:
            if category not in TRANSFER_CATEGORIES:
                raise ValueError("调拨必须选择调拨专用分类")
        elif entry_type in (TYPE_CONSUMPTION, TYPE_INCOME) and not category:
            raise ValueError("消费/收入必须选择分类")

        source = conn.execute(
            "SELECT * FROM source_transactions WHERE id = ?",
            (review["source_transaction_id"],),
        ).fetchone()
        entry_id = _create_ledger_entry(
            conn,
            entry_type=entry_type,
            amount_cents=source["amount_cents"],
            category=category,
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        conn.execute(
            """
            UPDATE review_queue
            SET status = 'resolved',
                resolved_ledger_id = ?,
                resolved_at = datetime('now')
            WHERE id = ? AND status = 'pending'
            """,
            (entry_id, review_id),
        )
        _add_audit_event(
            conn,
            event_type="high_risk_resolved",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=(
                f"review:{review_id};reason:{reason};type:{entry_type};"
                f"category:{category};purpose:{purpose}"
            ),
        )
        _sync_batch_pending(conn, source["batch_id"])
        conn.commit()
        return ResolveResult(
            review_id=review_id,
            entry_id=entry_id,
            reason=reason,
            entry_type=entry_type,
        )
