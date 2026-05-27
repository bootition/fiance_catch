from datetime import date as dt_date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..i18n import TRANSLATIONS, current_lang
from ..repo import list_categories, list_txns
from ..theme import PROJECT_DATASET_COLORS
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

REVIEW_PROJECT_MODES = {
    "all",
    "only",
    "exclude",
}


def _parse_review_period(period: str | None) -> str:
    if period in REVIEW_WINDOWS:
        return period
    return "month"


def _parse_review_project_mode(project_mode: str | None) -> str:
    if project_mode in REVIEW_PROJECT_MODES:
        return project_mode
    return "all"


def _parse_review_project(project: str | None) -> str | None:
    if project is None:
        return None
    normalized = project.strip()
    if not normalized:
        return None
    return normalized


def _review_bucket_start(value: dt_date, period: str) -> dt_date:
    if period == "week":
        sunday_offset = (value.weekday() + 1) % 7
        return value - timedelta(days=sunday_offset)
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


def _build_review_data(
    period: str,
    project_mode: str,
    project: str | None,
    t: dict[str, str],
) -> dict:
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
        start=range_start.isoformat(),
        end=range_end.isoformat(),
    )

    filtered_txns = []
    for txn in txns:
        category_value = str(txn["category"]).strip()
        if project_mode == "only":
            if category_value != project:
                continue
        elif project_mode == "exclude":
            if category_value == project:
                continue
        filtered_txns.append(txn)

    current_income: dict[dt_date, int] = {bucket: 0 for bucket in current_buckets}
    current_expense: dict[dt_date, int] = {bucket: 0 for bucket in current_buckets}
    current_categories: dict[str, int] = {}
    project_totals: dict[str, int] = {}
    project_bucket_expense: dict[str, dict[dt_date, int]] = {}

    current_bucket_set = set(current_buckets)

    for txn in filtered_txns:
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
                project_totals[category] = (
                    project_totals.get(category, 0) + amount_cents
                )
                if category not in project_bucket_expense:
                    project_bucket_expense[category] = {
                        bucket_item: 0 for bucket_item in current_buckets
                    }
                project_bucket_expense[category][bucket] += amount_cents

    labels = [_review_bucket_label(bucket, period) for bucket in current_buckets]
    line_datasets: list[dict] = [
        {
            "datasetKey": "income_total",
            "label": t["summary_income"],
            "data": [
                round(current_income[bucket] / 100, 2) for bucket in current_buckets
            ],
            "borderColor": "#1b8f5c",
            "backgroundColor": "rgba(27, 143, 92, 0.12)",
            "tension": 0.25,
            "hidden": False,
        },
        {
            "datasetKey": "expense_total",
            "label": t["summary_expense"],
            "data": [
                round(current_expense[bucket] / 100, 2) for bucket in current_buckets
            ],
            "borderColor": "#b4232c",
            "backgroundColor": "rgba(180, 35, 44, 0.12)",
            "tension": 0.25,
            "hidden": False,
        },
    ]

    sorted_projects = sorted(
        project_totals.items(),
        key=lambda item: (-item[1], item[0]),
    )
    selected_projects = sorted_projects
    for index, (project_name, _) in enumerate(selected_projects):
        line_color, line_bg = PROJECT_DATASET_COLORS[
            index % len(PROJECT_DATASET_COLORS)
        ]
        line_datasets.append(
            {
                "datasetKey": f"project:{project_name}",
                "label": project_name,
                "data": [
                    round(project_bucket_expense[project_name][bucket] / 100, 2)
                    for bucket in current_buckets
                ],
                "borderColor": line_color,
                "backgroundColor": line_bg,
                "tension": 0.25,
                "hidden": False,
            }
        )

    indicator_toggles = [
        {
            "key": "income_total",
            "label": t["summary_income"],
            "default_visible": True,
        },
        {
            "key": "expense_total",
            "label": t["summary_expense"],
            "default_visible": True,
        },
    ]
    for project_name, _ in selected_projects:
        indicator_toggles.append(
            {
                "key": f"project:{project_name}",
                "label": project_name,
                "default_visible": True,
            }
        )

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
            "datasets": line_datasets,
        },
        "line_indicators": indicator_toggles,
        "pie_chart": {
            "labels": pie_labels,
            "values": pie_values,
        },
    }


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    period: str | None = None,
    project_mode: str | None = None,
    project: str | None = None,
):
    resolved_start, resolved_end = _resolve_range(start, end)
    resolved_lang = current_lang()
    resolved_period = _parse_review_period(period)
    resolved_project_mode = _parse_review_project_mode(project_mode)
    resolved_project = _parse_review_project(project)

    project_options = list_categories(current_settings().db_path)
    if resolved_project_mode in {"only", "exclude"} and resolved_project is None:
        resolved_project_mode = "all"
    if resolved_project is not None and resolved_project not in project_options:
        resolved_project_mode = "all"
        resolved_project = None

    review_data = _build_review_data(
        resolved_period,
        resolved_project_mode,
        resolved_project,
        TRANSLATIONS[resolved_lang],
    )
    project_mode_options = [
        {
            "key": "all",
            "label": TRANSLATIONS[resolved_lang]["review_project_mode_all"],
        },
        {
            "key": "only",
            "label": TRANSLATIONS[resolved_lang]["review_project_mode_only"],
        },
        {
            "key": "exclude",
            "label": TRANSLATIONS[resolved_lang]["review_project_mode_exclude"],
        },
    ]

    context = _build_secondary_page_context(
        request,
        start=resolved_start,
        end=resolved_end,
        lang=resolved_lang,
        active_page="review",
        review_period=resolved_period,
        review_project_mode=resolved_project_mode,
        review_project=resolved_project,
    )
    context.update(
        {
            "review_period": resolved_period,
            "review_project_mode": resolved_project_mode,
            "review_project": resolved_project,
            "review_project_mode_options": project_mode_options,
            "review_projects": project_options,
            "review_tabs": [
                {
                    "key": "week",
                    "label": TRANSLATIONS[resolved_lang]["review_period_week"],
                    "url": _review_url(
                        period="week",
                        project_mode=resolved_project_mode,
                        project=resolved_project,
                    ),
                },
                {
                    "key": "month",
                    "label": TRANSLATIONS[resolved_lang]["review_period_month"],
                    "url": _review_url(
                        period="month",
                        project_mode=resolved_project_mode,
                        project=resolved_project,
                    ),
                },
                {
                    "key": "year",
                    "label": TRANSLATIONS[resolved_lang]["review_period_year"],
                    "url": _review_url(
                        period="year",
                        project_mode=resolved_project_mode,
                        project=resolved_project,
                    ),
                },
            ],
            "review_window_start": review_data["window_start"],
            "review_window_end": review_data["window_end"],
            "review_income_total_cents": review_data["income_total_cents"],
            "review_expense_total_cents": review_data["expense_total_cents"],
            "review_net_consumption_cents": review_data["net_consumption_cents"],
            "review_line_title": TRANSLATIONS[resolved_lang]["review_line_title"],
            "review_line_chart_data": review_data["line_chart"],
            "review_line_indicators": review_data["line_indicators"],
            "review_pie_chart_data": review_data["pie_chart"],
            "review_has_pie_data": bool(review_data["pie_chart"]["labels"]),
        }
    )
    return templates.TemplateResponse(request, "review.html", context)
