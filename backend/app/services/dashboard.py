"""Figures and work lists for the portal, limited to the patients the user may see."""

from typing import Any

from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission
from app.models import (
    Document,
    DocumentKind,
    LetterOutcome,
    Patient,
    PatientAssignment,
    QueryLetter,
    QueryLetterStatus,
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
