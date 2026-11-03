import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.db.session import DbSession
from app.models import LetterOutcome, PatientMessage, QueryLetter, QueryLetterStatus
from app.schemas.query import MessageOut
from app.services import dashboard
from app.services.patients import get_accessible_patient

router = APIRouter(tags=["dashboard"])


class DashboardStats(BaseModel):
    total_patients: int
    total_documents: int
    total_query_letters: int
    letters_processing: int
    letters_awaiting_review: int
    letters_completed: int
    letters_failed: int
    patients_with_missing_documents: int


class LetterRow(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_code: str
    patient_name: str
    status: QueryLetterStatus
    outcome: LetterOutcome | None
    review_reasons: list[str]
    insurer_name: str | None
    claim_reference: str | None
    created_at: datetime


class PatientMessageRow(BaseModel):
    letter_id: uuid.UUID
    letter_date: str | None
    insurer_name: str | None
    message: MessageOut


@router.get("/dashboard/stats", response_model=DashboardStats)
def get_stats(actor: CurrentUser, db: DbSession):
    return dashboard.stats(db, actor)


@router.get("/dashboard/letters", response_model=list[LetterRow])
def list_letters(
    actor: CurrentUser,
    db: DbSession,
    status: QueryLetterStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Recent query letters across visible patients; status=awaiting_review is the review queue."""
    return [
        LetterRow(
            id=letter.id,
            patient_id=patient.id,
            patient_code=patient.patient_code,
            patient_name=patient.full_name,
            status=letter.status,
            outcome=letter.outcome,
            review_reasons=letter.review_reasons,
            insurer_name=letter.insurer_name,
            claim_reference=letter.claim_reference,
            created_at=letter.created_at,
        )
        for letter, patient in dashboard.recent_letters(db, actor, status=status, limit=limit)
    ]


@router.get("/patients/{patient_id}/messages", response_model=list[PatientMessageRow])
def patient_messages(patient_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    """Every patient message prepared for this patient, newest first, with delivery status."""
    get_accessible_patient(db, actor, patient_id, request=request)
    rows = db.execute(
        select(PatientMessage, QueryLetter)
        .join(QueryLetter, QueryLetter.id == PatientMessage.query_letter_id)
        .where(QueryLetter.patient_id == patient_id)
        .order_by(PatientMessage.created_at.desc())
    ).all()
    return [
        PatientMessageRow(
            letter_id=letter.id,
            letter_date=letter.letter_date,
            insurer_name=letter.insurer_name,
            message=MessageOut.model_validate(message),
        )
        for message, letter in rows
    ]
