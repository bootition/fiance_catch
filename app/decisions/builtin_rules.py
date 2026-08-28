"""内置高置信度交通识别规则（用户指引 2026-08-27）。

这些规则是产品级硬编码默认值，不属于用户可停用的 classification_rules：
- 方向=支出且商品说明含「地铁_ / 单车 / 骑行 / 公交」→ 消费 · 出行交通
- 新导入直接自动入账；既有 unmatched 队列可用 apply_builtin_rules_to_pending 一次性应用
"""

from dataclasses import dataclass
import re

from ..db import connect
from ..ledger_repo import (
    _add_audit_event,
    _create_ledger_entry,
)
from .constants import CATEGORY_TRANSPORT, REASON_UNMATCHED, TYPE_CONSUMPTION

# 用「地铁_」而不是「地铁」，避免把“XX地铁站旁奶茶店”误判成交通
BUILTIN_TRANSPORT_ITEM_PATTERNS = (
    "地铁_",
    "单车",
    "骑行",
    "公交",
    "机票",
    "火车票",
)


@dataclass(frozen=True)
class BuiltinApplyResult:
    posted: int
    patterns: tuple[str, ...] = BUILTIN_TRANSPORT_ITEM_PATTERNS


def matches_builtin_transport(source) -> str | None:
    """命中返回模式，否则 None。只匹配支出方向。

    - 商品说明关键词：地铁_ / 单车 / 骑行 / 公交 / 机票 / 火车票
    - 商户名以「出行」结尾：滴滴出行、哈啰出行、曹操出行等打车软件
    """
    if source["direction"] != "expense":
        return None
    item_desc = str(source["item_desc"] or "")
    counterparty = str(source["counterparty"] or "")
    for pattern in BUILTIN_TRANSPORT_ITEM_PATTERNS:
        if pattern in item_desc:
            return pattern
    if counterparty.endswith("出行"):
        return "counterparty:出行"
    if "青桔" in counterparty or "青桔" in item_desc:
        return "青桔"
    return None


def matches_builtin_side_income(source) -> str | None:
    """闲鱼收入识别（比“支付宝所有收入”更严格）。

    只认两类高置信形态：
    - 支付宝收入方向 + 交易对方被平台脱敏（含 `*`）——C2C 买家形态
    - 已知副业来源商户（如 网易BUFF卖家）
    公司/机构发来的工资、报销等 unmasked 收入不会命中，留给用户确认。
    """
    if source["platform"] != "alipay" or source["direction"] != "income":
        return None
    raw_type = str(source["raw_type"] or "")
    if raw_type.strip() != "收入":
        return None
    counterparty = str(source["counterparty"] or "")
    item_desc = str(source["item_desc"] or "")
    if "*" in counterparty and len(item_desc.strip()) >= 10:
        return "masked_counterparty"
    if counterparty in ("网易BUFF卖家",):
        return "known_counterparty"
    return None


def matches_builtin_jd(source):
    """京东数字编号小金额 → 日常三餐（用户指引 2026-08-28）。"""
    if source["direction"] != "expense":
        return None
    counterparty = str(source["counterparty"] or "")
    item_desc = str(source["item_desc"] or "").strip()
    if "京东" not in counterparty and "京东" not in item_desc:
        return None
    numeric = re.sub(r"^京东-?订单编号", "", item_desc)
    if re.fullmatch(r"\d+", numeric) and int(source["amount_cents"]) < 3500:
        return "jd_numeric_small"
    return None


def matches_builtin_meituan(source):
    """美团消费形态识别（用户指引 2026-08-27）。

    - 只有一串编号：`美团订单-数字...` → 出行交通
    - 有商品名称（含中文）→ 日常三餐
    返回 (category, pattern)；不适用返回 None。
    """
    if source["direction"] != "expense":
        return None
    counterparty = str(source["counterparty"] or "")
    item_desc = str(source["item_desc"] or "").strip()
    is_meituan = (
        counterparty in ("美团", "美团平台商户")
        or "美团App" in item_desc
        or "美团微信小程序" in item_desc
        or "拼好饭微信小程序" in item_desc
    )
    if not is_meituan:
        return None
    if re.fullmatch(r"美团订单-\d+", item_desc):
        return ("出行交通", "meituan_serial")
    if "合并支付购买" in item_desc or not item_desc:
        return None
    if re.search(r"[一-鿿]", item_desc):
        return ("日常三餐", "meituan_named")
    return None


