from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .router_support.settings_access import configure_settings
from .routers import bulk_delete, cleanup, ledger, review
from .settings import get_settings


settings = get_settings()
configure_settings(settings)
init_db(settings)

app = FastAPI()
_static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
app.include_router(ledger.router)
app.include_router(review.router)
app.include_router(cleanup.router)
app.include_router(bulk_delete.router)
