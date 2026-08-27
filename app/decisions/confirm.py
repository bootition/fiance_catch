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
    _create_ledger_entry,
)
from .constants import (
    BULK_TYPES,
    CATEGORY_TRAVEL,
    DIRECTION_ALLOWED_BULK_TYPES,
    REASON_OBSERVING_RULE,
    REASON_UNMATCHED,
    TYPE_TRANSFER,
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


def _looks_masked(counterparty: str) -> bool:
    """判断商户名是否是平台隐私脱敏值（不能作为规则条件）。"""
    text = (counterparty or "").strip()
    return not text or text in ("/", "-") or "*" in text


def _most_common_item_desc(items) -> str:
    """取组内出现频次最高的非空商品说明作为规则模式；无则返回空串。"""
    counts: dict[str, int] = {}
    for item in items:
        text = (item.item_desc or "").strip()
        if not text or text in ("/", "-"):
            continue
        counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda pair: (pair[1], -len(pair[0])))[0]


def _rule_condition_for_group(group: Group) -> tuple[str, str]:
    """自动规则条件：正常商户按商户名；脱敏商户改用商品说明。"""
    if not _looks_masked(group.counterparty):
        return "counterparty", group.counterparty.strip()
    return "item_desc", _most_common_item_desc(group.items)


def _validate_confirm_choice(direction: str, entry_type: str, category: str) -> str:
    """分类确认的类型/分类约束：方向已知，类型必须匹配；消费/收入必须给分类。"""
    entry_type = (entry_type or "").strip()
    category = (category or "").strip()
    if entry_type not in BULK_TYPES:
        raise ValueError(f"invalid entry_type for bulk confirm: {entry_type!r}")
    direction = (direction or "").strip()
    allowed = DIRECTION_ALLOWED_BULK_TYPES.get(direction, frozenset())
    if entry_type not in allowed:
        label = {"income": "收入", "expense": "支出", "neutral": "不计收支"}.get(direction, direction)
        raise ValueError(f"{label}方向的交易不能确认为该类型")
    if entry_type == TYPE_TRANSFER:
        category = ""
    elif not category:
        raise ValueError("消费/收入必须选择分类")
    return category


def _sync_batch_pending_counts(conn, batch_ids) -> None:
    for batch_id in batch_ids:
        if batch_id is None:
            continue
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


@dataclass(frozen=True)
class GroupItem:
    review_id: int
    source_id: int
    batch_id: int | None
    amount_cents: int
    occurred_at: str
    item_desc: str
    raw_type: str
    reason: str
    suggested_category: str
    suggested_type: str


@dataclass(frozen=True)
class Group:
    counterparty: str
    platform: str
    direction: str
    items: list[GroupItem]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def total_cents(self) -> int:
        return sum(item.amount_cents for item in self.items)

    @property
    def item_desc_samples(self) -> list[str]:
        """本组商品/说明的去重样本（最多 3 个），用于表格快速辨认。"""
        samples: list[str] = []
        for item in self.items:
            text = (item.item_desc or "").strip()
            if text and text not in ("/", "-") and text not in samples:
                samples.append(text)
            if len(samples) >= 3:
                break
        return samples

    @property
    def first_occurred_at(self) -> str:
        return self.items[0].occurred_at

    @property
    def last_occurred_at(self) -> str:
        return self.items[-1].occurred_at


def group_review_items(db_path) -> list[Group]:
    """把 pending 分类区待确认项（未命中/观察期预填）按商户×平台×收支方向分组。

    方向参与分组键（红队修复，2026-08-14）：同一商户的收入与支出
    方向不同，混合一组会导致批量确认把两类交易打成同一类型。
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
              st.direction,
              st.occurred_at,
              st.amount_cents,
              st.item_desc,
              st.raw_type,
              st.batch_id
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending' AND rq.reason IN ({placeholders})
            ORDER BY st.counterparty ASC, st.direction ASC, st.occurred_at ASC
            """,
            tuple(ALLOWED_BULK_REASONS),
        ).fetchall()
    groups: dict[tuple[str, str, str], list[GroupItem]] = {}
    for row in rows:
        key = (row["counterparty"], row["platform"], row["direction"])
        groups.setdefault(key, []).append(
            GroupItem(
                review_id=int(row["review_id"]),
                source_id=int(row["source_transaction_id"]),
                batch_id=row["batch_id"],
                amount_cents=int(row["amount_cents"]),
                occurred_at=row["occurred_at"],
                item_desc=row["item_desc"],
                raw_type=row["raw_type"] or "",
                reason=row["reason"],
                suggested_category=row["suggested_category"] or "",
                suggested_type=row["suggested_type"] or "",
            )
        )
    return [
        Group(counterparty=key[0], platform=key[1], direction=key[2], items=items)
        for key, items in sorted(groups.items())
    ]


