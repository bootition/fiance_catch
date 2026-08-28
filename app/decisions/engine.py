"""入账决策引擎：把批次来源流水转为账本记录或待确认项（规格 §2/§5/§7.3）。

判定顺序（规格 §5 导入规则）：
1. 退款 → 退款待办（阶段 4 匹配原消费）
2. 提现到银行卡 → 逐笔选用途（待确认，绝不自动定性）
3. 人际转账/红包/收款 → 待确认
4. 不计收支：明确调拨 → transfer 入账；其他 → 待确认
5. 消费/收入：active 规则 → 自动入账；observing 规则 → 预填待确认；
   未命中 → 待确认
"""

import re
from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import (
    _add_audit_event,
    _bump_rule_stats,
    _create_ledger_entry,
    _enqueue_review,
    _update_batch_counts,
)
from .builtin_rules import matches_builtin_huabei_discard, matches_builtin_interest_income, matches_builtin_internal_transfer, matches_builtin_jd, matches_builtin_meituan, matches_builtin_side_income, matches_builtin_transport
from .constants import (
    CATEGORY_SIDE_INCOME,
    DIRECTION_ALLOWED_BULK_TYPES,
    CATEGORY_TRAVEL,
    REASON_OBSERVING_RULE,
    REASON_OTHER_NEUTRAL,
    REASON_PERSON_TRANSFER,
    REASON_REFUND_PENDING,
    REASON_UNMATCHED,
    REASON_WITHDRAWAL,
    SIDE_INCOME_KEYWORDS,
    TRANSFER_KEYWORDS,
    TYPE_CONSUMPTION,
    TYPE_INCOME,
    TYPE_TRANSFER,
    WITHDRAWAL_KEYWORDS,
)
from .rules import RULE_STATUS_ACTIVE, RULE_STATUS_OBSERVING, match_rules

from ..refunds.status import is_refund_status

ALIPAY_PERSON_TYPES = {"转账红包", "亲友代付", "红包"}
WECHAT_PERSON_TYPES = {"转账", "微信红包"}

# 商家收款码支付描述（支出方向）：不是人际"收款"（红队 P1 修复，2026-08-14）
_MERCHANT_COLLECT_RE = re.compile(r"(收钱码|二维码|扫码)收款")

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
    return is_refund_status(source["platform"], source["status_text"])


def _is_withdrawal(source) -> bool:
    haystack = f"{source['raw_type']} {source['item_desc']} {source['counterparty']}"
    return any(kw in haystack for kw in WITHDRAWAL_KEYWORDS)


