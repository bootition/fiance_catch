from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..i18n import TRANSLATIONS, parse_lang
from ..repo import (
    bulk_delete_selected_import_rows,
    bulk_set_category_for_selected_rows,
    bulk_set_tag_for_selected_rows,
    commit_import_session,
    create_category_rule,
    create_category_rules_from_selected_rows,
    create_import_session,
    create_import_txn,
    delete_bulk_by_batch_ids,
    discard_import_session,
    get_import_preview_counts,
    get_import_session,
    insert_import_rows,
    list_category_rules,
    list_import_batches,
    list_import_rows,
    preview_bulk_delete,
    update_import_row,
)
from ..router_support.bulk_delete_shared import _is_empty_bulk_delete_filters
from ..router_support.importing_shared import (
    _get_bulk_delete_token_payload,
    _is_valid_import_batch_id,
    _is_valid_import_session_id,
    _issue_bulk_delete_token,
    _parse_include_neutral,
    _parse_status_label,
)
from ..router_support.navigation import (
    _build_secondary_page_context,
    _import_preview_url,
    _import_url,
)
from ..router_support.request_parsing import _resolve_range
from ..router_support.settings_access import current_settings
from ..services.alipay_parser import (
    decode_import_file,
    parse_alipay_preview_rows,
    parse_alipay_rows,
)
from ..templates_core import templates


router = APIRouter(tags=["Importing"])


@router.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request,
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
    lang: str | None = None,
    imported: int | None = None,
    skipped_status: int | None = None,
    skipped_non_cashflow: int | None = None,
    skipped: int | None = None,
    invalid: int | None = None,
    batch_id: str | None = None,
    deleted: int | None = None,
    preview_token: str | None = None,
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    imported_count = 0 if imported is None else max(imported, 0)
    if skipped_status is None and skipped is not None:
        skipped_status = skipped
    skipped_status_count = 0 if skipped_status is None else max(skipped_status, 0)
    skipped_non_cashflow_count = (
        0 if skipped_non_cashflow is None else max(skipped_non_cashflow, 0)
    )
    invalid_count = 0 if invalid is None else max(invalid, 0)
    deleted_count = 0 if deleted is None else max(deleted, 0)
    resolved_batch_id = None
    if batch_id is not None and batch_id.strip():
        resolved_batch_id = batch_id.strip()
    has_import_result = (
        imported is not None
        or skipped_status is not None
        or skipped_non_cashflow is not None
        or skipped is not None
        or invalid is not None
    )
    has_delete_result = deleted is not None

    preview_payload = None
    if preview_token is not None and preview_token.strip():
        preview_payload = _get_bulk_delete_token_payload(preview_token.strip())

    undo_delete_token = None
    undo_expected_count = 0
    if (
        resolved_batch_id is not None
        and imported_count > 0
        and _is_valid_import_batch_id(resolved_batch_id)
    ):
        undo_preview = preview_bulk_delete(
            current_settings().db_path,
            {"batch_ids": [resolved_batch_id]},
            sample_limit=0,
        )
        undo_expected_count = int(undo_preview["matched_count"])
        if undo_expected_count > 0:
            undo_delete_token = _issue_bulk_delete_token(
                filters={"batch_ids": [resolved_batch_id]},
                matched_count=undo_expected_count,
                sample_rows=[],
                allow_delete_all=False,
            )

    bulk_preview_rows: list[dict] = []
    bulk_preview_matched_count = 0
    bulk_preview_token = None
    bulk_preview_requires_delete_all = False
    if preview_payload is not None:
        bulk_preview_rows = list(preview_payload.get("sample_rows", []))[:20]
        bulk_preview_matched_count = int(preview_payload.get("matched_count", 0))
        bulk_preview_token = (
            preview_token.strip() if preview_token is not None else None
        )
        bulk_preview_requires_delete_all = bool(
            preview_payload.get("allow_delete_all", False)
            and _is_empty_bulk_delete_filters(preview_payload.get("filters", {}))
        )

    import_batches = list_import_batches(current_settings().db_path, limit=200)

    context = _build_secondary_page_context(
        request,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
        active_page="import",
    )
    context.update(
        {
            "has_import_result": has_import_result,
            "imported_count": imported_count,
            "skipped_status_count": skipped_status_count,
            "skipped_non_cashflow_count": skipped_non_cashflow_count,
            "invalid_count": invalid_count,
            "batch_id": resolved_batch_id,
            "has_delete_result": has_delete_result,
            "deleted_count": deleted_count,
            "import_batches": import_batches,
            "has_bulk_preview_result": preview_payload is not None,
            "bulk_preview_rows": bulk_preview_rows,
            "bulk_preview_matched_count": bulk_preview_matched_count,
            "bulk_preview_token": bulk_preview_token,
            "bulk_preview_requires_delete_all": bulk_preview_requires_delete_all,
            "undo_delete_token": undo_delete_token,
            "undo_expected_count": undo_expected_count,
        }
    )
    return templates.TemplateResponse(request, "import.html", context)


