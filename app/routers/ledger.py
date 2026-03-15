import csv
from io import StringIO

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..i18n import TRANSLATIONS, parse_lang
from ..logic import parse_amount_to_cents, validate_direction
from ..repo import (
    create_txn,
    delete_txn,
    get_summary,
    get_txn,
    list_categories,
    list_txns,
    update_txn,
)
from ..router_support.navigation import _import_url, _index_url, _review_url
from ..router_support.request_parsing import _resolve_range, _validate_iso_date
from ..router_support.settings_access import current_settings
from ..templates_core import templates


router = APIRouter(tags=["Ledger"])

DEFAULT_CATEGORY_OPTIONS = [
    "food",
    "transport",
    "rent",
    "utilities",
    "shopping",
]
DEFAULT_NOTE = "无"


def _build_index_context(
    request: Request,
    start: str,
    end: str,
    account_id: int,
    show_archived: bool,
    lang: str,
) -> dict:
    _ = (account_id, show_archived)
    transactions = list_txns(current_settings().db_path, start=start, end=end)
    existing_categories = list_categories(current_settings().db_path)
    category_options: list[str] = []
    seen: set[str] = set()
    for category in [*existing_categories, *DEFAULT_CATEGORY_OPTIONS]:
        normalized = category.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        category_options.append(normalized)
    return {
        "request": request,
        "transactions": transactions,
        "summary": get_summary(current_settings().db_path, start=start, end=end),
        "edit_txn_id": None,
        "start": start,
        "end": end,
        "account_id": 1,
        "active_account_name": TRANSLATIONS[lang]["summary_account"],
        "is_archived_account": False,
        "is_default_account": True,
        "show_archived": False,
        "accounts": [],
        "txn_accounts": [{"id": 1, "name": "Default"}],
        "category_options": category_options,
        "active_page": "ledger",
        "ledger_url": _index_url(start, end, lang),
        "review_url": _review_url(lang),
        "import_url": _import_url(start, end, lang),
        "lang": lang,
        "t": TRANSLATIONS[lang],
    }


def _render_partial(
    request: Request,
    start: str,
    end: str,
    account_id: int,
    show_archived: bool,
    lang: str,
) -> HTMLResponse:
    context = _build_index_context(
        request,
        start,
        end,
        account_id,
        show_archived,
        lang,
    )
    summary_html = templates.get_template("_summary.html").render(**context)
    table_html = templates.get_template("_transactions_table.html").render(**context)
    return HTMLResponse(summary_html + table_html)


def _normalize_transaction_input(
    *,
    date: str,
    direction: str,
    amount: str,
    category: str,
    note: str | None,
) -> dict:
    txn_date = _validate_iso_date(date, field_name="date")
    try:
        valid_direction = validate_direction(direction)
        amount_cents = parse_amount_to_cents(amount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    normalized_note = DEFAULT_NOTE if note is None or not note.strip() else note.strip()
    return {
        "date_str": txn_date,
        "direction": valid_direction,
        "amount_cents": amount_cents,
        "category": category.strip(),
        "note": normalized_note,
    }


def _transaction_template_context(
    request: Request,
    *,
    txn,
    start: str,
    end: str,
    lang: str,
) -> dict:
    context = _build_index_context(
        request,
        start,
        end,
        1,
        False,
        lang,
    )
    context["txn"] = txn
    return context


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
    lang: str | None = None,
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    return templates.TemplateResponse(
        request,
        "index.html",
        _build_index_context(
            request,
            resolved_start,
            resolved_end,
            1,
            False,
            resolved_lang,
        ),
    )


@router.post("/transactions", response_class=HTMLResponse)
def create_transaction(
    request: Request,
    date: str = Form(...),
    direction: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    note: str | None = Form(default=None),
    account_id: int | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    normalized = _normalize_transaction_input(
        date=date,
        direction=direction,
        amount=amount,
        category=category,
        note=note,
    )
    create_txn(
        current_settings().db_path,
        account_id=1,
        **normalized,
    )
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request,
            resolved_start,
            resolved_end,
            1,
            False,
            resolved_lang,
        )
    return RedirectResponse(
        url=_index_url(
            resolved_start,
            resolved_end,
            resolved_lang,
        ),
        status_code=303,
    )


@router.get("/transactions/{txn_id}/row", response_class=HTMLResponse)
def transaction_row(
    txn_id: int,
    request: Request,
    start: str | None = None,
    end: str | None = None,
    lang: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    txn = get_txn(current_settings().db_path, txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    context = _transaction_template_context(
        request,
        txn=txn,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
    )
    return HTMLResponse(
        templates.get_template("_transaction_display_row.html").render(**context)
    )


@router.get("/transactions/{txn_id}/edit", response_class=HTMLResponse)
def edit_transaction_form(
    txn_id: int,
    request: Request,
    start: str | None = None,
    end: str | None = None,
    lang: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    txn = get_txn(current_settings().db_path, txn_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    context = _transaction_template_context(
        request,
        txn=txn,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
    )
    return HTMLResponse(
        templates.get_template("_transaction_edit_row.html").render(**context)
    )


@router.post("/transactions/{txn_id}", response_class=HTMLResponse)
def update_transaction_route(
    txn_id: int,
    request: Request,
    date: str = Form(...),
    direction: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    note: str | None = Form(default=None),
    account_id: int | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    normalized = _normalize_transaction_input(
        date=date,
        direction=direction,
        amount=amount,
        category=category,
        note=note,
    )
    updated = update_txn(
        current_settings().db_path,
        txn_id,
        account_id=1,
        **normalized,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="transaction not found")
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request,
            resolved_start,
            resolved_end,
            1,
            False,
            resolved_lang,
        )
    return RedirectResponse(
        url=_index_url(
            resolved_start,
            resolved_end,
            resolved_lang,
        ),
        status_code=303,
    )


@router.post("/transactions/{txn_id}/delete", response_class=HTMLResponse)
def delete_transaction(
    txn_id: int,
    request: Request,
    account_id: int | None = Form(default=None),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
    lang: str | None = Form(default=None),
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    delete_txn(current_settings().db_path, txn_id)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request,
            resolved_start,
            resolved_end,
            1,
            False,
            resolved_lang,
        )
    return RedirectResponse(
        url=_index_url(
            resolved_start,
            resolved_end,
            resolved_lang,
        ),
        status_code=303,
    )


@router.get("/export.csv")
def export_csv(
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
    lang: str | None = None,
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    parse_lang(lang)
    transactions = list_txns(
        current_settings().db_path,
        start=resolved_start,
        end=resolved_end,
    )

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "account_id", "date", "direction", "amount", "category", "note"]
    )
    for txn in transactions:
        writer.writerow(
            [
                txn["id"],
                txn["account_id"],
                txn["date"],
                txn["direction"],
                f"{txn['amount_cents'] / 100:.2f}",
                txn["category"],
                txn["note"],
            ]
        )

    body = "\ufeff" + output.getvalue()
    filename = f"ledger-{resolved_start}-to-{resolved_end}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )
