from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..db import connect
from ..migration_v2 import LEDGER_V2_MARKER, new_schema_initialized
from ..router_support.settings_access import current_settings
from ..templates_core import templates

router = APIRouter(tags=["Status"])

V2_TABLES = (
    "import_batches",
    "source_transactions",
    "ledger_entries",
    "review_queue",
    "refund_links",
    "classification_rules",
    "entry_audit_events",
)


def _build_status(settings):
    with connect(settings.db_path) as conn:
        initialized = new_schema_initialized(conn)
        marker = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (LEDGER_V2_MARKER,),
        ).fetchone()
        table_counts: dict[str, int | None] = {}
        for table in V2_TABLES:
            try:
                table_counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                )
            except Exception:
                table_counts[table] = None
    backups = []
    if settings.data_dir.exists():
        backups = sorted(settings.data_dir.glob("ledger.sqlite-*.bak"))
    return {
        "initialized": initialized,
        "migrated_at": marker["value"] if marker is not None else None,
        "table_counts": table_counts,
        "backup_names": [p.name for p in backups],
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    context = {"request": request, **_build_status(current_settings())}
    return templates.TemplateResponse(request, "maintenance.html", context)
