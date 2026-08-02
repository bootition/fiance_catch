"""待确认相似项分组与批量确认（规格 §3.3）。

风险区与分类区隔离：批量确认仅允许分类区原因（未命中、观察期规则预填）。
退款（refund_pending）、提现（withdrawal）、人际转账（person_transfer）、
其他中性资金流（other_neutral）为高风险区，禁止经通用 entry_type/category
入口批量确认（分别由受约束流程处理：退款关联在阶段 4，提现/人际逐笔选用途）。
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import (
    _add_audit_event,
    _create_classification_rule,
    _create_ledger_entry,
)
from .constants import (
    REASON_OBSERVING_RULE,
    REASON_UNMATCHED,
)

REVIEW_PENDING = "pending"

# 允许批量指定分类的待确认原因（分类区）
ALLOWED_BULK_REASONS = frozenset({REASON_UNMATCHED, REASON_OBSERVING_RULE})

# 高风险原因：任何情况下不得进入批量确认
HIGH_RISK_REASONS = frozenset(
    {
        "refund_pending",
        "withdrawal",
        "person_transfer",
        "other_neutral",
    }
)


@dataclass(frozen=True)
class GroupItem:
    review_id: int
    source_id: int
    batch_id: int | None
    amount_cents: int
    occurred_at: str
    item_desc: str
    reason: str
    suggested_category: str
    suggested_type: str


@dataclass(frozen=True)
class Group:
    counterparty: str
    platform: str
    items: list[GroupItem]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def total_cents(self) -> int:
        return sum(item.amount_cents for item in self.items)


def group_review_items(db_path) -> list[Group]:
    """把 pending 分类区待确认项（未命中/观察期预填）按商户分组。

    高风险区（退款/提现/人际/其他中性资金流）不参与分组，
    由各自受约束流程逐笔处理。
    """
    placeholders = ",".join("?" * len(ALLOWED_BULK_REASONS))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
              rq.id AS review_id,
              rq.source_transaction_id,
              rq.reason,
              rq.suggested_category,
              rq.suggested_type,
              st.platform,
              st.counterparty,
              st.occurred_at,
              st.amount_cents,
              st.item_desc,
              st.batch_id
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending' AND rq.reason IN ({placeholders})
            ORDER BY st.counterparty ASC, st.occurred_at ASC
            """,
            tuple(ALLOWED_BULK_REASONS),
        ).fetchall()
    groups: dict[tuple[str, str], list[GroupItem]] = {}
    for row in rows:
        key = (row["counterparty"], row["platform"])
        groups.setdefault(key, []).append(
            GroupItem(
                review_id=int(row["review_id"]),
                source_id=int(row["source_transaction_id"]),
                batch_id=row["batch_id"],
                amount_cents=int(row["amount_cents"]),
                occurred_at=row["occurred_at"],
                item_desc=row["item_desc"],
                reason=row["reason"],
                suggested_category=row["suggested_category"] or "",
                suggested_type=row["suggested_type"] or "",
            )
        )
    return [
        Group(counterparty=key[0], platform=key[1], items=items)
        for key, items in sorted(groups.items())
    ]


@dataclass(frozen=True)
class ConfirmResult:
    confirmed: int
    rule_id: int | None  # 建议创建的观察规则（若存在重复模式）


def confirm_group(
    db_path,
    counterparty: str,
    platform: str,
    *,
    entry_type: str,
    category: str,
    match_field: str = "counterparty",
) -> ConfirmResult:
    """批量确认同商户待确认项：统一入账并关闭队列项；若 ≥2 条则建议创建观察规则。

    match_field：创建规则时匹配商户（counterparty）或商品（item_desc）。
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        groups = group_review_items(db_path)
        group = next(
            (g for g in groups if g.counterparty == counterparty and g.platform == platform),
            None,
        )
        if group is None:
            raise ValueError(f"no pending review group: {counterparty} ({platform})")

        high_risk = {item.reason for item in group.items} & HIGH_RISK_REASONS
        if high_risk:
            raise ValueError(
                f"high-risk reasons cannot be bulk confirmed: {sorted(high_risk)}"
            )

        for item in group.items:
            source = conn.execute(
                "SELECT * FROM source_transactions WHERE id = ?", (item.source_id,)
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
                (entry_id, item.review_id),
            )
        _add_audit_event(
            conn,
            event_type="bulk_confirm",
            ref_batch_id=group.items[0].source_id,
            detail=f"counterparty:{counterparty};type:{entry_type};category:{category}",
        )

        affected_batches = {
            item.batch_id for item in group.items if item.batch_id is not None
        }
        for batch_id in affected_batches:
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
            conn.execute(
                "UPDATE import_batches SET pending_count = ? WHERE id = ?",
                (real_pending, batch_id),
            )

        rule_id = None
        if len(group.items) >= 2:
            pattern = (
                counterparty if match_field == "counterparty" else group.items[0].item_desc
            )
            if pattern:
                rule_id = _create_classification_rule(
                    conn,
                    match_field=match_field,
                    match_pattern=pattern,
                    target_type=entry_type,
                    target_category=category,
                )
        conn.commit()
        return ConfirmResult(confirmed=len(group.items), rule_id=rule_id)


def promote_rule(db_path, rule_id: int) -> bool:
    """把观察期规则提升为自动入账（经用户验证）；已在队列的观察项保持待处理。

    空匹配模式规则禁止提升（红队 P1：空模式会匹配全部交易）。
    """
    with connect(db_path) as conn:
        return _promote_rule(conn, rule_id)


def _promote_rule(conn, rule_id: int) -> bool:
    rule = conn.execute(
        "SELECT match_pattern, status FROM classification_rules WHERE id = ?",
        (rule_id,),
    ).fetchone()
    if rule is None or rule["status"] != "observing":
        return False
    if not rule["match_pattern"].strip():
        return False
    cur = conn.execute(
        """
        UPDATE classification_rules
        SET status = 'active', updated_at = datetime('now')
        WHERE id = ? AND status = 'observing'
        """,
        (rule_id,),
    )
    return int(cur.rowcount) > 0
