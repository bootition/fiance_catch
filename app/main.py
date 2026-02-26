from datetime import date as dt_date, timedelta
import csv
from io import StringIO

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import init_db
from .logic import parse_amount_to_cents, validate_direction
from .repo import (
    archive_account,
    create_account,
    create_txn,
    delete_account,
    delete_txn,
    get_account,
    get_summary,
    list_accounts,
    list_txns,
    rename_account,
    restore_account,
)
from .settings import get_settings


settings = get_settings()
init_db(settings)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _current_month_range(today: dt_date | None = None) -> tuple[str, str]:
    current = today or dt_date.today()
    month_start = dt_date(current.year, current.month, 1)
    if current.month == 12:
        next_month_start = dt_date(current.year + 1, 1, 1)
    else:
        next_month_start = dt_date(current.year, current.month + 1, 1)
    month_end = next_month_start - timedelta(days=1)
    return month_start.isoformat(), month_end.isoformat()


def _resolve_range(start: str | None, end: str | None) -> tuple[str, str]:
    default_start, default_end = _current_month_range()
    return start or default_start, end or default_end


def _parse_show_archived(show_archived: str | None) -> bool:
    if show_archived is None:
        return False
    return show_archived == "1"


def _index_url(account_id: int, start: str, end: str, show_archived: bool) -> str:
    base = f"/?account_id={account_id}&start={start}&end={end}"
    if show_archived:
        return f"{base}&show_archived=1"
    return base


def _resolve_account_id(account_id: int | None, *, show_archived: bool) -> int:
    candidate = account_id or 1
    if candidate < 1:
        return 1
    account = get_account(settings.db_path, candidate)
    if account is None:
        return 1
    if not show_archived and int(account["archived"]) == 1:
        return 1
    return candidate


def _build_index_context(
    request: Request,
    start: str,
    end: str,
    account_id: int,
    show_archived: bool,
) -> dict:
    transactions = list_txns(
        settings.db_path, account_id=account_id, start=start, end=end
    )
    accounts = list_accounts(settings.db_path, include_archived=show_archived)
    account = get_account(settings.db_path, account_id)
    is_archived_account = bool(account and int(account["archived"]) == 1)
    return {
        "request": request,
        "transactions": transactions,
        "summary": get_summary(
            settings.db_path, account_id=account_id, start=start, end=end
        ),
        "start": start,
        "end": end,
        "account_id": account_id,
        "active_account_name": account["name"] if account else "Default",
        "is_archived_account": is_archived_account,
        "is_default_account": account_id == 1,
        "show_archived": show_archived,
        "accounts": accounts,
    }


def _render_partial(
    request: Request,
    start: str,
    end: str,
    account_id: int,
    show_archived: bool,
) -> HTMLResponse:
    context = _build_index_context(request, start, end, account_id, show_archived)
    summary_html = templates.get_template("_summary.html").render(**context)
    table_html = templates.get_template("_transactions_table.html").render(**context)
    return HTMLResponse(summary_html + table_html)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    resolved_account_id = _resolve_account_id(
        account_id,
        show_archived=show_archived_value,
    )
    return templates.TemplateResponse(
        "index.html",
        _build_index_context(
            request,
            resolved_start,
            resolved_end,
            resolved_account_id,
            show_archived_value,
        ),
    )


@app.post("/accounts")
def create_account_route(
    name: str = Form(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    try:
        new_account_id = create_account(settings.db_path, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    return RedirectResponse(
        url=_index_url(
            new_account_id,
            resolved_start,
            resolved_end,
            show_archived_value,
        ),
        status_code=303,
    )


@app.post("/accounts/{account_id}/rename")
def rename_account_route(
    account_id: int,
    name: str = Form(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    if account_id < 1 or get_account(settings.db_path, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        rename_account(settings.db_path, account_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    return RedirectResponse(
        url=_index_url(
            account_id,
            resolved_start,
            resolved_end,
            show_archived_value,
        ),
        status_code=303,
    )


@app.post("/accounts/{account_id}/archive")
def archive_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    if account_id < 1 or get_account(settings.db_path, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        archive_account(settings.db_path, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    return RedirectResponse(
        url=_index_url(1, resolved_start, resolved_end, show_archived_value),
        status_code=303,
    )


@app.post("/accounts/{account_id}/restore")
def restore_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    if account_id < 1 or get_account(settings.db_path, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        restore_account(settings.db_path, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    return RedirectResponse(
        url=_index_url(
            account_id,
            resolved_start,
            resolved_end,
            show_archived_value,
        ),
        status_code=303,
    )


@app.post("/accounts/{account_id}/delete")
def delete_account_route(
    account_id: int,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    if account_id < 1 or get_account(settings.db_path, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        delete_account(settings.db_path, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    return RedirectResponse(
        url=_index_url(1, resolved_start, resolved_end, show_archived_value),
        status_code=303,
    )


@app.post("/transactions", response_class=HTMLResponse)
def create_transaction(
    request: Request,
    date: str = Form(...),
    direction: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    note: str = Form(...),
    account_id: int = Form(default=1),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    try:
        valid_direction = validate_direction(direction)
        amount_cents = parse_amount_to_cents(amount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    show_archived_value = _parse_show_archived(show_archived)
    resolved_account_id = _resolve_account_id(
        account_id,
        show_archived=True,
    )
    selected_account = get_account(settings.db_path, resolved_account_id)
    if selected_account is None:
        raise HTTPException(status_code=404, detail="account not found")
    if int(selected_account["archived"]) == 1:
        raise HTTPException(status_code=400, detail="archived account is read-only")

    create_txn(
        settings.db_path,
        account_id=resolved_account_id,
        date_str=date,
        direction=valid_direction,
        amount_cents=amount_cents,
        category=category.strip(),
        note=note.strip(),
    )
    resolved_start, resolved_end = _resolve_range(start, end)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request,
            resolved_start,
            resolved_end,
            resolved_account_id,
            show_archived_value,
        )
    return RedirectResponse(
        url=_index_url(
            resolved_account_id,
            resolved_start,
            resolved_end,
            show_archived_value,
        ),
        status_code=303,
    )


@app.post("/transactions/{txn_id}/delete", response_class=HTMLResponse)
def delete_transaction(
    txn_id: int,
    request: Request,
    account_id: int = Form(default=1),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
    show_archived: str | None = Form(default=None),
):
    show_archived_value = _parse_show_archived(show_archived)
    resolved_account_id = _resolve_account_id(
        account_id,
        show_archived=True,
    )
    selected_account = get_account(settings.db_path, resolved_account_id)
    if selected_account is None:
        raise HTTPException(status_code=404, detail="account not found")
    if int(selected_account["archived"]) == 1:
        raise HTTPException(status_code=400, detail="archived account is read-only")
    delete_txn(settings.db_path, txn_id, account_id=resolved_account_id)
    resolved_start, resolved_end = _resolve_range(start, end)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request,
            resolved_start,
            resolved_end,
            resolved_account_id,
            show_archived_value,
        )
    return RedirectResponse(
        url=_index_url(
            resolved_account_id,
            resolved_start,
            resolved_end,
            show_archived_value,
        ),
        status_code=303,
    )


@app.get("/export.csv")
def export_csv(
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    show_archived_value = _parse_show_archived(show_archived)
    resolved_account_id = _resolve_account_id(
        account_id,
        show_archived=show_archived_value,
    )
    transactions = list_txns(
        settings.db_path,
        account_id=resolved_account_id,
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
    filename = (
        f"ledger-account-{resolved_account_id}-{resolved_start}-to-{resolved_end}.csv"
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
