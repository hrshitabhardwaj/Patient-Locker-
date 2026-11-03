import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import (
    IdentityStatus,
    LetterOutcome,
    MatchStatus,
    MessageKind,
    MessageStatus,
    QueryLetterStatus,
    RequirementType,
)


class NotificationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    detail: str | None
    occurred_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    provider: str
    recipient: str
    status: str
    attempts: int
    provider_message_id: str | None
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    failed_at: datetime | None
    events: list[NotificationEventOut]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query_letter_id: uuid.UUID
    kind: MessageKind
    status: MessageStatus
    blocked_reasons: list[str]
    template_version: str
    whatsapp_text: str | None
    email_subject: str | None
    email_text: str | None
    email_html: str | None
    values: dict[str, Any]
    point_ids: list[str]
    created_at: datetime
    superseded_at: datetime | None
    notifications: list[NotificationOut]


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: MatchStatus
    explanation: str
    evidence: list[dict[str, Any]]
    condition_checks: list[dict[str, Any]]
    model_opinion: dict[str, Any] | None
    decided_by: str
    reviewer_id: uuid.UUID | None
    created_at: datetime


class QueryPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    requirement: str
    requirement_type: RequirementType
    conditions: list[dict[str, Any]]
    source_text: str
    source: str
    needs_review: bool
    review_reason: str | None
    dismissed_at: datetime | None
    current_match: MatchOut | None


class QueryLetterOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    status: QueryLetterStatus
    outcome: LetterOutcome | None
    identity_status: IdentityStatus | None
    needs_review: bool
    review_reasons: list[str]
    letter_date: str | None
    claim_reference: str | None
    insurer_name: str | None
    response_days: int | None
    point_count: int
    error: str | None
    created_at: datetime
    extracted_at: datetime | None
    matched_at: datetime | None


class QueryLetterDetail(QueryLetterOut):
    identity_details: dict[str, Any]
    extraction_details: dict[str, Any]
    points: list[QueryPointOut]
    current_message: MessageOut | None


class LetterDetailsUpdate(BaseModel):
    """Values staff may correct; only the fields sent are changed."""

    insurer_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)
    ] | None = None
    claim_reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=64)
    ] | None = None
    response_days: Annotated[int, Field(ge=1, le=90)] | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "LetterDetailsUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change.")
        return self


Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class PointReview(BaseModel):
    decision: Literal["covered", "needs_from_patient", "not_a_requirement"]
    document_id: uuid.UUID | None = None
    note: Note

    @model_validator(mode="after")
    def _document_for_covered(self) -> "PointReview":
        if self.decision == "covered" and self.document_id is None:
            raise ValueError("Choose the locker document that covers this requirement.")
        return self


class LetterConfirmation(BaseModel):
    note: Note
