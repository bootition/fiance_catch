from fastapi import Request
from urllib.parse import urlencode

from ..i18n import TRANSLATIONS


def _index_url(start: str, end: str) -> str:
    return f"/?start={start}&end={end}"


def _review_url(
    period: str | None = None,
    project_mode: str | None = None,
    project: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if period is not None:
        params["period"] = period
    if project_mode is not None:
        params["project_mode"] = project_mode
    if project is not None and project.strip():
        params["project"] = project.strip()
    if not params:
        return "/review"
    return "/review?" + urlencode(params)


def _cleanup_url(
    start: str,
    end: str,
    *,
    deleted: int | None = None,
    preview_token: str | None = None,
) -> str:
    base = f"/cleanup?start={start}&end={end}"
    if deleted is not None:
        base = f"{base}&deleted={deleted}"
    if preview_token is not None:
        base = f"{base}&preview_token={preview_token}"
    return base


def _build_secondary_page_context(
    request: Request,
    *,
    start: str,
    end: str,
    lang: str,
    active_page: str,
    review_period: str | None = None,
    review_project_mode: str | None = None,
    review_project: str | None = None,
) -> dict:
    return {
        "request": request,
        "start": start,
        "end": end,
        "lang": lang,
        "t": TRANSLATIONS[lang],
        "active_page": active_page,
        "ledger_url": _index_url(start, end),
        "review_url": _review_url(
            period=review_period,
            project_mode=review_project_mode,
            project=review_project,
        ),
        "cleanup_url": _cleanup_url(start, end),
    }
