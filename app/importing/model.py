from dataclasses import dataclass
from datetime import date as dt_date
from datetime import datetime as dt_datetime
from decimal import Decimal, InvalidOperation
from enum import Enum


class RowStatus(str, Enum):
    """平台原始状态分类（规格 §5 导入规则）。"""

    SUCCESS = "success"  # 成功/已到账：进入标准化、去重、入账/待确认决策
    SKIPPED = "skipped"  # 关闭/失败/未知/进行中：跳过，批次报告计数
    REFUND = "refund"  # 退款：创建退款待办，匹配原消费后才能影响统计


class Platform(str, Enum):
    ALIPAY = "alipay"
    WECHAT = "wechat"


@dataclass(frozen=True)
class NormalizedTransaction:
    platform: str
    source_txn_id: str
    occurred_at: str  # YYYY-MM-DD HH:MM:SS
    amount_cents: int
    direction: str  # expense | income | neutral（平台方向）
    status: RowStatus
    status_text: str  # 平台原始状态文本，保留溯源
    counterparty: str
    item_desc: str
    raw_type: str  # 平台原始交易分类/交易类型（人际转账、提现判定依据）
    note: str
    normalized_hash: str


def amount_to_cents(value) -> int:
    """金额（str/Decimal/int/float）→ 分。

    负数与超过 2 位小数的金额一律拒绝（与手工补账口径一致，红队修复 2026-08-14）：
    解析阶段抛 ValueError 使整批导入失败回滚，坏金额永不落库。
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid amount: {value!r}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    if amount < 0:
        raise ValueError(f"negative amount: {value!r}")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"amount supports up to 2 decimals: {value!r}")
    return int(cents)


def normalize_note(note) -> str:
    """备注：微信用 '/' 表示无，统一为空串。"""
    if note is None:
        return ""
    text = str(note).strip()
    if text in ("/", "-"):
        return ""
    return text


def normalize_occurred_at(value) -> str:
    """解析并规范化交易时间为 YYYY-MM-DD HH:MM:SS。

    接受 datetime/date 对象（微信 XLSX 日期单元格）或
    "YYYY-MM-DD[ HH:MM[:SS]]" 字符串（支付宝 CSV）。
    空值、格式非法或日历日期不存在（如 2026-02-30）一律抛 ValueError，
    使整批导入在解析阶段失败回滚，坏日期永不落库。
    """
    if isinstance(value, dt_datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt_date):
        return f"{value.strftime('%Y-%m-%d')} 00:00:00"
    if value is None:
        raise ValueError("invalid occurred_at: empty")
    text = str(value).strip()
    if not text:
        raise ValueError("invalid occurred_at: empty")
    try:
        parsed = dt_datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid occurred_at: {text!r}") from exc
    return parsed.strftime("%Y-%m-%d %H:%M:%S")
