import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, str_enum
from app.models.document import Document
from app.models.notification import Notification


class QueryLetterStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    # Query points are ready; document matching comes next.
    EXTRACTED = "extracted"
    MATCHING = "matching"
    # Something is uncertain: nothing is sent until staff resolve it.
    AWAITING_REVIEW = "awaiting_review"
    # Matching is settled and the patient must be asked for missing items.
    READY_TO_NOTIFY = "ready_to_notify"
    # Every requirement is already covered by the locker.
    NO_ACTION_REQUIRED = "no_action_required"
    # The patient message was delivered on every channel.
    NOTIFIED = "notified"
    FAILED = "failed"


class LetterOutcome(StrEnum):
    SEND_MISSING_ITEMS_MESSAGE = "send_missing_items_message"
    HOLD_FOR_STAFF_REVIEW = "hold_for_staff_review"
    NO_ADDITIONAL_DOCUMENTS_REQUIRED = "no_additional_documents_required"


class MatchStatus(StrEnum):
    COVERED = "covered"
    NEEDS_FROM_PATIENT = "needs_from_patient"
    NEEDS_REVIEW = "needs_review"


class IdentityStatus(StrEnum):
    # Claim number, policy number or name in the letter agree with the patient profile.
    MATCH = "match"
    # At least one of them disagrees: the letter may belong to someone else.
    MISMATCH = "mismatch"
    # The letter states none of them.
    UNKNOWN = "unknown"


class RequirementType(StrEnum):
    """What a query point asks for: a document category, a clarification, or something else."""

    DISCHARGE_SUMMARY = "discharge_summary"
    HOSPITAL_BILL = "hospital_bill"
    INVESTIGATION_REPORT = "investigation_report"
    CONSULTATION_PAPER = "consultation_paper"
    PHARMACY_BILL = "pharmacy_bill"
    POLICY_COPY = "policy_copy"
    HEALTH_CARD = "health_card"
    KYC_DOCUMENT = "kyc_document"
    OTHER = "other"
    CLARIFICATION = "clarification"


class QueryLetter(Base):
    """An insurer's query letter for a patient, and the result of reading it."""

    __tablename__ = "query_letters"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), unique=True)
    status: Mapped[QueryLetterStatus] = mapped_column(
        str_enum(QueryLetterStatus, "query_letter_status"),
        default=QueryLetterStatus.RECEIVED,
        server_default=QueryLetterStatus.RECEIVED.value,
    )
    identity_status: Mapped[IdentityStatus | None] = mapped_column(
        str_enum(IdentityStatus, "identity_status")
    )
    identity_details: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    letter_date: Mapped[str | None] = mapped_column(String(40))
    claim_reference: Mapped[str | None] = mapped_column(String(64))
    # Sender as printed on the letterhead (insurer or TPA); staff can correct it.
    insurer_name: Mapped[str | None] = mapped_column(String(200))
    # "within 15 days of this letter" -> 15; None when the letter sets no deadline.
    response_days: Mapped[int | None]
    # True when staff must look at the letter before anything is sent for it.
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    review_reasons: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Every candidate line with its keep/skip decision, warnings, and the model used.
    extraction_details: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[LetterOutcome | None] = mapped_column(str_enum(LetterOutcome, "letter_outcome"))
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    extracted_at: Mapped[datetime | None]
    matched_at: Mapped[datetime | None]

    document: Mapped[Document] = relationship(lazy="joined")
    points: Mapped[list["QueryPoint"]] = relationship(
        back_populates="query_letter",
        order_by="QueryPoint.seq",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["PatientMessage"]] = relationship(
        back_populates="query_letter",
        order_by="PatientMessage.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def current_message(self) -> "PatientMessage | None":
        live = [m for m in self.messages if m.superseded_at is None]
        return live[-1] if live else None


class QueryPoint(Base):
    """One separate requirement from a query letter."""

    __tablename__ = "query_points"
    __table_args__ = (UniqueConstraint("query_letter_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query_letter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_letters.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int]
    requirement: Mapped[str] = mapped_column(String(300))
    requirement_type: Mapped[RequirementType] = mapped_column(
        str_enum(RequirementType, "requirement_type")
    )
    # [{"code": "signed_by_doctor", "text": "signed by the treating doctor"}, ...]
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # The exact sentence or list item of the letter this point came from.
    source_text: Mapped[str] = mapped_column(Text)
    segment_index: Mapped[int]
    # "rules", "model" or "rules_and_model"
    source: Mapped[str] = mapped_column(String(16))
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    review_reason: Mapped[str | None] = mapped_column(String(64))
    # Set when staff decide an extracted point is not actually a requirement.
    dismissed_at: Mapped[datetime | None]
    dismissed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    query_letter: Mapped[QueryLetter] = relationship(back_populates="points")
    matches: Mapped[list["MatchResult"]] = relationship(
        back_populates="query_point",
        order_by="MatchResult.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def current_match(self) -> "MatchResult | None":
        live = [m for m in self.matches if m.superseded_at is None]
        return live[-1] if live else None


class MatchResult(Base):
    """A decision on one query point, with its evidence. Earlier decisions are kept, superseded."""

    __tablename__ = "match_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query_point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_points.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[MatchStatus] = mapped_column(str_enum(MatchStatus, "match_status"))
    explanation: Mapped[str] = mapped_column(Text)
    # [{"document_id", "filename", "page", "quote", "verified"} or {"kind": "signature", ...}]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # [{"code", "result": "met" | "not_met" | "unknown", "detail", "document_id"}]
    condition_checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    model_opinion: Mapped[dict[str, Any] | None]
    # "system" or "staff"
    decided_by: Mapped[str] = mapped_column(String(16))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    superseded_at: Mapped[datetime | None]

    query_point: Mapped[QueryPoint] = relationship(back_populates="matches")


class MessageKind(StrEnum):
    DOCUMENTS_NEEDED = "documents_needed"
    NOTHING_NEEDED = "nothing_needed"


class MessageStatus(StrEnum):
    # Rendered from the template and ready for delivery.
    READY = "ready"
    # A template value is missing; staff must supply it before anything can be sent.
    BLOCKED = "blocked"


class PatientMessage(Base):
    """The patient message for a letter, rendered from the company templates.

    A new version is created only when the content changes; older versions are kept,
    superseded, so the history of what was prepared (and later sent) is complete.
    """

    __tablename__ = "patient_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query_letter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_letters.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MessageKind] = mapped_column(str_enum(MessageKind, "message_kind"))
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus, "message_status"))
    blocked_reasons: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    template_version: Mapped[str] = mapped_column(String(40))
    whatsapp_text: Mapped[str | None] = mapped_column(Text)
    email_subject: Mapped[str | None] = mapped_column(String(300))
    email_text: Mapped[str | None] = mapped_column(Text)
    email_html: Mapped[str | None] = mapped_column(Text)
    # The placeholder values used, so every message can be traced to stored data.
    values: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    # The NEEDS_FROM_PATIENT points listed, in order.
    point_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    superseded_at: Mapped[datetime | None]

    query_letter: Mapped[QueryLetter] = relationship(back_populates="messages")
    notifications: Mapped[list["Notification"]] = relationship(
        order_by="Notification.created_at", viewonly=True
    )
