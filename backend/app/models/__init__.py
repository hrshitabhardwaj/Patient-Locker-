"""Import every model here so Alembic autogenerate and relationships can see them."""

from app.models.audit import AuditLog
from app.models.document import (
    ClassificationMethod,
    Document,
    DocumentCategory,
    DocumentKind,
    DocumentPage,
    DocumentProfile,
    ProcessingStatus,
    TextMethod,
)
from app.models.job import Job, JobStatus
from app.models.notification import Channel, Notification, NotificationEvent, NotificationStatus
from app.models.patient import Patient, PatientAssignment
from app.models.query import (
    IdentityStatus,
    LetterOutcome,
    MatchResult,
    MatchStatus,
    MessageKind,
    MessageStatus,
    PatientMessage,
    QueryLetter,
    QueryLetterStatus,
    QueryPoint,
    RequirementType,
)
from app.models.user import User, UserSession

__all__ = [
    "AuditLog",
    "Channel",
    "Notification",
    "NotificationEvent",
    "NotificationStatus",
    "ClassificationMethod",
    "Document",
    "DocumentCategory",
    "DocumentKind",
    "DocumentPage",
    "DocumentProfile",
    "IdentityStatus",
    "Job",
    "JobStatus",
    "LetterOutcome",
    "MatchResult",
    "MatchStatus",
    "MessageKind",
    "MessageStatus",
    "Patient",
    "PatientMessage",
    "PatientAssignment",
    "ProcessingStatus",
    "QueryLetter",
    "QueryLetterStatus",
    "QueryPoint",
    "RequirementType",
    "TextMethod",
    "User",
    "UserSession",
]
