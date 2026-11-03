import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable", "migrations_pending"]


@lru_cache
def latest_migration() -> str | None:
    return ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: DbSession) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        # A reachable but empty or outdated database still fails every real request.
        current = None
        if db.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
            current = db.scalar(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError:
        logger.exception("Database health check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")
    if current != latest_migration():
        logger.error("Database schema is not at the latest migration; run `alembic upgrade head`")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="migrations_pending")
    return HealthResponse(status="ok", database="ok")
