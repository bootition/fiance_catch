"""待确认相似项分组与批量确认（规格 §3.3）。

- 按商户（counterparty）分组待确认项，支持批量指定分类/类型
- 每次批量确认后，若组内存在重复模式（同商户多条），建议创建观察期规则
"""

from dataclasses import dataclass

from ..db import connect
from ..ledger_repo import (
    _add_audit_event,
    _create_classification_rule,
    _create_ledger_entry,
)

REVIEW_PENDING = "pending"


@dataclass(frozen=True)
class GroupItem:
    review_id: int
    source_id: int
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
    """把 pending 待确认项按商户分组（同类分类区批量确认）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
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
              st.item_desc
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
            ORDER BY st.counterparty ASC, st.occurred_at ASC
            """
        ).fetchall()
    groups: dict[tuple[str, str], list[GroupItem]] = {}
    for row in rows:
        key = (row["counterparty"], row["platform"])
        groups.setdefault(key, []).append(
            GroupItem(
                review_id=int(row["review_id"]),
                source_id=int(row["source_transaction_id"]),
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
    """把观察期规则提升为自动入账（经用户验证）；已在队列的观察项保持待处理。"""
    with connect(db_path) as conn:
        return _promote_rule(conn, rule_id)


def _promote_rule(conn, rule_id: int) -> bool:
    cur = conn.execute(
        """
        UPDATE classification_rules
        SET status = 'active', updated_at = datetime('now')
        WHERE id = ? AND status = 'observing'
        """,
        (rule_id,),
    )
    return int(cur.rowcount) > 0
