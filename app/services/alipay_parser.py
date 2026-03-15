import csv
import re
from datetime import date as dt_date
from io import StringIO

from fastapi import HTTPException

from ..logic import parse_amount_to_cents

ALIPAY_HEADER_ALIASES = {
    "date": ["交易创建时间", "交易时间", "创建时间", "时间"],
    "direction": ["收/支", "收支类型", "交易类型", "类型"],
    "amount": ["金额（元）", "金额", "交易金额"],
    "category": ["交易分类", "分类", "商品分类"],
    "note": ["商品说明", "交易对方", "备注", "商品名称"],
    "status": ["交易状态", "状态"],
    "trade_no": ["交易号", "交易单号", "支付宝交易号", "交易订单号"],
}
_ALIPAY_DATE_FLEX_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(value: str, *, field_name: str) -> str:
    if not _ISO_DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD")
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be YYYY-MM-DD"
        ) from exc
    return value


def decode_import_file(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="unsupported file encoding")


def _first_value(row: dict[str, str], aliases: list[str]) -> str:
    for key in aliases:
        value = row.get(key)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _is_alipay_header(columns: list[str]) -> bool:
    values = {col.strip().lstrip("\ufeff") for col in columns if col.strip()}
    required_groups = (
        ALIPAY_HEADER_ALIASES["date"],
        ALIPAY_HEADER_ALIASES["direction"],
        ALIPAY_HEADER_ALIASES["amount"],
    )
    for aliases in required_groups:
        if not any(alias in values for alias in aliases):
            return False
    return True


def _find_alipay_header(csv_text: str) -> tuple[int, str]:
    lines = csv_text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        for delimiter in (",", "\t"):
            columns = [col.strip() for col in line.split(delimiter)]
            if _is_alipay_header(columns):
                return index, delimiter
    raise HTTPException(status_code=400, detail="csv header is required")


def _parse_alipay_date(raw_value: str) -> str:
    normalized = raw_value.strip()
    if not normalized:
        raise ValueError("invalid date")
    match = _ALIPAY_DATE_FLEX_RE.search(normalized)
    if match is None:
        raise ValueError("invalid date")
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    return _validate_iso_date(f"{year:04d}-{month:02d}-{day:02d}", field_name="date")


def _classify_alipay_status(raw_value: str) -> str:
    text = raw_value.strip()
    if not text:
        return "importable"

    importable_keywords = ("成功", "已收入", "已收款", "已还款")
    if any(keyword in text for keyword in importable_keywords):
        return "importable"

    skip_keywords = (
        "关闭",
        "失败",
        "撤销",
        "退款关闭",
        "等待",
        "处理中",
        "未完成",
        "作废",
    )
    if any(keyword in text for keyword in skip_keywords):
        return "skipped_status"

    return "importable"


def _is_alipay_non_transaction_row(row: dict[str, str]) -> bool:
    non_empty_values = [
        str(value).strip()
        for value in row.values()
        if value is not None and str(value).strip()
    ]
    if not non_empty_values:
        return True

    date_value = _first_value(row, ALIPAY_HEADER_ALIASES["date"])
    direction_value = _first_value(row, ALIPAY_HEADER_ALIASES["direction"])
    amount_value = _first_value(row, ALIPAY_HEADER_ALIASES["amount"])
    status_value = _first_value(row, ALIPAY_HEADER_ALIASES["status"])
    trade_no_value = _first_value(row, ALIPAY_HEADER_ALIASES["trade_no"])

    if (
        not date_value
        and not direction_value
        and not amount_value
        and not status_value
        and not trade_no_value
    ):
        return True

    if not direction_value and not amount_value and len(non_empty_values) <= 1:
        first_cell = non_empty_values[0]
        if _ALIPAY_DATE_FLEX_RE.search(first_cell) is None:
            return True

    return False


def _parse_alipay_direction(raw_value: str) -> str:
    text = raw_value.strip()
    if "不计收支" in text:
        return "neutral"
    if "支出" in text:
        return "expense"
    if "收入" in text:
        return "income"
    raise ValueError("unsupported direction")


def _parse_alipay_amount(raw_value: str) -> int:
    normalized = raw_value.strip().replace(",", "").replace("￥", "").replace("¥", "")
    if normalized.startswith("+"):
        normalized = normalized[1:]
    if normalized.startswith("-"):
        normalized = normalized[1:]
    return parse_amount_to_cents(normalized)


def _apply_category_rules(
    raw_category: str,
    rules: list[dict[str, str]],
) -> str:
    normalized_category = raw_category.strip() or "misc"
    category_lower = normalized_category.lower()
    for rule in rules:
        pattern = str(rule.get("match_pattern", "")).strip().lower()
        target_category = str(rule.get("target_category", "")).strip()
        if not pattern or not target_category:
            continue
        if pattern in category_lower:
            return target_category
    return normalized_category


