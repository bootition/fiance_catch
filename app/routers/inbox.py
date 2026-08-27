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

DIRECTION_LABELS = {
    "expense": "支出",
    "income": "收入",
    "neutral": "不计收支",
}

WITHDRAWAL_PURPOSE_LABELS = {
    WITHDRAWAL_PURPOSE_TRANSFER: "未追踪账户调拨",
    WITHDRAWAL_PURPOSE_INVESTMENT: "投资",
    WITHDRAWAL_PURPOSE_CASH_EXPENSE: "现金消费",
    WITHDRAWAL_PURPOSE_OTHER: "其他",
}


RISK_PER_PAGE = 20


def _high_risk_items(db_path, *, page: int = 1, per_page: int = RISK_PER_PAGE) -> tuple[list[dict], int]:
    """分页查询高风险待办。只对当前页的退款项计算候选（避免整表算候选）。

    返回 (items, total)。
    """
    safe_page = max(1, int(page))
    safe_per_page = max(1, min(int(per_page), 100))
    offset = (safe_page - 1) * safe_per_page
    with connect(db_path) as conn:
        total = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM review_queue AS rq
                WHERE rq.status = 'pending'
                  AND rq.reason IN ('refund_pending','withdrawal','person_transfer','other_neutral')
                """
            ).fetchone()["c"]
        )
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
            LIMIT ? OFFSET ?
            """,
            (safe_per_page, offset),
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            if item["reason"] == "refund_pending":
                item["candidates"] = _refund_candidates(
                    db_path, int(item["source_id"])
                )
        return items, total


def _risk_item(db_path, review_id: int) -> dict | None:
    """单条高风险待办查询（含退款候选），供候选刷新路由使用。"""
    with connect(db_path) as conn:
        row = conn.execute(
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
              AND rq.id = ?
              AND rq.reason IN ('refund_pending','withdrawal','person_transfer','other_neutral')
            """,
            (int(review_id),),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    if item["reason"] == "refund_pending":
        item["candidates"] = _refund_candidates(db_path, int(item["source_id"]))
    return item


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


def _inbox_context(request: Request, flash: str | None, risk_page: int = 1) -> dict:
    settings = current_settings()
    items, risk_total = _high_risk_items(settings.db_path, page=risk_page)
    risk_total_pages = max(1, (risk_total + RISK_PER_PAGE - 1) // RISK_PER_PAGE)
    # 当前页处理完后可能超出总页数，回退到有效页
    if risk_page > risk_total_pages:
        risk_page = risk_total_pages
        items, risk_total = _high_risk_items(settings.db_path, page=risk_page)
    return {
        "request": request,
        "active_page": "inbox",
        "pending_count": _pending_count(),
        "high_risk": items,
        "high_risk_labels": HIGH_RISK_LABELS,
        "risk_page": risk_page,
        "risk_total": risk_total,
        "risk_total_pages": risk_total_pages,
        "groups": group_review_items(settings.db_path),
        "type_labels": TYPE_LABELS,
        "direction_labels": DIRECTION_LABELS,
        "categories": list_categories_used(settings.db_path),
        "withdrawal_purposes": WITHDRAWAL_PURPOSES,
        "withdrawal_purpose_labels": WITHDRAWAL_PURPOSE_LABELS,
        "flash": flash,
    }


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, risk_page: int = 1):
    return templates.TemplateResponse(
        request, "inbox.html", _inbox_context(request, None, risk_page)
    )


@router.get("/inbox/high-risk", response_class=HTMLResponse)
def inbox_high_risk(request: Request, page: int = 1):
    """高风险区翻页局部刷新：仅返回高风险区 section。"""
    return _section_response(request, "_high_risk_section.html", None, page)


def _pending_badge_oob() -> str:
    """导航「待确认」计数的 hx-swap-oob 片段（局部刷新时同步更新）。"""
    n = _pending_count()
    text = f"（{n}）" if n > 0 else ""
    return (
        f'<span id="nav-pending-count" hx-swap-oob="innerHTML">{text}</span>'
    )


def _section_response(request: Request, template_name: str, flash: str | None, risk_page: int = 1) -> HTMLResponse:
    """渲染局部片段（section / 卡片）+ 追加待确认计数 OOB 片段。"""
    context = _inbox_context(request, flash, risk_page)
    body = templates.env.get_template(template_name).render(context)
    return HTMLResponse(body + _pending_badge_oob())


@router.get("/inbox/refund-candidates/{review_id}", response_class=HTMLResponse)
def inbox_refund_candidates(request: Request, review_id: int):
    """单条退款卡片刷新：重新查询 90 天窗口候选；待办已处理则返回空片段（卡片被移除）。"""
    item = _risk_item(current_settings().db_path, review_id)
    if item is None:
        return HTMLResponse("")
    context = {**_inbox_context(request, None), "item": item}
    body = templates.env.get_template("_risk_card.html").render(context)
    return HTMLResponse(body)


@router.post("/inbox/confirm", response_class=HTMLResponse)
async def inbox_confirm(
    request: Request,
    counterparty: str = Form(...),
    platform: str = Form(...),
    direction: str = Form("expense"),
    entry_type: str = Form(...),
    category: str = Form(...),
    risk_page: int = Form(1),
):
    settings = current_settings()
    try:
        result = confirm_group(
            settings.db_path,
            counterparty,
            platform,
            direction=direction,
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
    return _section_response(request, "_category_section.html", flash, risk_page)


@router.post("/inbox/refund/link", response_class=HTMLResponse)
async def inbox_refund_link(
    request: Request,
    refund_source_id: int = Form(...),
    original_ledger_id: int = Form(...),
    review_id: int = Form(...),
    risk_page: int = Form(1),
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
    return _section_response(request, "_high_risk_section.html", flash, risk_page)


@router.post("/inbox/resolve", response_class=HTMLResponse)
async def inbox_resolve(
    request: Request,
    review_id: int = Form(...),
    entry_type: str = Form(""),
    category: str = Form(""),
    purpose: str = Form(""),
    risk_page: int = Form(1),
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
    return _section_response(request, "_high_risk_section.html", flash, risk_page)
