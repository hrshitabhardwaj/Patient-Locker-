import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, require_permission
from app.core.permissions import Permission
from app.db.session import DbSession
from app.models import AuditLog
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page
from app.services.audit import AuditAction, record_audit

router = APIRouter(
    prefix="/audit-logs",
    tags=["audit"],
    dependencies=[Depends(require_permission(Permission.READ_AUDIT_LOG))],
)


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(
    actor: CurrentUser,
    request: Request,
    db: DbSession,
    action: Annotated[str | None, Query(max_length=64)] = None,
    outcome: Annotated[str | None, Query(max_length=16)] = None,
    actor_user_id: uuid.UUID | None = None,
    entity_type: Annotated[str | None, Query(max_length=64)] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    patient_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = {
        "action": action,
        "outcome": outcome,
        "actor_user_id": actor_user_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "patient_id": patient_id,
    }
    statement = select(AuditLog)
    for column, value in filters.items():
        if value is not None:
            statement = statement.where(getattr(AuditLog, column) == value)
    if since is not None:
        statement = statement.where(AuditLog.occurred_at >= since)
    if until is not None:
        statement = statement.where(AuditLog.occurred_at < until)

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()

    # Reading the audit trail is itself recorded.
    record_audit(
        db,
        action=AuditAction.AUDIT_LOG_VIEWED,
        actor_id=actor.id,
        request=request,
        details={"filters": {k: str(v) for k, v in filters.items() if v is not None}},
    )
    db.commit()

    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(entry) for entry in items],
        total=total,
        limit=limit,
        offset=offset,
    )
