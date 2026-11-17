"""Figures and work lists for the portal, limited to the patients the user may see."""

import statistics
from typing import Any

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.models import (
    Document,
    DocumentKind,
    LetterOutcome,
    MatchResult,
    Notification,
    NotificationStatus,
    Patient,
    PatientAssignment,
    PatientMessage,
    QueryLetter,
    QueryLetterStatus,
    QueryPoint,
    User,
)

PROCESSING = (
    QueryLetterStatus.RECEIVED,
    QueryLetterStatus.EXTRACTING,
    QueryLetterStatus.EXTRACTED,
    QueryLetterStatus.MATCHING,
    QueryLetterStatus.READY_TO_NOTIFY,
    QueryLetterStatus.NO_ACTION_REQUIRED,
)


def _visible_patient_ids(user: User) -> Select:
    statement = select(Patient.id)
    if not has_permission(user.role, Permission.ACCESS_ALL_PATIENTS):
        statement = statement.join(
            PatientAssignment,
            (PatientAssignment.patient_id == Patient.id) & (PatientAssignment.user_id == user.id),
        )
    return statement


def stats(db: Session, user: User) -> dict[str, Any]:
    visible = _visible_patient_ids(user).scalar_subquery()
    letters = select(QueryLetter).where(QueryLetter.patient_id.in_(visible)).subquery()

    def count_letters(*conditions) -> int:
        statement = select(func.count()).select_from(letters)
        for condition in conditions:
            statement = statement.where(condition)
        return db.scalar(statement) or 0

    return {
        "total_patients": db.scalar(
            select(func.count()).select_from(_visible_patient_ids(user).subquery())
        )
        or 0,
        "total_documents": db.scalar(
            select(func.count(Document.id)).where(
                Document.patient_id.in_(visible),
                Document.kind == DocumentKind.SUPPORTING,
                Document.deleted_at.is_(None),
                Document.superseded_at.is_(None),
            )
        )
        or 0,
        "total_query_letters": count_letters(),
        "letters_processing": count_letters(letters.c.status.in_(PROCESSING)),
        "letters_awaiting_review": count_letters(
            letters.c.status == QueryLetterStatus.AWAITING_REVIEW
        ),
        "letters_completed": count_letters(letters.c.status == QueryLetterStatus.NOTIFIED),
        "letters_failed": count_letters(letters.c.status == QueryLetterStatus.FAILED),
        "failed_deliveries": db.scalar(
            select(func.count(Notification.id))
            .join(PatientMessage, PatientMessage.id == Notification.message_id)
            .where(
                Notification.patient_id.in_(visible),
                Notification.status == NotificationStatus.FAILED,
                PatientMessage.superseded_at.is_(None),
            )
        )
        or 0,
        "median_seconds_to_message": _median_seconds_to_message(db, visible),
        "patients_with_missing_documents": db.scalar(
            select(func.count(distinct(letters.c.patient_id))).where(
                letters.c.outcome == LetterOutcome.SEND_MISSING_ITEMS_MESSAGE
            )
        )
        or 0,
    }


def recent_letters(
    db: Session, user: User, *, status: QueryLetterStatus | None = None, limit: int = 20
) -> list[tuple[QueryLetter, Patient]]:
    statement = (
        select(QueryLetter, Patient)
        .join(Patient, Patient.id == QueryLetter.patient_id)
        .where(QueryLetter.patient_id.in_(_visible_patient_ids(user).scalar_subquery()))
        .order_by(QueryLetter.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        statement = statement.where(QueryLetter.status == status)
    return [(letter, patient) for letter, patient in db.execute(statement).all()]


def _median_seconds_to_message(db: Session, visible, sample: int = 50) -> float | None:
    """Median time from a letter's upload to its first message being sent, over the most
    recent letters handled without a staff decision (those wait for a person)."""
    first_sent = func.min(Notification.sent_at)
    rows = db.execute(
        select(QueryLetter.created_at, first_sent)
        .join(Notification, Notification.query_letter_id == QueryLetter.id)
        .where(QueryLetter.patient_id.in_(visible), Notification.sent_at.is_not(None))
        .where(QueryLetter.outcome != LetterOutcome.HOLD_FOR_STAFF_REVIEW)
        .where(
            ~select(MatchResult.id)
            .join(QueryPoint, QueryPoint.id == MatchResult.query_point_id)
            .where(QueryPoint.query_letter_id == QueryLetter.id, MatchResult.decided_by == "staff")
            .exists()
        )
        .group_by(QueryLetter.id, QueryLetter.created_at)
        .order_by(QueryLetter.created_at.desc())
        .limit(sample)
    ).all()
    durations = [
        (sent - uploaded).total_seconds() for uploaded, sent in rows if sent and sent >= uploaded
    ]
    return round(statistics.median(durations), 1) if durations else None


def recent_deliveries(
    db: Session, user: User, *, limit: int = 8
) -> list[tuple[Notification, Patient]]:
    statement = (
        select(Notification, Patient)
        .join(Patient, Patient.id == Notification.patient_id)
        .where(Notification.patient_id.in_(_visible_patient_ids(user).scalar_subquery()))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return [(notification, patient) for notification, patient in db.execute(statement).all()]
