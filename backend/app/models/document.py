import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, str_enum
from app.models.user import User


class DocumentKind(StrEnum):
    SUPPORTING = "supporting"
    QUERY_LETTER = "query_letter"


class DocumentCategory(StrEnum):
    DISCHARGE_SUMMARY = "discharge_summary"
    HOSPITAL_BILL = "hospital_bill"
    INVESTIGATION_REPORT = "investigation_report"
    CONSULTATION_PAPER = "consultation_paper"
    PHARMACY_BILL = "pharmacy_bill"
    POLICY_COPY = "policy_copy"
    HEALTH_CARD = "health_card"
    KYC_DOCUMENT = "kyc_document"
    OTHER = "other"


class ProcessingStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    # Readable as a file, but no usable text could be extracted (e.g. a blank or illegible scan).
    UNREADABLE = "unreadable"
    FAILED = "failed"


class TextMethod(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    # Digital text plus OCR of embedded scans on the same page.
    TEXT_LAYER_AND_OCR = "text_layer_and_ocr"


class ClassificationMethod(StrEnum):
    RULES = "rules"
    LLM = "llm"
    RULES_AND_LLM = "rules_and_llm"
    # Rules and model disagreed; the category is a best guess and should be checked.
    CONFLICT = "conflict"


class Document(Base):
    """A file in a patient's locker. The file itself is stored encrypted under `storage_key`.

    Replacing a document creates a new row pointing at the old one (`supersedes_id`), so the
    full version history is kept. Deleting is a soft delete.
    """

    __tablename__ = "documents"
    __table_args__ = (
        # The same file can't be in one patient's locker twice (deleted copies don't count).
        Index(
            "uq_documents_patient_sha256_live",
            "patient_id",
            "sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    kind: Mapped[DocumentKind] = mapped_column(str_enum(DocumentKind, "document_kind"))
    # The category staff chose at upload; null when they left it for automatic classification.
    declared_category: Mapped[DocumentCategory | None] = mapped_column(
        str_enum(DocumentCategory, "document_category")
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int]
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        str_enum(ProcessingStatus, "processing_status"),
        default=ProcessingStatus.UPLOADED,
        server_default=ProcessingStatus.UPLOADED.value,
    )
    # Short code explaining FAILED or UNREADABLE, e.g. "no_readable_text".
    processing_error: Mapped[str | None] = mapped_column(String(64))
    processed_at: Mapped[datetime | None]
    detected_category: Mapped[DocumentCategory | None] = mapped_column(
        str_enum(DocumentCategory, "detected_category")
    )
    classification_confidence: Mapped[float | None]
    classification_method: Mapped[ClassificationMethod | None] = mapped_column(
        str_enum(ClassificationMethod, "classification_method")
    )
    # Rule-extracted labelled values, line-item count and the rule classification.
    extracted_fields: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id"), unique=True
    )
    superseded_at: Mapped[datetime | None]
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None]
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    uploaded_by: Mapped[User] = relationship(foreign_keys=[uploaded_by_id], lazy="joined")

    @property
    def uploaded_by_name(self) -> str:
        return self.uploaded_by.full_name


class DocumentPage(Base):
    """Extracted text of one page, and how it was obtained."""

    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    method: Mapped[TextMethod] = mapped_column(str_enum(TextMethod, "text_method"))
    # Mean Tesseract word confidence (0-100) when OCR was used.
    ocr_confidence: Mapped[float | None]
    # Letters and digits only, a measure of how much real text the page has.
    char_count: Mapped[int]


class DocumentProfile(Base):
    """What the LLM reported about a document, after code verification. One per document."""

    __tablename__ = "document_profiles"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    # "succeeded" or "failed"
    status: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    category: Mapped[DocumentCategory | None] = mapped_column(
        str_enum(DocumentCategory, "profile_category")
    )
    facts: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(200))
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
