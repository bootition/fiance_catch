from datetime import date as dt_date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..i18n import TRANSLATIONS, parse_lang
from ..repo import list_txns
from ..router_support.navigation import _build_secondary_page_context, _review_url
from ..router_support.request_parsing import _resolve_range
from ..router_support.settings_access import current_settings
from ..templates_core import templates


router = APIRouter(tags=["Review"])

REVIEW_WINDOWS = {
    "week": 12,
    "month": 12,
    "year": 5,
}


def _parse_review_period(period: str | None) -> str:
    if period in REVIEW_WINDOWS:
        return period
    return "month"


def _review_bucket_start(value: dt_date, period: str) -> dt_date:
    if period == "week":
        return value - timedelta(days=value.weekday())
    if period == "month":
        return dt_date(value.year, value.month, 1)
    return dt_date(value.year, 1, 1)


def _review_bucket_add(bucket_start: dt_date, period: str, step: int) -> dt_date:
    if period == "week":
        return bucket_start + timedelta(days=7 * step)
    if period == "month":
        month_index = (bucket_start.month - 1) + step
        year = bucket_start.year + (month_index // 12)
        month = (month_index % 12) + 1
        return dt_date(year, month, 1)
    return dt_date(bucket_start.year + step, 1, 1)


def _review_bucket_label(bucket_start: dt_date, period: str) -> str:
    if period == "week":
        return bucket_start.strftime("%m-%d")
    if period == "month":
        return bucket_start.strftime("%Y-%m")
    return bucket_start.strftime("%Y")


def _build_review_data(account_id: int, period: str, t: dict[str, str]) -> dict:
    window_size = REVIEW_WINDOWS[period]
    current_bucket = _review_bucket_start(dt_date.today(), period)
    current_buckets = [
        _review_bucket_add(current_bucket, period, -(window_size - 1 - index))
        for index in range(window_size)
    ]

    range_start = current_buckets[0]
    range_end = _review_bucket_add(current_bucket, period, 1) - timedelta(days=1)
    txns = list_txns(
        current_settings().db_path,
        account_id=account_id,
        start=range_start.isoformat(),
        end=range_end.isoformat(),
    )

    current_income: dict[dt_date, int] = {bucket: 0 for bucket in current_buckets}
    current_expense: dict[dt_date, int] = {bucket: 0 for bucket in current_buckets}
    current_categories: dict[str, int] = {}

    current_bucket_set = set(current_buckets)

    for txn in txns:
        txn_date = dt_date.fromisoformat(str(txn["date"]))
        bucket = _review_bucket_start(txn_date, period)
        amount_cents = int(txn["amount_cents"])
        direction = str(txn["direction"])

        if bucket in current_bucket_set:
            if direction == "income":
                current_income[bucket] += amount_cents
            elif direction == "expense":
                current_expense[bucket] += amount_cents
                category = str(txn["category"]).strip() or "uncategorized"
                current_categories[category] = (
                    current_categories.get(category, 0) + amount_cents
                )

    labels = [_review_bucket_label(bucket, period) for bucket in current_buckets]
    current_income_series = [
        round(current_income[bucket] / 100, 2) for bucket in current_buckets
    ]
    current_expense_series = [
        round(current_expense[bucket] / 100, 2) for bucket in current_buckets
    ]

    sorted_categories = sorted(
        current_categories.items(),
        key=lambda item: (-item[1], item[0]),
    )
    top_categories = sorted_categories[:8]
    other_total = sum(item[1] for item in sorted_categories[8:])
    if other_total > 0:
        top_categories.append((t["review_pie_other"], other_total))

    pie_labels = [item[0] for item in top_categories]
    pie_values = [round(item[1] / 100, 2) for item in top_categories]

    income_total_cents = sum(current_income.values())
    expense_total_cents = sum(current_expense.values())
    net_consumption_cents = expense_total_cents - income_total_cents

    return {
        "window_start": current_buckets[0].isoformat(),
        "window_end": range_end.isoformat(),
        "income_total_cents": income_total_cents,
        "expense_total_cents": expense_total_cents,
        "net_consumption_cents": net_consumption_cents,
        "line_chart": {
            "labels": labels,
            "datasets": [
                {
                    "label": t["review_line_current_income"],
                    "data": current_income_series,
                    "borderColor": "#1f7a3f",
                    "backgroundColor": "rgba(31, 122, 63, 0.12)",
                    "tension": 0.25,
                },
                {
                    "label": t["review_line_current_expense"],
                    "data": current_expense_series,
                    "borderColor": "#b4232c",
                    "backgroundColor": "rgba(180, 35, 44, 0.12)",
                    "tension": 0.25,
                },
            ],
        },
        "pie_chart": {
            "labels": pie_labels,
            "values": pie_values,
        },
    }


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    account_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    show_archived: str | None = None,
    lang: str | None = None,
    period: str | None = None,
):
    _ = account_id
    _ = show_archived
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = parse_lang(lang)
    resolved_period = _parse_review_period(period)
    review_data = _build_review_data(1, resolved_period, TRANSLATIONS[resolved_lang])
    context = _build_secondary_page_context(
        request,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
        active_page="review",
        review_period=resolved_period,
    )
    context.update(
        {
            "review_period": resolved_period,
            "review_tabs": [
                {
                    "key": "week",
                    "label": TRANSLATIONS[resolved_lang]["review_period_week"],
                    "url": _review_url(resolved_lang, period="week"),
                },
                {
                    "key": "month",
                    "label": TRANSLATIONS[resolved_lang]["review_period_month"],
                    "url": _review_url(resolved_lang, period="month"),
                },
                {
                    "key": "year",
                    "label": TRANSLATIONS[resolved_lang]["review_period_year"],
                    "url": _review_url(resolved_lang, period="year"),
                },
            ],
            "review_window_start": review_data["window_start"],
            "review_window_end": review_data["window_end"],
            "review_income_total_cents": review_data["income_total_cents"],
            "review_expense_total_cents": review_data["expense_total_cents"],
            "review_net_consumption_cents": review_data["net_consumption_cents"],
            "review_line_chart_data": review_data["line_chart"],
            "review_pie_chart_data": review_data["pie_chart"],
            "review_has_pie_data": bool(review_data["pie_chart"]["labels"]),
        }
    )
    return templates.TemplateResponse(request, "review.html", context)