def _is_person_transfer(source) -> bool:
    if source["platform"] == "alipay" and source["raw_type"] in ALIPAY_PERSON_TYPES:
        return True
    if source["platform"] == "wechat" and source["raw_type"] in WECHAT_PERSON_TYPES:
        return True
    desc = f"{source['item_desc']} {source['counterparty']}"
    if source["direction"] == "expense" and _MERCHANT_COLLECT_RE.search(desc):
        return False  # 商家收款码支付，不是人际转账（红队 P1 修复）
    if "收款" in desc and source["direction"] == "income":
        return True  # 收入方向的“收款”才是人际收款
    if "转账" in desc:
        return True
    if "红包" in desc and source["direction"] != "expense":
        return True  # 支出方向的“红包”多为促销抵扣描述
    return False


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
        rule = match_rules(
            conn,
            source["counterparty"],
            source["item_desc"],
            platform=source["platform"],
            direction=source["direction"],
            raw_type=source["raw_type"],
        )
        if (
            rule is not None
            and rule["status"] == RULE_STATUS_ACTIVE
            and rule["target_type"] in DIRECTION_ALLOWED_BULK_TYPES.get(source["direction"], frozenset())
        ):
            entry_id = _create_ledger_entry(
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
                ref_ledger_id=entry_id,
                ref_rule_id=rule["id"],
                ref_batch_id=source["batch_id"],
                detail=f"field:{rule['match_field']};pattern:{rule['match_pattern']}",
            )
            return ACTION_POSTED
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_PERSON_TRANSFER,
            priority=PRIORITY_PERSON,
        )
        return ACTION_QUEUED
    if source["direction"] == "neutral":
        if matches_builtin_interest_income(source):
            entry_id = _create_ledger_entry(
                conn,
                entry_type=TYPE_INCOME,
                amount_cents=source["amount_cents"],
                category="其他收入",
                txn_date=source["occurred_at"][:10],
                source_transaction_id=source["id"],
                batch_id=source["batch_id"],
                note="",
            )
            _add_audit_event(
                conn,
                event_type="rule_applied",
                ref_ledger_id=entry_id,
                ref_batch_id=source["batch_id"],
                detail="builtin:1;field:interest;pattern:interest",
            )
            return ACTION_POSTED
        if matches_builtin_internal_transfer(source) or matches_builtin_huabei_discard(source):
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
        rule = match_rules(
            conn,
            source["counterparty"],
            source["item_desc"],
            platform=source["platform"],
            direction=source["direction"],
            raw_type=source["raw_type"],
        )
        if (
            rule is not None
            and rule["status"] == RULE_STATUS_ACTIVE
            and rule["target_type"] == TYPE_TRANSFER
        ):
            entry_id = _create_ledger_entry(
                conn,
                entry_type=TYPE_TRANSFER,
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
                ref_ledger_id=entry_id,
                ref_rule_id=rule["id"],
                ref_batch_id=source["batch_id"],
                detail=f"field:{rule['match_field']};pattern:{rule['match_pattern']}",
            )
            return ACTION_POSTED
        _enqueue_review(
            conn,
            source_transaction_id=source["id"],
            reason=REASON_OTHER_NEUTRAL,
            priority=PRIORITY_NEUTRAL,
        )
        return ACTION_QUEUED

    rule = match_rules(
        conn,
        source["counterparty"],
        source["item_desc"],
        platform=source["platform"],
        direction=source["direction"],
        raw_type=source["raw_type"],
    )
    # 旅游类规则永不自动入账（规格 §2.2：旅游必须用户确认），
    # 即使存在 active 旅游规则也按观察期预填处理，命中照常计数（红队修复 2026-08-14）
    if (
        rule is not None
        and rule["status"] == RULE_STATUS_ACTIVE
        and rule["target_category"] != CATEGORY_TRAVEL
    ):
        entry_id = _create_ledger_entry(
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
            ref_ledger_id=entry_id,
            ref_rule_id=rule["id"],
            ref_batch_id=source["batch_id"],
            detail=(
                f"source:{source['id']};field:{rule['match_field']};"
                f"pattern:{rule['match_pattern']}"
            ),
        )
        return ACTION_POSTED
    if (
        rule is not None
        and (
            rule["status"] == RULE_STATUS_OBSERVING
            or rule["target_category"] == CATEGORY_TRAVEL
        )
    ):
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

    builtin_pattern = matches_builtin_transport(source)
    if builtin_pattern is not None:
        entry_id = _create_ledger_entry(
            conn,
            entry_type=TYPE_CONSUMPTION,
            amount_cents=source["amount_cents"],
            category="出行交通",
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        _add_audit_event(
            conn,
            event_type="rule_applied",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=f"builtin:1;field:item_desc;pattern:{builtin_pattern}",
        )
        return ACTION_POSTED

    meituan_match = matches_builtin_meituan(source)
    if meituan_match is not None:
        category, pattern = meituan_match
        entry_id = _create_ledger_entry(
            conn,
            entry_type=TYPE_CONSUMPTION,
            amount_cents=source["amount_cents"],
            category=category,
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        _add_audit_event(
            conn,
            event_type="rule_applied",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=f"builtin:1;field:meituan;pattern:{pattern}",
        )
        return ACTION_POSTED

    jd_pattern = matches_builtin_jd(source)
    if jd_pattern is not None:
        entry_id = _create_ledger_entry(
            conn,
            entry_type=TYPE_CONSUMPTION,
            amount_cents=source["amount_cents"],
            category="日常三餐",
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        _add_audit_event(
            conn,
            event_type="rule_applied",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=f"builtin:1;field:jd;pattern:{jd_pattern}",
        )
        return ACTION_POSTED

    side_income_pattern = matches_builtin_side_income(source)
    if side_income_pattern is not None:
        entry_id = _create_ledger_entry(
            conn,
            entry_type=TYPE_INCOME,
            amount_cents=source["amount_cents"],
            category=CATEGORY_SIDE_INCOME,
            txn_date=source["occurred_at"][:10],
            source_transaction_id=source["id"],
            batch_id=source["batch_id"],
            note="",
        )
        _add_audit_event(
            conn,
            event_type="rule_applied",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=f"builtin:1;field:side_income;pattern:{side_income_pattern}",
        )
        return ACTION_POSTED

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
        real_pending = int(
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
        _update_batch_counts(
            conn,
            batch_id,
            row_count=int(batch["row_count"]),
            accepted_count=int(batch["accepted_count"]),
            skipped_count=int(batch["skipped_count"]),
            pending_count=real_pending,
        )
        conn.commit()
        return ProcessResult(
            total=len(sources),
            posted=posted,
            queued=queued,
            skipped_existing=skipped,
        )
