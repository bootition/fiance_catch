"""入账决策引擎：把批次来源流水转为账本记录或待确认项（规格 §2/§5/§7.3）。

判定顺序（规格 §5 导入规则）：
1. 退款 → 退款待办（阶段 4 匹配原消费）
2. 提现到银行卡 → 逐笔选用途（待确认，绝不自动定性）
3. 人际转账/红包/收款 → 待确认
4. 不计收支：明确调拨 → transfer 入账；其他 → 待确认
5. 消费/收入：active 规则 → 自动入账；observing 规则 → 预填待确认；
   未命中 → 待确认
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import (
    _add_audit_event,
    _bump_rule_stats,
    _create_ledger_entry,
    _enqueue_review,
    _update_batch_counts,
)
from .constants import (
    CATEGORY_SIDE_INCOME,
    PERSON_KEYWORDS,
    REASON_OBSERVING_RULE,
    REASON_OTHER_NEUTRAL,
    REASON_PERSON_TRANSFER,
    REASON_REFUND_PENDING,
    REASON_UNMATCHED,
    REASON_WITHDRAWAL,
    SIDE_INCOME_KEYWORDS,
    TRANSFER_KEYWORDS,
    TYPE_INCOME,
    TYPE_TRANSFER,
    WITHDRAWAL_KEYWORDS,
)
from .rules import RULE_STATUS_ACTIVE, RULE_STATUS_OBSERVING, match_rules

ALIPAY_PERSON_TYPES = {"转账红包", "亲友代付", "红包"}
WECHAT_PERSON_TYPES = {"转账", "微信红包"}
ALIPAY_REFUND_STATUS = "退款成功"
WECHAT_REFUND_PREFIXES = ("已全额退款", "已退款")

PRIORITY_REFUND = 5
PRIORITY_WITHDRAWAL = 5
PRIORITY_PERSON = 4
PRIORITY_NEUTRAL = 3
PRIORITY_OBSERVING = 2
PRIORITY_UNMATCHED = 1

ACTION_POSTED = "posted"
ACTION_QUEUED = "queued"


@dataclass(frozen=True)
class ProcessResult:
    total: int
    posted: int  # 自动入账（含调拨）
    queued: int  # 进入待确认
    skipped_existing: int  # 已处理过，跳过


def _is_refund(source) -> bool:
    if source["platform"] == "alipay":
        return source["status_text"] == ALIPAY_REFUND_STATUS
    return source["status_text"].startswith(WECHAT_REFUND_PREFIXES)


def _is_withdrawal(source) -> bool:
    haystack = f"{source['raw_type']} {source['item_desc']} {source['counterparty']}"
    return any(kw in haystack for kw in WITHDRAWAL_KEYWORDS)


def _is_person_transfer(source) -> bool:
    if source["platform"] == "alipay" and source["raw_type"] in ALIPAY_PERSON_TYPES:
        return True
    if source["platform"] == "wechat" and source["raw_type"] in WECHAT_PERSON_TYPES:
        return True
    haystack = f"{source['item_desc']} {source['counterparty']}"
    return any(kw in haystack for kw in PERSON_KEYWORDS)


def _is_transfer(source) -> bool:
    haystack = f"{source['item_desc']} {source['counterparty']} {source['raw_type']}"
    return any(kw in haystack for kw in TRANSFER_KEYWORDS)


def _suggest_side_income(source) -> tuple[str, str]:
    """未命中收入类：闲鱼等关键词预填副业收入建议（仍须确认）。"""
    haystack = f"{source['item_desc']} {source['counterparty']}"
    if source["direction"] == "income" and any(
        kw in haystack for kw in SIDE_INCOME_KEYWORDS
    ):
        return TYPE_INCOME, CATEGORY_SIDE_INCOME
    return "", ""


def process_source(conn, source) -> str:
    """单条来源流水的入账/待确认决策（须在事务内调用）；返回动作。"""
    if _is_refund(source):
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_REFUND_PENDING,
            priority=PRIORITY_REFUND,
        )
        return ACTION_QUEUED
    if _is_withdrawal(source):
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_WITHDRAWAL,
            priority=PRIORITY_WITHDRAWAL,
        )
        return ACTION_QUEUED
    if _is_person_transfer(source):
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_PERSON_TRANSFER,
            priority=PRIORITY_PERSON,
        )
        return ACTION_QUEUED
    if source["direction"] == "neutral":
        if _is_transfer(source):
            _create_ledger_entry(
                conn,
                entry_type=TYPE_TRANSFER,
                amount_cents=source["amount_cents"],
                category="",
                txn_date=source["occurred_at"][:10],
                source_transaction_id=source["id"],
                batch_id=source["batch_id"],
                note="",
            )
            return ACTION_POSTED
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_OTHER_NEUTRAL,
            priority=PRIORITY_NEUTRAL,
        )
        return ACTION_QUEUED

    rule = match_rules(conn, source["counterparty"], source["item_desc"])
    if rule is not None and rule["status"] == RULE_STATUS_ACTIVE:
        _create_ledger_entry(
            conn,
            entry_type=rule["target_type"],
            amount_cents=source["amount_cents"],
            category=rule["target_category"],
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        _bump_rule_stats(conn, rule["id"], confirmed=False)
        _add_audit_event(
            conn,
            event_type="rule_applied",
            ref_rule_id=rule["id"],
            ref_batch_id=source["batch_id"],
            detail=f"source:{source['id']}",
        )
        return ACTION_POSTED
    if rule is not None and rule["status"] == RULE_STATUS_OBSERVING:
        _bump_rule_stats(conn, rule["id"], confirmed=False)
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_OBSERVING_RULE,
            priority=PRIORITY_OBSERVING,
            suggested_category=rule["target_category"],
            suggested_type=rule["target_type"],
        )
        return ACTION_QUEUED

    suggested_type, suggested_category = _suggest_side_income(source)
    _enqueue_review(
        conn,
        source_transaction_id=source["id"],
        reason=REASON_UNMATCHED,
        priority=PRIORITY_UNMATCHED,
        suggested_category=suggested_category,
        suggested_type=suggested_type,
    )
    return ACTION_QUEUED


def _already_processed(conn, batch_id: int) -> set[int]:
    source_ids = {
        int(row["source_transaction_id"])
        for row in conn.execute(
            """
            SELECT source_transaction_id FROM review_queue
            WHERE source_transaction_id IN (
              SELECT id FROM source_transactions WHERE batch_id = ?
            )
            """,
            (batch_id,),
        ).fetchall()
    }
    source_ids.update(
        int(row["source_transaction_id"])
        for row in conn.execute(
            """
            SELECT source_transaction_id FROM ledger_entries
            WHERE source_transaction_id IN (
              SELECT id FROM source_transactions WHERE batch_id = ?
            )
            """,
            (batch_id,),
        ).fetchall()
        if row["source_transaction_id"] is not None
    )
    return source_ids


def process_batch(db_path, batch_id: int) -> ProcessResult:
    """对一个批次执行入账决策（单事务，失败完整回滚，幂等可重跑）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        sources = conn.execute(
            """
            SELECT * FROM source_transactions
            WHERE batch_id = ?
            ORDER BY occurred_at ASC, id ASC
            """,
            (batch_id,),
        ).fetchall()
        already = _already_processed(conn, batch_id)
        posted = 0
        queued = 0
        skipped = 0
        for source in sources:
            if int(source["id"]) in already:
                skipped += 1
                continue
            action = process_source(conn, source)
            if action == ACTION_POSTED:
                posted += 1
            else:
                queued += 1
        batch = conn.execute(
            "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        _update_batch_counts(
            conn,
            batch_id,
            row_count=int(batch["row_count"]),
            accepted_count=int(batch["accepted_count"]),
            skipped_count=int(batch["skipped_count"]),
            pending_count=queued,
        )
        conn.commit()
        return ProcessResult(
            total=len(sources),
            posted=posted,
            queued=queued,
            skipped_existing=skipped,
        )
