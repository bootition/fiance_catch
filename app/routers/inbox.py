from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db import connect
from ..decisions.confirm import confirm_group, group_review_items
from ..decisions.constants import TYPE_CONSUMPTION, TYPE_INCOME, TYPE_TRANSFER
from ..decisions.high_risk import (
    WITHDRAWAL_PURPOSES,
    WITHDRAWAL_PURPOSE_CASH_EXPENSE,
    WITHDRAWAL_PURPOSE_INVESTMENT,
    WITHDRAWAL_PURPOSE_OTHER,
    WITHDRAWAL_PURPOSE_TRANSFER,
    resolve_high_risk_review,
)
from ..refunds.linking import link_refund_to_ledger
from ..refunds.matching import find_refund_candidates
from ..router_support.settings_access import current_settings
from ..stats import list_categories_used
from ..templates_core import templates

router = APIRouter(tags=["Inbox"])

HIGH_RISK_LABELS = {
    "refund_pending": "退款待办（需关联原消费）",
    "withdrawal": "提现到银行卡（需逐笔选用途）",
    "person_transfer": "人际转账/红包/收款",
    "other_neutral": "其他不计收支资金流",
}

TYPE_LABELS = {
    TYPE_CONSUMPTION: "消费",
    TYPE_INCOME: "收入",
    TYPE_TRANSFER: "调拨",
}

WITHDRAWAL_PURPOSE_LABELS = {
    WITHDRAWAL_PURPOSE_TRANSFER: "未追踪账户调拨",
    WITHDRAWAL_PURPOSE_INVESTMENT: "投资",
    WITHDRAWAL_PURPOSE_CASH_EXPENSE: "现金消费",
    WITHDRAWAL_PURPOSE_OTHER: "其他",
}


def _high_risk_items(db_path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              rq.id AS review_id,
              rq.reason,
              rq.priority,
              st.id AS source_id,
              st.platform,
              st.source_txn_id,
              st.occurred_at,
              st.amount_cents,
              st.counterparty,
              st.item_desc,
              st.direction
            FROM review_queue AS rq
            JOIN source_transactions AS st ON st.id = rq.source_transaction_id
            WHERE rq.status = 'pending'
              AND rq.reason IN ('refund_pending','withdrawal','person_transfer','other_neutral')
            ORDER BY rq.priority DESC, st.occurred_at DESC
            """
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            if item["reason"] == "refund_pending":
                item["candidates"] = _refund_candidates(
                    db_path, int(item["source_id"])
                )
        return items


def _refund_candidates(db_path, source_id: int) -> list[dict]:
    try:
        return [
            {
                "ledger_id": c.ledger_id,
                "amount_cents": c.amount_cents,
                "txn_date": c.txn_date,
                "counterparty": c.counterparty,
                "item_desc": c.item_desc,
                "already_refunded_cents": c.already_refunded_cents,
                "match_reason": c.match_reason,
            }
            for c in find_refund_candidates(db_path, source_id)
        ]
    except ValueError:
        return []


def _pending_count() -> int:
    with connect(current_settings().db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM review_queue WHERE status = 'pending'"
            ).fetchone()["c"]
        )


def _inbox_context(request: Request, flash: str | None) -> dict:
    settings = current_settings()
    return {
        "request": request,
        "active_page": "inbox",
        "pending_count": _pending_count(),
        "high_risk": _high_risk_items(settings.db_path),
        "high_risk_labels": HIGH_RISK_LABELS,
        "groups": group_review_items(settings.db_path),
        "type_labels": TYPE_LABELS,
        "categories": list_categories_used(settings.db_path),
        "withdrawal_purposes": WITHDRAWAL_PURPOSES,
        "withdrawal_purpose_labels": WITHDRAWAL_PURPOSE_LABELS,
        "flash": flash,
    }


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request):
    return templates.TemplateResponse(
        request, "inbox.html", _inbox_context(request, None)
    )


@router.post("/inbox/confirm", response_class=HTMLResponse)
async def inbox_confirm(
    request: Request,
    counterparty: str = Form(...),
    platform: str = Form(...),
    entry_type: str = Form(...),
    category: str = Form(...),
):
    settings = current_settings()
    try:
        result = confirm_group(
            settings.db_path,
            counterparty,
            platform,
            entry_type=entry_type,
            category=category,
        )
        flash = (
            f"已确认 {result.confirmed} 项（{counterparty}）"
            + (
                f"，并建议创建观察期规则 #{result.rule_id}"
                if result.rule_id is not None
                else ""
            )
        )
    except ValueError as exc:
        flash = f"批量确认失败：{exc}"
    return templates.TemplateResponse(
        request, "inbox.html", _inbox_context(request, flash)
    )


@router.post("/inbox/refund/link", response_class=HTMLResponse)
async def inbox_refund_link(
    request: Request,
    refund_source_id: int = Form(...),
    original_ledger_id: int = Form(...),
    review_id: int = Form(...),
):
    settings = current_settings()
    try:
        result = link_refund_to_ledger(
            settings.db_path, refund_source_id, original_ledger_id
        )
        flash = (
            f"已关联退款 #{result.refund_link_id}：原消费 #{result.original_ledger_id} "
            f"净成本 {result.net_cost_cents / 100:.2f} 元"
        )
    except ValueError as exc:
        flash = f"退款关联失败：{exc}"
    return templates.TemplateResponse(
        request, "inbox.html", _inbox_context(request, flash)
    )


@router.post("/inbox/resolve", response_class=HTMLResponse)
async def inbox_resolve(
    request: Request,
    review_id: int = Form(...),
    entry_type: str = Form(""),
    category: str = Form(""),
    purpose: str = Form(""),
):
    settings = current_settings()
    try:
        result = resolve_high_risk_review(
            settings.db_path,
            review_id,
            entry_type=entry_type,
            category=category,
            purpose=purpose,
        )
        flash = f"已定性 #{result.entry_id}（{HIGH_RISK_LABELS.get(result.reason, result.reason)}）"
    except ValueError as exc:
        flash = f"处理失败：{exc}"
    return templates.TemplateResponse(
        request, "inbox.html", _inbox_context(request, flash)
    )
