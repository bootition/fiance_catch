from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import accounts, bulk_delete, importing, ledger, review
from .settings import get_settings


settings = get_settings()
init_db(settings)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(ledger.router)
app.include_router(review.router)
app.include_router(accounts.router)
app.include_router(importing.router)
app.include_router(bulk_delete.router)
