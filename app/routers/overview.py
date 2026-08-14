import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..router_support.settings_access import current_settings
from ..stats import overview_stats
from ..templates_core import templates

router = APIRouter(tags=["Overview"])

_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _safe_ym(ym: str | None) -> str | None:
    """非法月份参数回退到默认（本月），不抛 500（红队修复，2026-08-14）。"""
    if ym is None:
        return None
    match = _YM_RE.fullmatch(ym.strip())
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{match.group(2)}"
    return None


@router.get("/", response_class=HTMLResponse)
def index(request: Request, ym: str | None = None):
    stats = overview_stats(current_settings().db_path, _safe_ym(ym))
    context = {
        "request": request,
        "active_page": "overview",
        "pending_count": stats["pending_count"],
        **stats,
    }
    return templates.TemplateResponse(request, "overview.html", context)