def matches_builtin_interest_income(source):
    """余额宝/货币基金收益 → 收入·其他收入（用户指引 2026-08-28）。"""
    if source["direction"] != "neutral":
        return None
    item_desc = str(source["item_desc"] or "")
    if "收益发放" in item_desc or "利息" in item_desc:
        return "interest"
    return None


def matches_builtin_internal_transfer(source):
    """余利宝/网商银行账户内调转：丢弃为调拨，不影响收支。"""
    if source["direction"] != "neutral":
        return None
    text = f"{source['counterparty']} {source['item_desc']}"
    if "余利宝" in text or "网商银行" in text:
        return "internal_transfer"
    return None


def matches_builtin_huabei_discard(source):
    """花呗还款：消费已记账，还款本身丢弃为调拨（不计收支）。"""
    if source["direction"] != "neutral":
        return None
    text = f"{source['counterparty']} {source['item_desc']} {source['raw_type']}"
    if "花呗" in text and ("还款" in text or "信用借还" in text):
        return "huabei_repay"
    return None


def _post_builtin(
    conn,
    source,
    review_id,
    pattern: str,
    *,
    entry_type: str = TYPE_CONSUMPTION,
    category: str = CATEGORY_TRANSPORT,
    match_field: str = "item_desc",
) -> int:
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
        event_type="rule_applied",
        ref_ledger_id=entry_id,
        ref_batch_id=source["batch_id"],
        detail=f"builtin:1;field:{match_field};pattern:{pattern}",
    )
    return entry_id


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


def apply_builtin_rules_to_pending(db_path) -> BuiltinApplyResult:
    """对既有 unmatched 待确认项应用内置交通规则（单事务）。"""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT
              rq.id AS review_id,
              rq.source_transaction_id,
              st.*
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
              AND rq.reason = ?
            ORDER BY st.occurred_at ASC, st.id ASC
            """,
            (REASON_UNMATCHED,),
        ).fetchall()
        posted = 0
        for row in rows:
            source = dict(row)
            pattern = matches_builtin_transport(source)
            if pattern is not None:
                _post_builtin(conn, source, source["review_id"], pattern)
                _sync_batch_pending(conn, source["batch_id"])
                posted += 1
                continue
            side_income_pattern = matches_builtin_side_income(source)
            if side_income_pattern is not None:
                _post_builtin(
                    conn,
                    source,
                    source["review_id"],
                    side_income_pattern,
                    entry_type=TYPE_INCOME,
                    category=CATEGORY_SIDE_INCOME,
                    match_field="side_income",
                )
                _sync_batch_pending(conn, source["batch_id"])
                posted += 1
                continue
            meituan_match = matches_builtin_meituan(source)
            if meituan_match is not None:
                category, pattern = meituan_match
                _post_builtin(
                    conn,
                    source,
                    source["review_id"],
                    pattern,
                    entry_type=TYPE_CONSUMPTION,
                    category=category,
                    match_field="meituan",
                )
                _sync_batch_pending(conn, source["batch_id"])
                posted += 1
                continue
            jd_pattern = matches_builtin_jd(source)
            if jd_pattern is not None:
                _post_builtin(
                    conn,
                    source,
                    source["review_id"],
                    jd_pattern,
                    entry_type=TYPE_CONSUMPTION,
                    category="日常三餐",
                    match_field="jd",
                )
                _sync_batch_pending(conn, source["batch_id"])
                posted += 1
        conn.commit()
        return BuiltinApplyResult(posted=posted)
