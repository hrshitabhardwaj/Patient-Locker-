import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import (
    ClassificationMethod,
    DocumentCategory,
    DocumentKind,
    ProcessingStatus,
    TextMethod,
)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    kind: DocumentKind
    declared_category: DocumentCategory | None
    original_filename: str
    mime_type: str
    size_bytes: int
    page_count: int
    processing_status: ProcessingStatus
    processing_error: str | None
    processed_at: datetime | None
    detected_category: DocumentCategory | None
    classification_confidence: float | None
    classification_method: ClassificationMethod | None
    uploaded_by_id: uuid.UUID
    uploaded_by_name: str
    uploaded_at: datetime
    supersedes_id: uuid.UUID | None
    superseded_at: datetime | None
    deleted_at: datetime | None


class DocumentCategoryUpdate(BaseModel):
    # Required key; null means "not specified, classify automatically".
    declared_category: DocumentCategory | None


class DocumentEvent(BaseModel):
    occurred_at: datetime
    action: str
    outcome: str
    document_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    details: dict[str, Any]


class DocumentHistoryOut(BaseModel):
    versions: list[DocumentOut]
    events: list[DocumentEvent]


class PageTextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    method: TextMethod
    ocr_confidence: float | None
    char_count: int
    text: str


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    model: str
    prompt_version: str
    category: DocumentCategory | None
    facts: dict[str, Any]
    error: str | None
    duration_ms: int | None
    updated_at: datetime


class DocumentAnalysisOut(BaseModel):
    document: DocumentOut
    extracted_fields: dict[str, Any]
    pages: list[PageTextOut]
    profile: ProfileOut | None


class ReprocessOut(BaseModel):
    queued: bool
