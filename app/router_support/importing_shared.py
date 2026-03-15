import re
from time import time
from uuid import uuid4


_BULK_DELETE_PREVIEW_TTL_SECONDS = 900
_BULK_DELETE_PREVIEWS: dict[str, dict] = {}


def _cleanup_bulk_delete_tokens() -> None:
    now_ts = time()
    expired_tokens = [
        token
        for token, payload in _BULK_DELETE_PREVIEWS.items()
        if now_ts - float(payload.get("created_at", 0.0))
        > _BULK_DELETE_PREVIEW_TTL_SECONDS
    ]
    for token in expired_tokens:
        _BULK_DELETE_PREVIEWS.pop(token, None)


def _issue_bulk_delete_token(
    *,
    filters: dict,
    matched_count: int,
    sample_rows: list[dict],
    allow_delete_all: bool,
) -> str:
    _cleanup_bulk_delete_tokens()
    token = uuid4().hex
    _BULK_DELETE_PREVIEWS[token] = {
        "filters": filters,
        "matched_count": int(matched_count),
        "sample_rows": sample_rows,
        "allow_delete_all": allow_delete_all,
        "created_at": time(),
    }
    return token


def _get_bulk_delete_token_payload(delete_token: str) -> dict | None:
    _cleanup_bulk_delete_tokens()
    return _BULK_DELETE_PREVIEWS.get(delete_token)


def _drop_bulk_delete_token(delete_token: str) -> None:
    _BULK_DELETE_PREVIEWS.pop(delete_token, None)


def _parse_include_neutral(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip()
    if normalized == "1":
        return True
    if normalized == "0":
        return False
    from fastapi import HTTPException

    raise HTTPException(status_code=400, detail="include_neutral must be 0 or 1")


def _is_valid_import_session_id(session_id: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{32}", session_id) is not None


def _parse_status_label(parse_status: str, t: dict[str, str]) -> str:
    key = f"import_preview_status_{parse_status}"
    return t.get(key, parse_status)


def _is_valid_import_batch_id(batch_id: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{32}", batch_id) is not None
