from fastapi import HTTPException

from .importing_shared import _is_valid_import_batch_id
from .request_parsing import _optional_iso_date, _optional_trimmed


def _parse_bulk_direction(direction: str | None) -> str | None:
    normalized = _optional_trimmed(direction)
    if normalized is None:
        return None
    if normalized not in {"income", "expense", "neutral"}:
        raise HTTPException(
            status_code=400,
            detail="direction must be income, expense, or neutral",
        )
    return normalized


def _parse_imported_only(value: str | None) -> bool | None:
    normalized = _optional_trimmed(value)
    if normalized is None:
        return None
    if normalized == "1":
        return True
    if normalized == "0":
        return False
    raise HTTPException(status_code=400, detail="imported_only must be 0 or 1")


def _normalize_batch_ids(batch_ids: list[str] | None) -> list[str]:
    if not batch_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in batch_ids:
        value = raw_value.strip()
        if not value:
            continue
        if not _is_valid_import_batch_id(value):
            raise HTTPException(status_code=400, detail="invalid batch_id")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _build_bulk_delete_filters(
    *,
    start: str | None,
    end: str | None,
    direction: str | None,
    category: str | None,
    note_contains: str | None,
    imported_only: str | None,
    batch_ids: list[str] | None,
) -> dict:
    return {
        "start": _optional_iso_date(start, field_name="start"),
        "end": _optional_iso_date(end, field_name="end"),
        "direction": _parse_bulk_direction(direction),
        "category": _optional_trimmed(category),
        "note_contains": _optional_trimmed(note_contains),
        "imported_only": _parse_imported_only(imported_only),
        "batch_ids": _normalize_batch_ids(batch_ids),
    }


def _is_empty_bulk_delete_filters(filters: dict) -> bool:
    return (
        filters.get("start") is None
        and filters.get("end") is None
        and filters.get("direction") is None
        and filters.get("category") is None
        and filters.get("note_contains") is None
        and filters.get("imported_only") is None
        and not filters.get("batch_ids")
    )
