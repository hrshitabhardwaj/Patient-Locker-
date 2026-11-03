import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    action: str
    outcome: str
    entity_type: str | None
    entity_id: str | None
    patient_id: uuid.UUID | None
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]
