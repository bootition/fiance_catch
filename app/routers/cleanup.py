from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..i18n import current_lang
from ..repo import list_import_batches
from ..router_support.bulk_delete_shared import _unwrap_bulk_preview_token
from ..router_support.navigation import _build_secondary_page_context
from ..router_support.request_parsing import _resolve_range
from ..router_support.settings_access import current_settings
from ..templates_core import templates


router = APIRouter(tags=["Cleanup"])


@router.get("/cleanup", response_class=HTMLResponse)
def cleanup_page(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    deleted: int | None = None,
    preview_token: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = current_lang()
    deleted_count = 0 if deleted is None else max(deleted, 0)

    preview_ctx = _unwrap_bulk_preview_token(preview_token)

    context = _build_secondary_page_context(
        request,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
        active_page="cleanup",
    )
    context.update(
        {
            "has_delete_result": deleted is not None,
            "deleted_count": deleted_count,
            "import_batches": list_import_batches(
                current_settings().db_path, limit=200
            ),
            "has_bulk_preview_result": preview_ctx["token"] is not None,
            "bulk_preview_rows": preview_ctx["rows"],
            "bulk_preview_matched_count": preview_ctx["matched_count"],
            "bulk_preview_token": preview_ctx["token"],
            "bulk_preview_requires_delete_all": preview_ctx["requires_delete_all"],
        }
    )
    return templates.TemplateResponse(request, "cleanup.html", context)
