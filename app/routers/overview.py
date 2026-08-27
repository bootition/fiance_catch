import re
from datetime import date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..router_support.settings_access import current_settings
from ..stats import overview_stats
from ..templates_core import templates

router = APIRouter(tags=["Overview"])

_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")
_VALID_PERIODS = {"week", "month", "year"}


def _safe_ym(ym: str | None) -> str | None:
    """非法月份参数回退到默认（本月），不抛 500（红队修复，2026-08-14）。"""
    if ym is None:
        return None
    match = _YM_RE.fullmatch(ym.strip())
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{match.group(2)}"
    return None


def _safe_period(period: str | None) -> str:
    return period if period in _VALID_PERIODS else "month"


def _safe_anchor(anchor: str | None) -> date | None:
    if anchor is None:
        return None
    text = str(anchor).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    ym: str | None = None,
    period: str | None = None,
    anchor: str | None = None,
):
    period = _safe_period(period)
    anchor_date = _safe_anchor(anchor) or date.today()
    # 兼容旧链接 /?ym=YYYY-MM：固定切到月视图
    safe_ym = _safe_ym(ym)
    if safe_ym:
        period = "month"
        anchor_date = datetime.strptime(f"{safe_ym}-01", "%Y-%m-%d").date()
    elif ym is not None:
        # 非法 ym 回退默认月视图（历史测试行为）
        period = "month"

    stats = overview_stats(
        current_settings().db_path,
        safe_ym,
        period=period,
        anchor=anchor_date.isoformat(),
    )
    context = {
        "request": request,
        "active_page": "overview",
        "pending_count": stats["pending_count"],
        **stats,
    }
    return templates.TemplateResponse(request, "overview.html", context)
