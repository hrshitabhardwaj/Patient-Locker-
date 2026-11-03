import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import schema
from app.db.session import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable", "migrations_pending"]


@router.get("/health", response_model=HealthResponse)
def health(response: Response, db: DbSession) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        # A reachable but empty or outdated database still fails every real request.
        up_to_date = schema.is_up_to_date(db)
    except SQLAlchemyError:
        logger.exception("Database health check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")
    if not up_to_date:
        logger.error("Database schema is not at the latest migration; run `alembic upgrade head`")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="migrations_pending")
    return HealthResponse(status="ok", database="ok")
