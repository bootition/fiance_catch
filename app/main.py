from datetime import date as dt_date, timedelta
import csv
from io import StringIO

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import init_db
from .logic import parse_amount_to_cents, validate_direction
from .repo import create_txn, delete_txn, get_summary, list_txns
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


def _build_index_context(request: Request, start: str, end: str) -> dict:
    transactions = list_txns(settings.db_path, start=start, end=end)
    return {
        "request": request,
        "transactions": transactions,
        "summary": get_summary(settings.db_path, start=start, end=end),
        "start": start,
        "end": end,
    }


def _render_partial(request: Request, start: str, end: str) -> HTMLResponse:
    context = _build_index_context(request, start, end)
    summary_html = templates.get_template("_summary.html").render(**context)
    table_html = templates.get_template("_transactions_table.html").render(**context)
    return HTMLResponse(summary_html + table_html)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, start: str | None = None, end: str | None = None):
    resolved_start, resolved_end = _resolve_range(start, end)
    return templates.TemplateResponse(
        "index.html",
        _build_index_context(request, resolved_start, resolved_end),
    )


@app.post("/transactions", response_class=HTMLResponse)
def create_transaction(
    request: Request,
    date: str = Form(...),
    direction: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    note: str = Form(...),
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
):
    try:
        valid_direction = validate_direction(direction)
        amount_cents = parse_amount_to_cents(amount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    create_txn(
        settings.db_path,
        date_str=date,
        direction=valid_direction,
        amount_cents=amount_cents,
        category=category.strip(),
        note=note.strip(),
    )
    resolved_start, resolved_end = _resolve_range(start, end)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(request, resolved_start, resolved_end)
    return RedirectResponse(
        url=f"/?start={resolved_start}&end={resolved_end}",
        status_code=303,
    )


@app.post("/transactions/{txn_id}/delete", response_class=HTMLResponse)
def delete_transaction(
    txn_id: int,
    request: Request,
    start: str | None = Form(default=None),
    end: str | None = Form(default=None),
):
    delete_txn(settings.db_path, txn_id)
    resolved_start, resolved_end = _resolve_range(start, end)
    if request.headers.get("HX-Request") == "true":
        return _render_partial(request, resolved_start, resolved_end)
    return RedirectResponse(
        url=f"/?start={resolved_start}&end={resolved_end}",
        status_code=303,
    )


@app.get("/export.csv")
def export_csv(start: str | None = None, end: str | None = None):
    resolved_start, resolved_end = _resolve_range(start, end)
    transactions = list_txns(settings.db_path, start=resolved_start, end=resolved_end)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "date", "direction", "amount", "category", "note"])
    for txn in transactions:
        writer.writerow(
            [
                txn["id"],
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
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