def group_review_items_paged(
    db_path, *, page: int = 1, per_page: int = 30, q: str = ""
) -> tuple[list[Group], int, int]:
    """分类区分页 + 按商户模糊搜索。

    返回 (当前页 groups, 过滤后总组数, 过滤后总笔数)。
    分组仍在全量结果上完成（内存分组为毫秒级），分页仅限制渲染到页面的
    组数——这是消除页面渲染卡顿的关键（渲染几百个原生 select 才是瓶颈）。
    """
    groups = group_review_items(db_path)
    query = (q or "").strip()
    if query:
        needle = query.lower()
        groups = [
            g
            for g in groups
            if needle in (g.counterparty or "").lower()
            or any(
                needle in (item.item_desc or "").lower()
                or needle in (item.raw_type or "").lower()
                for item in g.items
            )
        ]
    total_groups = len(groups)
    total_items = sum(g.count for g in groups)
    total_pages = max(1, (total_groups + per_page - 1) // per_page)
    safe_page = max(1, min(int(page), total_pages))
    start = (safe_page - 1) * per_page
    return groups[start : start + per_page], total_groups, total_items


@dataclass(frozen=True)
class ConfirmResult:
    confirmed: int
    rule_id: int | None = None  # 用户指引 2026-08-27：不再自动创建规则


def confirm_group(
    db_path,
    counterparty: str,
    platform: str,
    *,
    direction: str = "expense",
    entry_type: str,
    category: str,
    match_field: str = "counterparty",
) -> ConfirmResult:
    """批量确认同商户同方向待确认项：统一入账并关闭队列项。

    用户指引（2026-08-27）：不再根据确认结果自动总结/创建规则；
    规则只由用户操作 AI 显式写入。每条入账记录写入独立 bulk_confirm
    审计事件，可从流水详情追溯。
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        groups = group_review_items(db_path)
        group = next(
            (
                g
                for g in groups
                if g.counterparty == counterparty
                and g.platform == platform
                and g.direction == direction
            ),
            None,
        )
        if group is None:
            raise ValueError(
                f"no pending review group: {counterparty} ({platform}/{direction})"
            )

        high_risk = {item.reason for item in group.items} & HIGH_RISK_REASONS
        if high_risk:
            raise ValueError(
                f"high-risk reasons cannot be bulk confirmed: {sorted(high_risk)}"
            )

        category = _validate_confirm_choice(direction, entry_type, category)

        rule_id = None

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
                ref_ledger_id=entry_id,
                ref_rule_id=rule_id,
                ref_batch_id=item.batch_id,
                detail=(
                    f"counterparty:{counterparty};direction:{direction};"
                    f"type:{entry_type};category:{category}"
                ),
            )

        _sync_batch_pending_counts(
            conn,
            {item.batch_id for item in group.items if item.batch_id is not None},
        )

        conn.commit()
        return ConfirmResult(confirmed=len(group.items), rule_id=rule_id)


def confirm_review_item(
    db_path,
    review_id: int,
    *,
    entry_type: str,
    category: str,
) -> ConfirmResult:
    """只处理分组中的某一笔（不处理同组其他笔；不创建规则）。

    用于用户反馈：合并规则 = 商户 × 平台 × 收支方向；当合并不恰当时，
    展开明细后可逐笔单独确认。
    """
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute(
            """
            SELECT * FROM review_queue
            WHERE id = ? AND status = 'pending'
            """,
            (int(review_id),),
        ).fetchone()
        if review is None:
            raise ValueError(f"no pending review: {review_id}")
        if review["reason"] not in ALLOWED_BULK_REASONS:
            raise ValueError(f"review {review_id} is not in the classification zone")

        source = conn.execute(
            "SELECT * FROM source_transactions WHERE id = ?",
            (review["source_transaction_id"],),
        ).fetchone()
        if source is None:
            raise ValueError(f"source transaction not found: {review['source_transaction_id']}")

        category = _validate_confirm_choice(
            source["direction"], entry_type, category
        )
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
            event_type="bulk_confirm",
            ref_ledger_id=entry_id,
            ref_batch_id=source["batch_id"],
            detail=(
                f"scope:single;review:{review_id};"
                f"counterparty:{source['counterparty']};direction:{source['direction']};"
                f"type:{entry_type};category:{category}"
            ),
        )
        _sync_batch_pending_counts(conn, {source["batch_id"]})
        conn.commit()
        return ConfirmResult(confirmed=1, rule_id=None)


def promote_rule(db_path, rule_id: int) -> bool:
    """把观察期规则提升为自动入账（经用户验证）；已在队列的观察项保持待处理。

    空匹配模式规则禁止提升（红队 P1：空模式会匹配全部交易）；
    旅游类规则禁止提升（规格 §2.2：旅游必须用户确认，红队修复 2026-08-14）。
    """
    with connect(db_path) as conn:
        return _promote_rule(conn, rule_id)


def _promote_rule(conn, rule_id: int) -> bool:
    rule = conn.execute(
        "SELECT match_pattern, status, target_category FROM classification_rules WHERE id = ?",
        (rule_id,),
    ).fetchone()
    if rule is None or rule["status"] != "observing":
        return False
    if not rule["match_pattern"].strip():
        return False
    if rule["target_category"] == CATEGORY_TRAVEL:
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
