from fastapi import Request

from ..i18n import TRANSLATIONS


def _index_url(start: str, end: str, lang: str) -> str:
    return f"/?start={start}&end={end}&lang={lang}"


def _review_url(lang: str, period: str | None = None) -> str:
    base = f"/review?lang={lang}"
    if period is not None:
        return f"{base}&period={period}"
    return base


def _import_url(
    start: str,
    end: str,
    lang: str,
    *,
    imported: int | None = None,
    skipped_status: int | None = None,
    skipped_non_cashflow: int | None = None,
    skipped: int | None = None,
    invalid: int | None = None,
    batch_id: str | None = None,
    deleted: int | None = None,
    preview_token: str | None = None,
) -> str:
    base = f"/import?start={start}&end={end}&lang={lang}"
    if imported is not None:
        base = f"{base}&imported={imported}"
    if skipped_status is None:
        skipped_status = skipped
    if skipped_status is not None:
        base = f"{base}&skipped_status={skipped_status}"
    if skipped_non_cashflow is not None:
        base = f"{base}&skipped_non_cashflow={skipped_non_cashflow}"
    if skipped is not None:
        base = f"{base}&skipped={skipped}"
    if invalid is not None:
        base = f"{base}&invalid={invalid}"
    if batch_id is not None:
        base = f"{base}&batch_id={batch_id}"
    if deleted is not None:
        base = f"{base}&deleted={deleted}"
    if preview_token is not None:
        base = f"{base}&preview_token={preview_token}"
    return base


def _import_preview_url(session_id: str, *, start: str, end: str, lang: str) -> str:
    return f"/import/preview/{session_id}?start={start}&end={end}&lang={lang}"


def _build_secondary_page_context(
    request: Request,
    *,
    start: str,
    end: str,
    lang: str,
    active_page: str,
    review_period: str | None = None,
) -> dict:
    return {
        "request": request,
        "account_id": 1,
        "active_account_name": TRANSLATIONS[lang]["summary_account"],
        "start": start,
        "end": end,
        "show_archived": False,
        "lang": lang,
        "t": TRANSLATIONS[lang],
        "active_page": active_page,
        "ledger_url": _index_url(start, end, lang),
        "review_url": _review_url(lang, period=review_period),
        "import_url": _import_url(start, end, lang),
    }
