from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..i18n import parse_lang
from ..repo import delete_bulk_by_filters, preview_bulk_delete
from ..router_support.bulk_delete_shared import (
    _build_bulk_delete_filters,
    _is_empty_bulk_delete_filters,
)
from ..router_support.importing_shared import (
    _drop_bulk_delete_token,
    _get_bulk_delete_token_payload,
    _issue_bulk_delete_token,
)
from ..router_support.navigation import _import_url
from ..router_support.request_parsing import _resolve_range
from ..router_support.settings_access import current_settings


router = APIRouter(tags=["Bulk Delete"])


@router.post("/transactions/bulk-delete/preview")
def preview_bulk_delete_route(
    request: Request,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    direction: str | None = Form(default=None),
    category: str | None = Form(default=None),
    note_contains: str | None = Form(default=None),
    imported_only: str | None = Form(default=None),
    allow_delete_all: str | None = Form(default=None),
    lang: str | None = Form(default=None),
    page_start: str | None = Form(default=None),
    page_end: str | None = Form(default=None),
    response_mode: str | None = Form(default=None),
    batch_ids: list[str] = Form(default=[]),
):
    _ = request
    resolved_lang = parse_lang(lang)
    resolved_page_start, resolved_page_end = _resolve_range(page_start, page_end)

    filters = _build_bulk_delete_filters(
        start=start,
        end=end,
        direction=direction,
        category=category,
        note_contains=note_contains,
        imported_only=imported_only,
        batch_ids=batch_ids,
    )

    allow_delete_all_value = allow_delete_all == "1"
    if _is_empty_bulk_delete_filters(filters) and not allow_delete_all_value:
        raise HTTPException(
            status_code=400,
            detail="empty delete conditions not allowed",
        )

    preview = preview_bulk_delete(current_settings().db_path, filters, sample_limit=20)
    delete_token = _issue_bulk_delete_token(
        filters=filters,
        matched_count=int(preview["matched_count"]),
        sample_rows=list(preview["sample_rows"]),
        allow_delete_all=allow_delete_all_value,
    )

    if response_mode == "redirect":
        return RedirectResponse(
            url=_import_url(
                resolved_page_start,
                resolved_page_end,
                resolved_lang,
                preview_token=delete_token,
            ),
            status_code=303,
        )

    return {
        "matched_count": int(preview["matched_count"]),
        "sample_rows": list(preview["sample_rows"]),
        "delete_token": delete_token,
    }


@router.post("/transactions/bulk-delete/execute")
def execute_bulk_delete_route(
    delete_token: str = Form(...),
    confirm_text: str = Form(...),
    expected_count: int = Form(...),
    allow_delete_all: str | None = Form(default=None),
    lang: str | None = Form(default=None),
    page_start: str | None = Form(default=None),
    page_end: str | None = Form(default=None),
):
    resolved_lang = parse_lang(lang)
    resolved_page_start, resolved_page_end = _resolve_range(page_start, page_end)

    payload = _get_bulk_delete_token_payload(delete_token.strip())
    if payload is None:
        raise HTTPException(status_code=400, detail="invalid delete_token")

    filters = dict(payload.get("filters", {}))
    is_delete_all = _is_empty_bulk_delete_filters(filters)

    normalized_confirm = confirm_text.strip()
    if is_delete_all:
        if allow_delete_all != "1" or normalized_confirm != "DELETE ALL":
            raise HTTPException(
                status_code=400,
                detail="confirm_text must be DELETE ALL",
            )
    else:
        if normalized_confirm != "DELETE":
            raise HTTPException(status_code=400, detail="confirm_text must be DELETE")

    latest_preview = preview_bulk_delete(
        current_settings().db_path, filters, sample_limit=0
    )
    current_matched_count = int(latest_preview["matched_count"])
    if current_matched_count != int(expected_count):
        raise HTTPException(status_code=409, detail="matched count changed")

    deleted_count = delete_bulk_by_filters(current_settings().db_path, filters)
    _drop_bulk_delete_token(delete_token.strip())

    return RedirectResponse(
        url=_import_url(
            resolved_page_start,
            resolved_page_end,
            resolved_lang,
            deleted=deleted_count,
        ),
        status_code=303,
    )