@router.post("/import/alipay/preview")
async def import_alipay_preview_route(
    file: UploadFile = File(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
    include_neutral: str | None = Form(default="1"),
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    include_neutral_value = _parse_include_neutral(include_neutral)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="empty import file")

    csv_text = decode_import_file(raw_bytes)
    category_rules = list_category_rules(current_settings().db_path, enabled_only=True)
    parsed_rows = parse_alipay_preview_rows(
        csv_text,
        include_neutral=include_neutral_value,
        category_rules=category_rules,
    )

    session_id = create_import_session(
        current_settings().db_path,
        source_name=file.filename or "alipay.csv",
        lang=resolved_lang,
        include_neutral=include_neutral_value,
    )
    insert_import_rows(current_settings().db_path, session_id, parsed_rows)

    return RedirectResponse(
        url=_import_preview_url(
            session_id,
            start=resolved_start,
            end=resolved_end,
            lang=resolved_lang,
        ),
        status_code=303,
    )


@router.get("/import/preview/{session_id}", response_class=HTMLResponse)
def import_preview_page(
    request: Request,
    session_id: str,
    start: str | None = None,
    end: str | None = None,
    lang: str | None = None,
):
    if not _is_valid_import_session_id(session_id.strip()):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    session = get_import_session(current_settings().db_path, session_id.strip())
    if session is None:
        raise HTTPException(status_code=404, detail="import session not found")

    rows = list_import_rows(current_settings().db_path, session_id.strip())
    counts = get_import_preview_counts(current_settings().db_path, session_id.strip())

    context = _build_secondary_page_context(
        request,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
        active_page="import",
    )
    context.update(
        {
            "import_session_id": session_id.strip(),
            "import_session_status": str(session["status"]),
            "preview_rows": [
                {
                    **row,
                    "parse_status_label": _parse_status_label(
                        str(row["parse_status"]),
                        TRANSLATIONS[resolved_lang],
                    ),
                }
                for row in rows
            ],
            "preview_valid_count": counts["valid_count"],
            "preview_skipped_status_count": counts["skipped_status_count"],
            "preview_invalid_count": counts["invalid_count"],
            "preview_deleted_count": counts["deleted_count"],
            "preview_selected_count": counts["selected_count"],
            "preview_rules": list_category_rules(
                current_settings().db_path, enabled_only=True
            ),
            "preview_url": _import_preview_url(
                session_id.strip(),
                start=resolved_start,
                end=resolved_end,
                lang=resolved_lang,
            ),
        }
    )
    return templates.TemplateResponse(request, "import_preview.html", context)


@router.post("/import/preview/{session_id}/row/{row_id}")
def update_import_preview_row_route(
    session_id: str,
    row_id: int,
    category: str | None = Form(default=None),
    note: str | None = Form(default=None),
    selected: str | None = Form(default=None),
    action: str | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    normalized_session_id = session_id.strip()
    if not _is_valid_import_session_id(normalized_session_id):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    session = get_import_session(current_settings().db_path, normalized_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="import session not found")
    if str(session["status"]) != "active":
        raise HTTPException(status_code=400, detail="import session not active")

    existing_rows = list_import_rows(current_settings().db_path, normalized_session_id)
    existing_row = None
    for row in existing_rows:
        if int(row["id"]) == row_id:
            existing_row = row
            break
    if existing_row is None:
        raise HTTPException(status_code=404, detail="import row not found")

    normalized_category = (category or "").strip() or str(
        existing_row.get("category") or "misc"
    )
    normalized_note = (note or "").strip() or str(existing_row.get("note") or "")
    should_delete = action == "delete"

    updated = update_import_row(
        current_settings().db_path,
        session_id=normalized_session_id,
        row_id=row_id,
        category=normalized_category,
        note=normalized_note,
        selected=(selected == "1") and not should_delete,
        deleted=should_delete,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="failed to update import row")

    return RedirectResponse(
        url=_import_preview_url(
            normalized_session_id,
            start=resolved_start,
            end=resolved_end,
            lang=resolved_lang,
        ),
        status_code=303,
    )


@router.post("/import/preview/{session_id}/bulk-update")
def bulk_update_import_preview_rows_route(
    session_id: str,
    action: str = Form(...),
    target_category: str | None = Form(default=None),
    tag: str | None = Form(default=None),
    rule_pattern: str | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    normalized_session_id = session_id.strip()
    if not _is_valid_import_session_id(normalized_session_id):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    session = get_import_session(current_settings().db_path, normalized_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="import session not found")
    if str(session["status"]) != "active":
        raise HTTPException(status_code=400, detail="import session not active")

    if action == "set_category":
        normalized_target = (target_category or "").strip()
        if not normalized_target:
            raise HTTPException(status_code=400, detail="target_category required")
        bulk_set_category_for_selected_rows(
            current_settings().db_path,
            session_id=normalized_session_id,
            target_category=normalized_target,
        )
    elif action == "add_tag":
        normalized_tag = (tag or "").strip()
        if not normalized_tag:
            raise HTTPException(status_code=400, detail="tag required")
        bulk_set_tag_for_selected_rows(
            current_settings().db_path,
            session_id=normalized_session_id,
            tag=normalized_tag,
        )
    elif action == "create_rule":
        normalized_target = (target_category or "").strip()
        if not normalized_target:
            raise HTTPException(status_code=400, detail="target_category required")
        normalized_pattern = (rule_pattern or "").strip()
        if normalized_pattern:
            try:
                create_category_rule(
                    current_settings().db_path,
                    match_pattern=normalized_pattern,
                    target_category=normalized_target,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            try:
                create_category_rules_from_selected_rows(
                    current_settings().db_path,
                    session_id=normalized_session_id,
                    target_category=normalized_target,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        bulk_set_category_for_selected_rows(
            current_settings().db_path,
            session_id=normalized_session_id,
            target_category=normalized_target,
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported bulk action")

    return RedirectResponse(
        url=_import_preview_url(
            normalized_session_id,
            start=resolved_start,
            end=resolved_end,
            lang=resolved_lang,
        ),
        status_code=303,
    )


@router.post("/import/preview/{session_id}/bulk-delete")
def bulk_delete_import_preview_rows_route(
    session_id: str,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    normalized_session_id = session_id.strip()
    if not _is_valid_import_session_id(normalized_session_id):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    session = get_import_session(current_settings().db_path, normalized_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="import session not found")
    if str(session["status"]) != "active":
        raise HTTPException(status_code=400, detail="import session not active")

    bulk_delete_selected_import_rows(
        current_settings().db_path,
        session_id=normalized_session_id,
    )

    return RedirectResponse(
        url=_import_preview_url(
            normalized_session_id,
            start=resolved_start,
            end=resolved_end,
            lang=resolved_lang,
        ),
        status_code=303,
    )


@router.post("/import/preview/{session_id}/commit")
def commit_import_preview_session_route(
    session_id: str,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    normalized_session_id = session_id.strip()
    if not _is_valid_import_session_id(normalized_session_id):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    try:
        result = commit_import_session(
            current_settings().db_path,
            session_id=normalized_session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(
        url=_import_url(
            resolved_start,
            resolved_end,
            resolved_lang,
            imported=int(result["imported_count"]),
            skipped_status=int(result["skipped_count"]),
            skipped_non_cashflow=0,
            invalid=int(result["invalid_count"]),
            batch_id=(
                None
                if result["import_batch_id"] is None
                else str(result["import_batch_id"])
            ),
            deleted=int(result["deleted_count"]),
        ),
        status_code=303,
    )


@router.post("/import/preview/{session_id}/discard")
def discard_import_preview_session_route(
    session_id: str,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    normalized_session_id = session_id.strip()
    if not _is_valid_import_session_id(normalized_session_id):
        raise HTTPException(status_code=400, detail="invalid import_session_id")

    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    try:
        discard_import_session(
            current_settings().db_path, session_id=normalized_session_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(
        url=_import_url(resolved_start, resolved_end, resolved_lang),
        status_code=303,
    )


@router.post("/import/alipay")
async def import_alipay_route(
    file: UploadFile = File(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
    include_neutral: str | None = Form(default="1"),
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    include_neutral_value = _parse_include_neutral(include_neutral)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="empty import file")

    csv_text = decode_import_file(raw_bytes)
    (
        parsed_rows,
        invalid_rows,
        skipped_status_rows,
        skipped_non_cashflow_rows,
    ) = parse_alipay_rows(csv_text, include_neutral=include_neutral_value)
    batch_id = uuid4().hex

    inserted = 0
    for item in parsed_rows:
        create_import_txn(
            current_settings().db_path,
            date_str=str(item["date_str"]),
            direction=str(item["direction"]),
            amount_cents=int(item["amount_cents"]),
            category=str(item["category"]),
            note=str(item["note"]),
            source_txn_id=(
                None if item["source_txn_id"] is None else str(item["source_txn_id"])
            ),
            import_batch_id=batch_id,
        )
        inserted += 1

    return RedirectResponse(
        url=_import_url(
            resolved_start,
            resolved_end,
            resolved_lang,
            imported=inserted,
            skipped_status=skipped_status_rows,
            skipped_non_cashflow=skipped_non_cashflow_rows,
            invalid=invalid_rows,
            batch_id=batch_id,
        ),
        status_code=303,
    )


@router.post("/import/batches/{batch_id}/delete")
def delete_import_batch_route(
    batch_id: str,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)

    normalized_batch_id = batch_id.strip()
    if not _is_valid_import_batch_id(normalized_batch_id):
        raise HTTPException(status_code=400, detail="invalid batch_id")

    deleted_count = delete_bulk_by_batch_ids(
        current_settings().db_path, [normalized_batch_id]
    )

    return RedirectResponse(
        url=_import_url(
            resolved_start,
            resolved_end,
            resolved_lang,
            deleted=deleted_count,
        ),
        status_code=303,
    )
