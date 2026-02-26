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
    create_account,
    create_txn,
    delete_txn,
    get_account,
    get_summary,
    list_accounts,
    list_txns,
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


def _resolve_account_id(account_id: int | None) -> int:
    candidate = account_id or 1
    if candidate < 1:
        return 1
    account = get_account(settings.db_path, candidate)
    if account is None:
        return 1
    return candidate


def _build_index_context(
    request: Request, start: str, end: str, account_id: int
) -> dict:
    transactions = list_txns(
        settings.db_path, account_id=account_id, start=start, end=end
    )
    accounts = list_accounts(settings.db_path)
    account = get_account(settings.db_path, account_id)
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
        "accounts": accounts,
    }


def _render_partial(
    request: Request, start: str, end: str, account_id: int
) -> HTMLResponse:
    context = _build_index_context(request, start, end, account_id)
    summary_html = templates.get_template("_summary.html").render(**context)
    table_html = templates.get_template("_transactions_table.html").render(**context)
    return HTMLResponse(summary_html + table_html)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_account_id = _resolve_account_id(account_id)
    return templates.TemplateResponse(
        "index.html",
        _build_index_context(
            request, resolved_start, resolved_end, resolved_account_id
        ),
    )


@app.post("/accounts")
def create_account_route(
    name: str = Form(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
):
    try:
        new_account_id = create_account(settings.db_path, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_start, resolved_end = _resolve_range(start, end)
    return RedirectResponse(
        url=(
            f"/?account_id={new_account_id}&start={resolved_start}&end={resolved_end}"
        ),
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
):
    try:
        valid_direction = validate_direction(direction)
        amount_cents = parse_amount_to_cents(amount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_account_id = _resolve_account_id(account_id)

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
            request, resolved_start, resolved_end, resolved_account_id
        )
    return RedirectResponse(
        url=(
            f"/?account_id={resolved_account_id}&start={resolved_start}&end={resolved_end}"
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
):
    resolved_account_id = _resolve_account_id(account_id)
    delete_txn(settings.db_path, txn_id, account_id=resolved_account_id)
    resolved_start, resolved_end = _resolve_range(start, end)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(
            request, resolved_start, resolved_end, resolved_account_id
        )
    return RedirectResponse(
        url=(
            f"/?account_id={resolved_account_id}&start={resolved_start}&end={resolved_end}"
        ),
        status_code=303,
    )


@app.get("/export.csv")
def export_csv(
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_account_id = _resolve_account_id(account_id)
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
