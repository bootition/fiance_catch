from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..decisions.engine import process_batch
from ..importing.service import import_file
from ..revoke import revoke_batch
from ..router_support.settings_access import current_settings
from ..stats import list_batches
from ..templates_core import templates

router = APIRouter(tags=["Imports"])

BLOCK_REASON_LABELS = {
    "manual_edited": "已人工编辑，保留以免破坏用户改动",
    "refund_linked": "已参与退款关联，保留以免破坏退款冲减",
    "ledger_referenced": "被保留的账本记录引用，随其一并保留",
}


def _block_reason_label(blocked_item) -> str:
    return BLOCK_REASON_LABELS.get(blocked_item.reason, blocked_item.reason)


@router.get("/imports/new", response_class=HTMLResponse)
def imports_new(request: Request):
    context = {
        "request": request,
        "active_page": "imports_new",
        "pending_count": _pending_count(),
        "last_result": None,
    }
    return templates.TemplateResponse(request, "imports_new.html", context)


def _pending_count() -> int:
    from ..db import connect

    with connect(current_settings().db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM review_queue WHERE status = 'pending'"
            ).fetchone()["c"]
        )


@router.post("/imports/new", response_class=HTMLResponse)
async def imports_upload(
    request: Request,
    platform: str = Form(...),
    file: UploadFile | None = None,
):
    settings = current_settings()
    if platform not in ("alipay", "wechat"):
        context = {
            "request": request,
            "active_page": "imports_new",
            "pending_count": _pending_count(),
            "last_result": {"error": "无效平台"},
        }
        return templates.TemplateResponse(request, "imports_new.html", context)
    if file is None or not file.filename:
        return RedirectResponse("/imports/new?error=未选择文件", status_code=303)

    content = await file.read()
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(
        suffix=Path(file.filename).suffix, delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = import_file(settings.db_path, tmp_path, platform)
        processed = process_batch(settings.db_path, result.batch_id)
    except (ValueError, FileNotFoundError) as exc:
        context = {
            "request": request,
            "active_page": "imports_new",
            "pending_count": _pending_count(),
            "last_result": {"error": str(exc)},
        }
        return templates.TemplateResponse(request, "imports_new.html", context)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    context = {
        "request": request,
        "active_page": "imports_new",
        "pending_count": _pending_count(),
        "last_result": {
            "file_name": result.file_name,
            "total": result.total,
            "added": result.added,
            "duplicates": result.duplicates,
            "skipped": result.skipped,
            "invalid": result.invalid,
            "refunds": result.refunds,
            "auto_posted": processed.posted,
            "queued": processed.queued,
            "error": None,
        },
    }
    return templates.TemplateResponse(request, "imports_new.html", context)


@router.get("/imports", response_class=HTMLResponse)
def imports_list(request: Request):
    context = {
        "request": request,
        "active_page": "imports",
        "pending_count": _pending_count(),
        "batches": list_batches(current_settings().db_path),
    }
    return templates.TemplateResponse(request, "imports.html", context)


@router.post("/imports/{batch_id}/revoke", response_class=HTMLResponse)
def imports_revoke(request: Request, batch_id: int):
    settings = current_settings()
    blocked = []
    try:
        result = revoke_batch(settings.db_path, batch_id)
        blocked = [
            {
                "kind": b.kind,
                "ref_id": b.ref_id,
                "reason": b.reason,
                "reason_label": _block_reason_label(b),
            }
            for b in result.blocked
        ]
        flash = (
            f"已撤销批次 #{batch_id}：删除来源 {result.deleted_sources}、"
            f"账本 {result.deleted_ledger}、待办 {result.deleted_reviews}；"
            f"保留（阻塞）{result.blocked_count} 项"
        )
    except ValueError as exc:
        flash = f"撤销失败：{exc}"
    context = {
        "request": request,
        "active_page": "imports",
        "pending_count": _pending_count(),
        "batches": list_batches(settings.db_path),
        "flash": flash,
        "blocked": blocked,
        "revoked_batch_id": batch_id,
    }
    return templates.TemplateResponse(request, "imports.html", context)