def parse_alipay_preview_rows(
    csv_text: str,
    *,
    include_neutral: bool,
    category_rules: list[dict[str, str]],
) -> list[dict[str, str | int | None]]:
    header_index, delimiter = _find_alipay_header(csv_text)
    lines = csv_text.splitlines()
    body_text = "\n".join(lines[header_index:])

    reader = csv.DictReader(StringIO(body_text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="csv header is required")
    reader.fieldnames = [
        (field or "").strip().lstrip("\ufeff") for field in reader.fieldnames
    ]

    parsed_rows: list[dict[str, str | int | None]] = []
    row_no = 0
    for row in reader:
        row_no += 1
        if _is_alipay_non_transaction_row(row):
            continue

        status = _first_value(row, ALIPAY_HEADER_ALIASES["status"])
        status_class = _classify_alipay_status(status)
        if status_class == "skipped_status":
            raw_category = (
                _first_value(row, ALIPAY_HEADER_ALIASES["category"]) or "misc"
            )
            parsed_rows.append(
                {
                    "row_no": row_no,
                    "date": None,
                    "direction": None,
                    "amount_cents": None,
                    "category": raw_category,
                    "raw_category": raw_category,
                    "note": _first_value(row, ALIPAY_HEADER_ALIASES["note"]) or "无",
                    "status_text": status,
                    "parse_status": "skipped_status",
                    "parse_error": "non-final status",
                    "source_txn_id": _first_value(
                        row, ALIPAY_HEADER_ALIASES["trade_no"]
                    )
                    or None,
                    "tag": "",
                    "selected": 0,
                    "deleted": 0,
                }
            )
            continue

        try:
            date_str = _parse_alipay_date(
                _first_value(row, ALIPAY_HEADER_ALIASES["date"])
            )
            direction = _parse_alipay_direction(
                _first_value(row, ALIPAY_HEADER_ALIASES["direction"])
            )
            amount_cents = _parse_alipay_amount(
                _first_value(row, ALIPAY_HEADER_ALIASES["amount"])
            )
        except (HTTPException, ValueError):
            raw_category = (
                _first_value(row, ALIPAY_HEADER_ALIASES["category"]) or "misc"
            )
            parsed_rows.append(
                {
                    "row_no": row_no,
                    "date": None,
                    "direction": None,
                    "amount_cents": None,
                    "category": raw_category,
                    "raw_category": raw_category,
                    "note": _first_value(row, ALIPAY_HEADER_ALIASES["note"]) or "无",
                    "status_text": status,
                    "parse_status": "invalid",
                    "parse_error": "invalid row",
                    "source_txn_id": _first_value(
                        row, ALIPAY_HEADER_ALIASES["trade_no"]
                    )
                    or None,
                    "tag": "",
                    "selected": 0,
                    "deleted": 0,
                }
            )
            continue

        if direction == "neutral" and not include_neutral:
            raw_category = (
                _first_value(row, ALIPAY_HEADER_ALIASES["category"]) or "misc"
            )
            parsed_rows.append(
                {
                    "row_no": row_no,
                    "date": date_str,
                    "direction": direction,
                    "amount_cents": amount_cents,
                    "category": raw_category,
                    "raw_category": raw_category,
                    "note": _first_value(row, ALIPAY_HEADER_ALIASES["note"]) or "无",
                    "status_text": status,
                    "parse_status": "skipped_status",
                    "parse_error": "non-cashflow skipped",
                    "source_txn_id": _first_value(
                        row, ALIPAY_HEADER_ALIASES["trade_no"]
                    )
                    or None,
                    "tag": "",
                    "selected": 0,
                    "deleted": 0,
                }
            )
            continue

        raw_category = _first_value(row, ALIPAY_HEADER_ALIASES["category"]) or "misc"
        category = _apply_category_rules(raw_category, category_rules)
        note = _first_value(row, ALIPAY_HEADER_ALIASES["note"]) or "无"
        trade_no = _first_value(row, ALIPAY_HEADER_ALIASES["trade_no"]) or None
        parsed_rows.append(
            {
                "row_no": row_no,
                "date": date_str,
                "direction": direction,
                "amount_cents": amount_cents,
                "category": category,
                "raw_category": raw_category,
                "note": note,
                "status_text": status,
                "parse_status": "valid",
                "parse_error": "",
                "source_txn_id": trade_no,
                "tag": "",
                "selected": 1,
                "deleted": 0,
            }
        )

    return parsed_rows


def parse_alipay_rows(
    csv_text: str,
    *,
    include_neutral: bool,
) -> tuple[list[dict[str, str | int | None]], int, int, int]:
    preview_rows = parse_alipay_preview_rows(
        csv_text,
        include_neutral=include_neutral,
        category_rules=[],
    )

    parsed_rows: list[dict[str, str | int | None]] = []
    invalid_rows = 0
    skipped_status_rows = 0
    skipped_non_cashflow_rows = 0
    for row in preview_rows:
        parse_status = str(row["parse_status"])
        if parse_status == "valid":
            parsed_rows.append(
                {
                    "date_str": row["date"],
                    "direction": row["direction"],
                    "amount_cents": row["amount_cents"],
                    "category": row["category"],
                    "note": row["note"],
                    "source_txn_id": row["source_txn_id"],
                }
            )
            continue

        if parse_status == "invalid":
            invalid_rows += 1
            continue

        if parse_status == "skipped_status":
            if str(row.get("parse_error") or "") == "non-cashflow skipped":
                skipped_non_cashflow_rows += 1
            else:
                skipped_status_rows += 1

    return parsed_rows, invalid_rows, skipped_status_rows, skipped_non_cashflow_rows
