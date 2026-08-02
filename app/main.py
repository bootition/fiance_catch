from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .router_support.settings_access import configure_settings
from .routers import status
from .settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_settings(settings)
    init_db(settings)
    yield


app = FastAPI(lifespan=lifespan)
_static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
app.include_router(status.router)
