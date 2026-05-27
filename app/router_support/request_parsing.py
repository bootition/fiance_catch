from datetime import date as dt_date, timedelta

from fastapi import HTTPException

from ..validation import validate_iso_date


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
    resolved_start = (
        default_start
        if start is None
        else validate_iso_date(start, field_name="start")
    )
    resolved_end = (
        default_end if end is None else validate_iso_date(end, field_name="end")
    )
    _validate_range_order(resolved_start, resolved_end)
    return resolved_start, resolved_end


def _validate_range_order(start: str | None, end: str | None) -> None:
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=400, detail="start must be on or before end"
        )


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
    return validate_iso_date(normalized, field_name=field_name)
