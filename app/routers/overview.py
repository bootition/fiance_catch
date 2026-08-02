from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..router_support.settings_access import current_settings
from ..stats import overview_stats
from ..templates_core import templates

router = APIRouter(tags=["Overview"])


@router.get("/", response_class=HTMLResponse)
def index(request: Request, ym: str | None = None):
    stats = overview_stats(current_settings().db_path, ym)
    context = {
        "request": request,
        "active_page": "overview",
        "pending_count": stats["pending_count"],
        **stats,
    }
    return templates.TemplateResponse(request, "overview.html", context)
