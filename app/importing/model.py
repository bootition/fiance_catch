from dataclasses import dataclass
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
    note: str
    normalized_hash: str


def amount_to_cents(value) -> int:
    """金额（str/Decimal/int/float）→ 分。"""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid amount: {value!r}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    if amount < 0:
        raise ValueError(f"negative amount: {value!r}")
    return int((amount * 100).quantize(Decimal("1")))


def normalize_note(note) -> str:
    """备注：微信用 '/' 表示无，统一为空串。"""
    if note is None:
        return ""
    text = str(note).strip()
    if text in ("/", "-"):
        return ""
    return text
