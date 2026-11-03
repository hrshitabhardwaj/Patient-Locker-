"""Write audit entries. Callers add the entry in the same transaction as the action it records."""

import uuid
from enum import StrEnum
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


class AuditAction(StrEnum):
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"
    PASSWORD_CHANGE_FAILED = "auth.password_change_failed"
    ACCESS_DENIED = "access.denied"
    CSRF_REJECTED = "access.csrf_rejected"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_PASSWORD_RESET = "user.password_reset"
    AUDIT_LOG_VIEWED = "audit.viewed"
    PATIENT_CREATED = "patient.created"
    PATIENT_UPDATED = "patient.updated"
    PATIENT_VIEWED = "patient.viewed"
    PATIENT_ASSIGNMENTS_CHANGED = "patient.assignments_changed"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_UPLOAD_REJECTED = "document.upload_rejected"
    DOCUMENT_VIEWED = "document.viewed"
    DOCUMENT_DOWNLOADED = "document.downloaded"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_ANALYSIS_VIEWED = "document.analysis_viewed"
    DOCUMENT_REPROCESS_REQUESTED = "document.reprocess_requested"
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_PROCESSING_FAILED = "document.processing_failed"
    DOCUMENT_PROFILED = "document.profiled"
    DOCUMENT_PROFILE_FAILED = "document.profile_failed"
    QUERY_LETTER_UPLOADED = "query_letter.uploaded"
    QUERY_LETTER_VIEWED = "query_letter.viewed"
    QUERY_LETTER_REPROCESS_REQUESTED = "query_letter.reprocess_requested"
    QUERY_LETTER_EXTRACTED = "query_letter.extracted"
    QUERY_LETTER_FAILED = "query_letter.failed"
    QUERY_LETTER_MATCHED = "query_letter.matched"
    QUERY_LETTER_MATCH_FAILED = "query_letter.match_failed"
    QUERY_LETTER_REMATCH_REQUESTED = "query_letter.rematch_requested"
    QUERY_LETTER_CONFIRMED = "query_letter.confirmed"
    QUERY_POINT_REVIEWED = "query_point.reviewed"
    QUERY_LETTER_UPDATED = "query_letter.updated"
    MESSAGE_PREPARED = "message.prepared"
    NOTIFICATION_ATTEMPTED = "notification.attempted"
    NOTIFICATION_STATUS_UPDATED = "notification.status_updated"
    WEBHOOK_REJECTED = "webhook.rejected"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


# Details carry IDs and codes. These keys would mean personal data is about to be stored.
# Filenames are included because they often contain the patient's name.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "email",
        "phone",
        "name",
        "full_name",
        "password",
        "temporary_password",
        "address",
        "text",
        "filename",
        "date_of_birth",
    }
)


def record_audit(
    db: Session,
    *,
    action: AuditAction,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    patient_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    details = details or {}
    leaked = _FORBIDDEN_DETAIL_KEYS & details.keys()
    if leaked:
        raise ValueError(f"Audit details must not contain personal data: {sorted(leaked)}")

    entry = AuditLog(
        action=str(action),
        outcome=str(outcome),
        actor_user_id=actor_id,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        patient_id=patient_id,
        ip_address=request.client.host if request and request.client else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
        details=details,
    )
    db.add(entry)
    return entry
