import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

settings = get_settings()
logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Ilova ishga tushdi")
    yield
    await engine.dispose()
    log.info("Ilova to'xtadi")


app = FastAPI(title="MLBB Tournament", lifespan=lifespan)


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as e:  # noqa: BLE001
        log.error("DB tekshiruvi xato: %s", e)
        return {"status": "degraded", "db": "error"}
