from datetime import date as dt_date, timedelta
import re

from fastapi import HTTPException


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _current_month_range(today: dt_date | None = None) -> tuple[str, str]:
    current = today or dt_date.today()
    month_start = dt_date(current.year, current.month, 1)
    if current.month == 12:
        next_month_start = dt_date(current.year + 1, 1, 1)
    else:
        next_month_start = dt_date(current.year, current.month + 1, 1)
    month_end = next_month_start - timedelta(days=1)
    return month_start.isoformat(), month_end.isoformat()


def _validate_iso_date(value: str, *, field_name: str) -> str:
    if not _ISO_DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD")
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be YYYY-MM-DD"
        ) from exc
    return value


def _resolve_range(start: str | None, end: str | None) -> tuple[str, str]:
    default_start, default_end = _current_month_range()
    resolved_start = (
        default_start
        if start is None
        else _validate_iso_date(start, field_name="start")
    )
    resolved_end = (
        default_end if end is None else _validate_iso_date(end, field_name="end")
    )
    return resolved_start, resolved_end


def _optional_trimmed(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _optional_iso_date(value: str | None, *, field_name: str) -> str | None:
    normalized = _optional_trimmed(value)
    if normalized is None:
        return None
    return _validate_iso_date(normalized, field_name=field_name)
