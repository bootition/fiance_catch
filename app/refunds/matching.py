"""退款候选匹配（规格 §5：优先按平台订单、商户、金额、时间匹配原消费）。

匹配对象为已入账的消费 ledger_entries；找不到候选时退款停留在待确认，
不能默认作为收入。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import connect

# 支付宝退款订单号形如 <原单号>_RM<...>，前缀即原消费单号
RM_SUFFIX_RE = re.compile(r"^(.*?)_RM\d+$")
# 退款行商品说明形如"退款-商户单号XXX"；原消费行商品说明为"商户单号XXX"
MERCHANT_ID_RE = re.compile(r"商户单号([A-Za-z0-9_\-]+)")

MATCH_WINDOW_DAYS = 90


@dataclass(frozen=True)
class RefundCandidate:
    ledger_id: int
    amount_cents: int
    txn_date: str
    counterparty: str
    item_desc: str
    already_refunded_cents: int
    score: int
    match_reason: str


def _extract_refund_refs(refund_source) -> tuple[str, str]:
    """从退款流水提取可关联的订单引用：(原订单号前缀, 商户单号)。"""
    original_txn = ""
    match = RM_SUFFIX_RE.match(refund_source["source_txn_id"] or "")
    if match:
        original_txn = match.group(1)
    merchant_id = ""
    match = MERCHANT_ID_RE.search(refund_source["item_desc"] or "")
    if match:
        merchant_id = match.group(1)
    return original_txn, merchant_id


def _refunded_cents_by_ledger(conn, ledger_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(refund_amount_cents), 0) AS c
        FROM refund_links
        WHERE original_ledger_id = ?
        """,
        (ledger_id,),
    ).fetchone()
    return int(row["c"])


def find_refund_candidates(db_path, refund_source_id: int) -> list[RefundCandidate]:
    """为退款来源流水查找候选原消费（按匹配质量降序）。"""
    with connect(db_path) as conn:
        refund = conn.execute(
            "SELECT * FROM source_transactions WHERE id = ?", (refund_source_id,)
        ).fetchone()
        if refund is None:
            raise ValueError(f"refund source not found: {refund_source_id}")
        original_txn, merchant_id = _extract_refund_refs(refund)
        refund_date = refund["occurred_at"][:10]
        refund_amount = int(refund["amount_cents"])
        window_start = (
            datetime.strptime(refund_date, "%Y-%m-%d")
            - timedelta(days=MATCH_WINDOW_DAYS)
        ).strftime("%Y-%m-%d")

        rows = conn.execute(
            """
            SELECT le.id, le.amount_cents, le.txn_date, le.source_transaction_id
            FROM ledger_entries AS le
            WHERE le.entry_type = 'consumption'
              AND le.txn_date >= ? AND le.txn_date <= ?
            ORDER BY le.txn_date DESC, le.id DESC
            """,
            (window_start, refund_date),
        ).fetchall()

        candidates: list[RefundCandidate] = []
        for row in rows:
            source = None
            if row["source_transaction_id"] is not None:
                source = conn.execute(
                    "SELECT * FROM source_transactions WHERE id = ?",
                    (row["source_transaction_id"],),
                ).fetchone()
            counterparty = source["counterparty"] if source is not None else ""
            item_desc = source["item_desc"] if source is not None else ""
            source_txn = source["source_txn_id"] if source is not None else ""

            already = _refunded_cents_by_ledger(conn, row["id"])
            score, reason = _score(
                refund_amount=refund_amount,
                entry_amount=int(row["amount_cents"]),
                item_desc=item_desc,
                source_txn=source_txn,
                original_txn=original_txn,
                merchant_id=merchant_id,
                counterparty=source["counterparty"] if source is not None else "",
                refund_counterparty=refund["counterparty"],
            )
            if score <= 0:
                continue
            candidates.append(
                RefundCandidate(
                    ledger_id=int(row["id"]),
                    amount_cents=int(row["amount_cents"]),
                    txn_date=row["txn_date"],
                    counterparty=counterparty,
                    item_desc=item_desc,
                    already_refunded_cents=already,
                    score=score,
                    match_reason=reason,
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.txn_date))
        return candidates


def _score(
    *,
    refund_amount: int,
    entry_amount: int,
    item_desc: str,
    source_txn: str,
    original_txn: str,
    merchant_id: str,
    counterparty: str,
    refund_counterparty: str,
) -> tuple[int, str]:
    """匹配打分：订单级引用 100 > 商户单号 90 > 金额+同商户 70 > 金额+时间窗 60。"""
    if original_txn and source_txn == original_txn:
        return 100, "原交易订单号匹配"
    if merchant_id and merchant_id in item_desc:
        return 90, "商户单号匹配"
    if refund_amount != entry_amount:
        return 0, ""
    if counterparty and counterparty == refund_counterparty:
        return 70, "同商户金额匹配"
    return 60, "金额与时间窗口匹配"
