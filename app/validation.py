import re
from datetime import date as dt_date

from fastapi import HTTPException

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_iso_date(value: str, *, field_name: str) -> str:
    """Validate and normalize an ISO date string (YYYY-MM-DD)."""
    if not _ISO_DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD")
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be YYYY-MM-DD"
        ) from exc
    return value
