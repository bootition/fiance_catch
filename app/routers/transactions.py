from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import connect
from ..decisions.reopen import reopen_ledger_entry
from ..ledger_repo import (
    create_ledger_entry,
    delete_ledger_entry,
    update_ledger_entry,
)
from ..router_support.settings_access import current_settings
from ..stats import list_category_options, list_entries_filtered, list_source_statuses
from ..templates_core import templates
from ..validation import validate_iso_date

router = APIRouter(tags=["Transactions"])

VALID_TYPES = ("consumption", "income", "transfer", "refund")


def _pending_count() -> int:
    with connect(current_settings().db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM review_queue WHERE status = 'pending'"
            ).fetchone()["c"]
        )


def _parse_optional_int(value: str | None) -> int | None:
    """空字符串/None 安全返回 None；非法值抛 ValueError（FastAPI 422 的替代）。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise ValueError("batch_id 必须是整数") from exc


def _parse_optional_bool(value: str | None) -> bool:
    """空字符串视为 False；接受 true/1/yes/on。"""
    if value is None or str(value).strip() == "":
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _default_range() -> tuple[str, str]:
    today = date.today()
    start = f"{today.year}-{today.month:02d}-01"
    return start, f"{today.year}-{today.month:02d}-{today.day:02d}"


def _month_range(txn_date: str) -> tuple[str, str]:
    year, month = txn_date.split("-")[:2]
    start = f"{year}-{month}-01"
    if month == "12":
        end = f"{int(year) + 1}-01-01"
    else:
        end = f"{year}-{int(month) + 1:02d}-01"
    return start, end


def _batch_options(db_path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, file_name FROM import_batches ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return [dict(row) for row in rows]


@router.get("/transactions", response_class=HTMLResponse)
def transactions_list(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    entry_type: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    batch_id: str | None = None,
    manual_only: str | None = None,
    source_status: str | None = None,
    q: str | None = None,
    flash: str | None = None,
):
    settings = current_settings()
    default_start, default_end = _default_range()
    start = start or default_start
    end = end or default_end
    try:
        parsed_batch_id = _parse_optional_int(batch_id)
        rows = list_entries_filtered(
            settings.db_path,
            start=start,
            end=end + " 23:59:59" if len(end) == 10 else end,
            entry_type=entry_type or None,
            category=category or None,
            platform=platform or None,
            batch_id=parsed_batch_id,
            manual_only=_parse_optional_bool(manual_only),
            source_status=source_status or None,
            q=q or None,
        )
    except ValueError as exc:
        parsed_batch_id = None
        flash = str(exc)
        rows = list_entries_filtered(
            settings.db_path,
            start=start,
            end=end + " 23:59:59" if len(end) == 10 else end,
            entry_type=entry_type or None,
            category=category or None,
            platform=platform or None,
            batch_id=None,
            manual_only=False,
            source_status=source_status or None,
            q=q or None,
        )
    parsed_manual_only = _parse_optional_bool(manual_only)
    context = {
        "request": request,
        "active_page": "transactions",
        "pending_count": _pending_count(),
        "entries": rows,
        "start": start,
        "end": end,
        "filters": {
            "entry_type": entry_type or "",
            "category": category or "",
            "platform": platform or "",
            "batch_id": parsed_batch_id,
            "manual_only": parsed_manual_only,
            "source_status": source_status or "",
            "q": q or "",
        },
        "categories": list_category_options(settings.db_path),
        "source_statuses": list_source_statuses(settings.db_path),
        "batches": _batch_options(settings.db_path),
        "type_labels": {"consumption": "消费", "income": "收入", "transfer": "调拨", "refund": "退款"},
        "flash": flash,
    }
    return templates.TemplateResponse(request, "transactions.html", context)


@router.post("/transactions", response_class=HTMLResponse)
def transactions_create(
    request: Request,
    entry_type: str = Form(...),
    amount: str = Form(...),
    category: str = Form(""),
    txn_date: str = Form(...),
    note: str = Form(""),
):
    settings = current_settings()
    # 写前校验（P1 红队修复）：非法日期绝不落库
    txn_date = validate_iso_date(txn_date, field_name="txn_date")
    try:
        from ..logic import parse_amount_to_cents

        amount_cents = parse_amount_to_cents(amount)
        if entry_type not in VALID_TYPES:
            raise ValueError("无效交易类型")
        entry_id = create_ledger_entry(
            settings.db_path,
            entry_type=entry_type,
            amount_cents=amount_cents,
            category=category,
            txn_date=txn_date,
            note=note,
        )
        flash = f"已补记 #{entry_id}"
    except ValueError as exc:
        flash = f"补记失败：{exc}"
    start, end = _month_range(txn_date)
    rows = list_entries_filtered(
        settings.db_path,
        start=start,
        end=end,
    )
    context = {
        "request": request,
        "active_page": "transactions",
        "pending_count": _pending_count(),
        "entries": rows,
        "start": start,
        "end": end,
        "filters": {"entry_type": "", "category": "", "platform": "", "batch_id": None, "manual_only": False, "source_status": "", "q": ""},
        "categories": list_category_options(settings.db_path),
        "source_statuses": list_source_statuses(settings.db_path),
        "batches": _batch_options(settings.db_path),
        "type_labels": {"consumption": "消费", "income": "收入", "transfer": "调拨", "refund": "退款"},
        "flash": flash,
    }
    return templates.TemplateResponse(request, "transactions.html", context)


@router.get("/transactions/{entry_id}", response_class=HTMLResponse)
def transactions_detail(request: Request, entry_id: int):
    """流水详情：来源流水、批次归属、规则命中/退款/人工改动审计、退款关联明细。"""
    settings = current_settings()
    with connect(settings.db_path) as conn:
        entry = conn.execute(
            "SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None:
            return RedirectResponse("/transactions?flash=记录不存在", status_code=303)
        entry = dict(entry)
        source = None
        if entry["source_transaction_id"] is not None:
            source = conn.execute(
                "SELECT * FROM source_transactions WHERE id = ?",
                (entry["source_transaction_id"],),
            ).fetchone()
            source = dict(source) if source is not None else None
        batch = None
        if entry["batch_id"] is not None:
            batch = conn.execute(
                "SELECT * FROM import_batches WHERE id = ?", (entry["batch_id"],)
            ).fetchone()
            batch = dict(batch) if batch is not None else None
        links = [
            dict(r)
            for r in conn.execute(
                """
                SELECT rl.*, st.occurred_at AS refund_at,
                       st.counterparty AS refund_counterparty,
                       st.item_desc AS refund_item_desc
                FROM refund_links AS rl
                LEFT JOIN source_transactions AS st ON st.id = rl.refund_source_id
                WHERE rl.original_ledger_id = ?
                ORDER BY rl.linked_at DESC, rl.id DESC
                """,
                (entry_id,),
            ).fetchall()
        ]
        audit = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM entry_audit_events
                WHERE ref_ledger_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (entry_id,),
            ).fetchall()
        ]
        refunded_cents = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(refund_amount_cents), 0) AS c
                FROM refund_links WHERE original_ledger_id = ?
                """,
                (entry_id,),
            ).fetchone()["c"]
        )
    context = {
        "request": request,
        "active_page": "transactions",
        "pending_count": _pending_count(),
        "entry": entry,
        "entry_type_labels": {
            "consumption": "消费",
            "income": "收入",
            "transfer": "调拨",
            "refund": "退款",
        },
        "audit_type_labels": {
            "manual_edit": "人工修改",
            "bulk_confirm": "批量确认",
            "rule_applied": "规则自动入账",
            "refund_linked": "退款关联",
            "batch_revoked": "批次撤销",
            "high_risk_resolved": "高风险定性",
            "bulk_reopen": "退回待确认",
        },
        "source": source,
        "batch": batch,
        "links": links,
        "audit": audit,
        "refunded_cents": refunded_cents,
        "net_cost_cents": int(entry["amount_cents"]) - refunded_cents,
    }
    return templates.TemplateResponse(request, "entry_detail.html", context)


