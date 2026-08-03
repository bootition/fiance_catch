import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ..db import connect
from ..decisions.confirm import promote_rule
from ..ledger_repo import (
    create_classification_rule,
    list_classification_rules,
    update_rule_status,
)
from ..router_support.settings_access import current_settings
from ..stats import list_categories_used
from ..templates_core import templates

router = APIRouter(tags=["Rules"])

STATUS_LABELS = {
    "observing": "观察期",
    "active": "自动入账",
    "disabled": "已停用",
}
TYPE_LABELS = {"consumption": "消费", "income": "收入", "transfer": "调拨"}
FIELD_LABELS = {"counterparty": "商户/对方", "item_desc": "商品说明"}


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
        "categories": list_categories_used(settings.db_path),
        "selected_rule_id": rule_id,
        "history": history,
        "flash": None,
    }
    return templates.TemplateResponse(request, "rules.html", context)


@router.post("/rules", response_class=HTMLResponse)
def rules_create(
    request: Request,
    match_field: str = Form(...),
    match_pattern: str = Form(...),
    target_type: str = Form(...),
    target_category: str = Form(""),
):
    settings = current_settings()
    if match_field not in ("counterparty", "item_desc"):
        return _render(settings, request, "创建失败：无效匹配字段")
    if target_type not in TYPE_LABELS:
        return _render(settings, request, "创建失败：无效目标类型")
    try:
        rule_id = create_classification_rule(
            settings.db_path,
            match_field=match_field,
            match_pattern=match_pattern,
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
    flash = f"规则 #{rule_id} 已提升为自动入账" if ok else f"提升失败：规则 #{rule_id} 不可提升（可能不存在/非观察期/空模式）"
    return _render(settings, request, flash)


@router.post("/rules/{rule_id}/status/{status}", response_class=HTMLResponse)
def rules_status(request: Request, rule_id: int, status: str):
    settings = current_settings()
    if status not in ("active", "disabled"):
        return _render(settings, request, "无效状态")
    ok = update_rule_status(settings.db_path, rule_id, status)
    flash = f"规则 #{rule_id} 已{'启用' if status == 'active' else '停用'}" if ok else f"操作失败：规则 #{rule_id}"
    return _render(settings, request, flash)


def _render(settings, request, flash: str | None):
    context = {
        "request": request,
        "active_page": "rules",
        "pending_count": _pending_count(),
        "rules": list_classification_rules(settings.db_path),
        "status_labels": STATUS_LABELS,
        "type_labels": TYPE_LABELS,
        "field_labels": FIELD_LABELS,
        "categories": list_categories_used(settings.db_path),
        "selected_rule_id": None,
        "history": [],
        "flash": flash,
    }
    return templates.TemplateResponse(request, "rules.html", context)
