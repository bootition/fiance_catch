import hashlib
import io
import csv

from .model import NormalizedTransaction, Platform, RowStatus, amount_to_cents, normalize_note, normalize_occurred_at

ALIPAY_SUCCESS = {
    "交易成功",
    "支付成功",
    "代付成功",
    "还款成功",
    "名下账户代付付款成功",
}
ALIPAY_REFUND = {"退款成功"}
# 关闭/失败/已撤销/进行中等一律跳过（宁可跳过不可猜）
ALIPAY_SKIP = {"交易关闭", "已撤销", "等待对方确认收货"}

# 表头列（0 基）：交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
COL_TIME = 0
COL_CATEGORY = 1
COL_COUNTERPARTY = 2
COL_ACCOUNT = 3
COL_ITEM = 4
COL_DIRECTION = 5
COL_AMOUNT = 6
COL_PAY_METHOD = 7
COL_STATUS = 8
COL_TXN_ID = 9
COL_MERCHANT_ID = 10
COL_NOTE = 11
EXPECTED_COLS = 12

DIRECTION_MAP = {
    "支出": "expense",
    "收入": "income",
    "不计收支": "neutral",
}


def _classify_status(status_text: str) -> RowStatus:
    text = status_text.strip()
    if text in ALIPAY_SUCCESS:
        return RowStatus.SUCCESS
    if text in ALIPAY_REFUND:
        return RowStatus.REFUND
    return RowStatus.SKIPPED


def _normalized_hash(
    platform: str,
    source_txn_id: str,
    occurred_at: str,
    amount_cents: int,
    direction: str,
    counterparty: str,
    item_desc: str,
) -> str:
    payload = "|".join(
        [
            platform,
            source_txn_id,
            occurred_at,
            str(amount_cents),
            direction,
            counterparty,
            item_desc,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_alipay_csv_bytes(data: bytes) -> list[NormalizedTransaction]:
    """解析支付宝交易明细 CSV（GB18030），返回标准化行。

    头部为导出信息，表头行以“交易时间”开头；商品说明可能含半角逗号，
    用 csv.reader 解析并将超列内容并入商品说明。
    """
    text = _decode_csv(data)
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = []
    header_idx = -1
    for raw_row in reader:
        if not raw_row or not raw_row[0].strip():
            continue
        if raw_row[0].strip() == "交易时间":
            header_idx = len(rows)
        rows.append([cell.strip() for cell in raw_row])

    if header_idx < 0:
        raise ValueError("支付宝 CSV 未找到表头行（交易时间）")

    normalized: list[NormalizedTransaction] = []
    for raw in rows[header_idx + 1 :]:
        cells = _align_cells(raw)
        normalized.append(_normalize_row(cells))
    return normalized


def _decode_csv(data: bytes) -> str:
    for enc in ("gb18030", "utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("gb18030", errors="replace")


def _align_cells(raw: list[str]) -> list[str]:
    """把多余列（商品说明/备注中的逗号）并入商品说明，不足补空。"""
    cells = list(raw)
    if len(cells) > EXPECTED_COLS:
        extra = cells[EXPECTED_COLS - 1 :]
        cells = cells[: EXPECTED_COLS - 1] + ["，".join(extra)]
    elif len(cells) < EXPECTED_COLS:
        cells += [""] * (EXPECTED_COLS - len(cells))
    return cells


def _normalize_row(cells: list[str]) -> NormalizedTransaction:
    occurred_at = normalize_occurred_at(cells[COL_TIME])
    raw_type = cells[COL_CATEGORY]
    counterparty = cells[COL_COUNTERPARTY]
    item_desc = cells[COL_ITEM]
    direction_raw = cells[COL_DIRECTION]
    amount_cents = amount_to_cents(cells[COL_AMOUNT])
    status_text = cells[COL_STATUS]
    source_txn_id = cells[COL_TXN_ID]
    note = normalize_note(cells[COL_NOTE])

    direction = DIRECTION_MAP.get(direction_raw, "neutral")
    status = _classify_status(status_text)
    normalized_hash = _normalized_hash(
        Platform.ALIPAY.value,
        source_txn_id,
        occurred_at,
        amount_cents,
        direction,
        counterparty,
        item_desc,
    )
    return NormalizedTransaction(
        platform=Platform.ALIPAY.value,
        source_txn_id=source_txn_id,
        occurred_at=occurred_at,
        amount_cents=amount_cents,
        direction=direction,
        status=status,
        status_text=status_text,
        counterparty=counterparty,
        item_desc=item_desc,
        raw_type=raw_type,
        note=note,
        normalized_hash=normalized_hash,
    )