@router.post("/transactions/{entry_id}/edit", response_class=HTMLResponse)
def transactions_edit(
    request: Request,
    entry_id: int,
    entry_type: str = Form(...),
    amount: str = Form(...),
    category: str = Form(""),
    txn_date: str = Form(...),
    note: str = Form(""),
):
    settings = current_settings()
    txn_date = validate_iso_date(txn_date, field_name="txn_date")
    try:
        from ..logic import parse_amount_to_cents

        amount_cents = parse_amount_to_cents(amount)
        if entry_type not in VALID_TYPES:
            raise ValueError("无效交易类型")
        update_ledger_entry(
            settings.db_path,
            entry_id,
            entry_type=entry_type,
            amount_cents=amount_cents,
            category=category,
            txn_date=txn_date,
            note=note,
        )
        flash = f"已更新 #{entry_id}（人工改动已标记）"
    except ValueError as exc:
        flash = f"更新失败：{exc}"
    return RedirectResponse(f"/transactions/{entry_id}?flash={quote(flash)}", status_code=303)


@router.post("/transactions/{entry_id}/reopen", response_class=HTMLResponse)
def transactions_reopen(request: Request, entry_id: int):
    """把一笔账本记录退回待确认（误操作纠正；退款关联/人工编辑会阻塞）。"""
    settings = current_settings()
    try:
        result = reopen_ledger_entry(settings.db_path, entry_id)
        flash = f"已将 #{entry_id} 退回待确认，可重新分类"
        if result.blocked_count:
            flash += f"；阻塞 {result.blocked_count} 项（已退款关联/已人工编辑）"
    except ValueError as exc:
        flash = f"退回失败：{exc}"
    return RedirectResponse(f"/transactions?flash={quote(flash)}", status_code=303)


@router.post("/transactions/{entry_id}/delete", response_class=HTMLResponse)
def transactions_delete(request: Request, entry_id: int):
    settings = current_settings()
    try:
        deleted = delete_ledger_entry(settings.db_path, entry_id)
        flash = f"已删除 #{entry_id}" if deleted else f"删除失败：#{entry_id} 不存在"
    except ValueError as exc:
        flash = f"删除失败：{exc}"
    return RedirectResponse(f"/transactions?flash={quote(flash)}", status_code=303)
