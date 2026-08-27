import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ..db import connect
from ..decisions.confirm import promote_rule
from ..decisions.reopen import reopen_rule_confirmations
from ..decisions.constants import CATEGORY_TRAVEL
from ..ledger_repo import (
    create_classification_rule,
    list_classification_rules,
    update_rule_status,
)
from ..router_support.settings_access import current_settings
from ..stats import list_category_options
from ..templates_core import templates

router = APIRouter(tags=["Rules"])

STATUS_LABELS = {
    "observing": "观察期",
    "active": "自动入账",
    "disabled": "已停用",
}
TYPE_LABELS = {"consumption": "消费", "income": "收入", "transfer": "调拨"}
FIELD_LABELS = {"counterparty": "商户/对方", "item_desc": "商品说明", "raw_type": "原始交易分类"}
PLATFORM_LABELS = {"": "全部平台", "alipay": "支付宝", "wechat": "微信"}
DIRECTION_RULE_LABELS = {"": "全部方向", "expense": "支出", "income": "收入", "neutral": "不计收支"}


def _pending_count() -> int:
    with connect(current_settings().db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM review_queue WHERE status = 'pending'"
            ).fetchone()["c"]
        )


def _hit_history(db_path, rule_id: int) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_type, detail, created_at
            FROM entry_audit_events
            WHERE ref_rule_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            (rule_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@router.get("/rules", response_class=HTMLResponse)
def rules_list(request: Request, rule_id: int | None = None):
    settings = current_settings()
    rules = list_classification_rules(settings.db_path)
    history = _hit_history(settings.db_path, rule_id) if rule_id else []
    context = {
        "request": request,
        "active_page": "rules",
        "pending_count": _pending_count(),
        "rules": rules,
        "status_labels": STATUS_LABELS,
        "type_labels": TYPE_LABELS,
        "field_labels": FIELD_LABELS,
        "platform_labels": PLATFORM_LABELS,
        "direction_rule_labels": DIRECTION_RULE_LABELS,
        "categories": list_category_options(settings.db_path),
        "selected_rule_id": rule_id,
        "history": history,
        "flash": None,
        "blocked": [],
        "reopened_rule_id": None,
    }
    return templates.TemplateResponse(request, "rules.html", context)


@router.post("/rules", response_class=HTMLResponse)
def rules_create(
    request: Request,
    match_field: str = Form(...),
    match_pattern: str = Form(...),
    platform: str = Form(""),
    direction: str = Form(""),
    target_type: str = Form(...),
    target_category: str = Form(""),
):
    settings = current_settings()
    if match_field not in ("counterparty", "item_desc", "raw_type"):
        return _render(settings, request, "创建失败：无效匹配字段")
    if platform not in PLATFORM_LABELS:
        return _render(settings, request, "创建失败：无效平台条件")
    if direction not in DIRECTION_RULE_LABELS:
        return _render(settings, request, "创建失败：无效方向条件")
    if target_type not in TYPE_LABELS:
        return _render(settings, request, "创建失败：无效目标类型")
    try:
        rule_id = create_classification_rule(
            settings.db_path,
            match_field=match_field,
            match_pattern=match_pattern,
            platform=platform,
            direction=direction,
            target_type=target_type,
            target_category=target_category,
        )
        flash = f"已创建观察期规则 #{rule_id}（先预填待确认，验证后可提升为自动入账）"
    except ValueError as exc:
        flash = f"创建失败：{exc}"
    except sqlite3.IntegrityError:
        flash = "创建失败：规则数据不合法"
    return _render(settings, request, flash)


@router.post("/rules/{rule_id}/promote", response_class=HTMLResponse)
def rules_promote(request: Request, rule_id: int):
    settings = current_settings()
    ok = promote_rule(settings.db_path, rule_id)
    if ok:
        flash = f"规则 #{rule_id} 已提升为自动入账"
    else:
        rule = next(
            (
                r
                for r in list_classification_rules(settings.db_path)
                if int(r["id"]) == rule_id
            ),
            None,
        )
        if rule is not None and rule["target_category"] == CATEGORY_TRAVEL:
            flash = f"规则 #{rule_id} 是旅游类规则，不可自动入账（旅游必须逐笔确认）"
        else:
            flash = f"提升失败：规则 #{rule_id} 不可提升（可能不存在/非观察期/空模式）"
    return _render(settings, request, flash)


@router.post("/rules/{rule_id}/reopen", response_class=HTMLResponse)
def rules_reopen(request: Request, rule_id: int):
    """把该规则批量确认产生的账本记录退回待确认（误操作纠正）。"""
    settings = current_settings()
    try:
        result = reopen_rule_confirmations(settings.db_path, rule_id)
        flash = f"规则 #{rule_id}：已退回 {result.reopened} 笔到待确认"
        if result.blocked_count:
            flash += f"；阻塞保留 {result.blocked_count} 笔"
        blocked = [
            {
                "entry_id": b.entry_id,
                "reason": b.reason,
                "reason_label": (
                    "已关联退款，不能退回" if b.reason == "refund_linked" else "已人工编辑，保留" if b.reason == "manual_edited" else b.reason
                ),
            }
            for b in result.blocked
        ]
        return _render(settings, request, flash, blocked=blocked, reopened_rule_id=rule_id)
    except ValueError as exc:
        return _render(settings, request, f"退回失败：{exc}")


@router.post("/rules/{rule_id}/status/{status}", response_class=HTMLResponse)
def rules_status(request: Request, rule_id: int, status: str):
    settings = current_settings()
    if status not in ("active", "disabled"):
        return _render(settings, request, "无效状态")
    ok = update_rule_status(settings.db_path, rule_id, status)
    flash = f"规则 #{rule_id} 已{'启用' if status == 'active' else '停用'}" if ok else f"操作失败：规则 #{rule_id}"
    return _render(settings, request, flash)


def _render(settings, request, flash: str | None, blocked: list | None = None, reopened_rule_id: int | None = None):
    context = {
        "request": request,
        "active_page": "rules",
        "pending_count": _pending_count(),
        "rules": list_classification_rules(settings.db_path),
        "status_labels": STATUS_LABELS,
        "type_labels": TYPE_LABELS,
        "field_labels": FIELD_LABELS,
        "platform_labels": PLATFORM_LABELS,
        "direction_rule_labels": DIRECTION_RULE_LABELS,
        "categories": list_category_options(settings.db_path),
        "selected_rule_id": None,
        "history": [],
        "flash": flash,
        "blocked": blocked or [],
        "reopened_rule_id": reopened_rule_id,
    }
    return templates.TemplateResponse(request, "rules.html", context)
