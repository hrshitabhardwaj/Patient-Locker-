import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile, status

from app.api.deps import CurrentUser, require_permission
from app.core.config import get_settings
from app.core.errors import UnprocessableError
from app.core.permissions import Permission
from app.db.session import DbSession
from app.models import QueryLetter
from app.schemas.document import ReprocessOut
from app.schemas.query import (
    LetterConfirmation,
    LetterDetailsUpdate,
    MessageOut,
    PointReview,
    QueryLetterDetail,
    QueryLetterOut,
    QueryPointOut,
)
from app.services import matching, messages
from app.services import query_letters as query_service
from app.services.audit import AuditAction, record_audit
from app.services.patients import get_accessible_patient

router = APIRouter(tags=["query letters"])


def _summary(letter: QueryLetter) -> dict:
    return {
        "id": letter.id,
        "patient_id": letter.patient_id,
        "document_id": letter.document_id,
        "original_filename": letter.document.original_filename,
        "status": letter.status,
        "outcome": letter.outcome,
        "identity_status": letter.identity_status,
        "needs_review": letter.needs_review,
        "review_reasons": letter.review_reasons,
        "letter_date": letter.letter_date,
        "claim_reference": letter.claim_reference,
        "insurer_name": letter.insurer_name,
        "response_days": letter.response_days,
        "point_count": len(letter.points),
        "error": letter.error,
        "created_at": letter.created_at,
        "extracted_at": letter.extracted_at,
        "matched_at": letter.matched_at,
    }


def _detail(letter: QueryLetter) -> QueryLetterDetail:
    return QueryLetterDetail(
        **_summary(letter),
        identity_details=letter.identity_details,
        extraction_details=letter.extraction_details,
        points=[QueryPointOut.model_validate(point) for point in letter.points],
        current_message=(
            MessageOut.model_validate(letter.current_message) if letter.current_message else None
        ),
    )


@router.post(
    "/patients/{patient_id}/query-letters",
    response_model=QueryLetterOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_query_letter(
    patient_id: uuid.UUID,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File()],
):
    """Upload an insurer's query letter. Extraction starts automatically."""
    patient = get_accessible_patient(db, actor, patient_id, request=request)
    limit = get_settings().max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise UnprocessableError(
            f"The file is larger than the {get_settings().max_upload_mb} MB limit."
        )
    letter = query_service.upload_query_letter(
        db, actor=actor, patient=patient, data=data, filename=file.filename, request=request
    )
    db.refresh(letter)
    return QueryLetterOut(**_summary(letter))


@router.get("/patients/{patient_id}/query-letters", response_model=list[QueryLetterOut])
def list_query_letters(
    patient_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    letters = query_service.list_query_letters(
        db, actor=actor, patient_id=patient_id, request=request
    )
    return [QueryLetterOut(**_summary(letter)) for letter in letters]


@router.get("/query-letters/{letter_id}", response_model=QueryLetterDetail)
def get_query_letter(letter_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    letter = query_service.view_query_letter(
        db, actor=actor, letter_id=letter_id, request=request
    )
    return _detail(letter)


@router.patch("/query-letters/{letter_id}", response_model=QueryLetterDetail)
def update_query_letter(
    letter_id: uuid.UUID,
    body: LetterDetailsUpdate,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
):
    """Correct the sender, claim number or deadline used in the patient message."""
    letter = query_service.get_accessible_letter(db, actor, letter_id, request=request)
    messages.update_letter_details(
        db,
        actor=actor,
        letter=letter,
        changes=body.model_dump(exclude_unset=True),
        request=request,
    )
    return _detail(letter)


@router.get("/query-letters/{letter_id}/messages", response_model=list[MessageOut])
def list_letter_messages(
    letter_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    """Every version of the patient message prepared for this letter, oldest first."""
    letter = query_service.get_accessible_letter(db, actor, letter_id, request=request)
    return [MessageOut.model_validate(m) for m in letter.messages]


@router.post(
    "/query-letters/{letter_id}/rematch",
    response_model=ReprocessOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def rematch_query_letter(
    letter_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    """Match again against the current locker (e.g. after the patient sent documents)."""
    letter = query_service.get_accessible_letter(db, actor, letter_id, request=request)
    queued = matching.queue_matching(db, letter.id)
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_REMATCH_REQUESTED,
        actor_id=actor.id,
        request=request,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={"queued": queued},
    )
    db.commit()
    return ReprocessOut(queued=queued)


@router.post(
    "/query-points/{point_id}/review",
    response_model=QueryLetterDetail,
    dependencies=[Depends(require_permission(Permission.RESOLVE_REVIEWS))],
)
def review_query_point(
    point_id: uuid.UUID, body: PointReview, actor: CurrentUser, request: Request, db: DbSession
):
    """Staff decision on one point. The letter's outcome is recalculated."""
    point = matching.get_point(db, point_id)
    query_service.get_accessible_letter(db, actor, point.query_letter_id, request=request)
    letter = matching.review_point(
        db,
        actor=actor,
        point=point,
        decision=body.decision,
        document_id=body.document_id,
        note=body.note,
        request=request,
    )
    return _detail(letter)


@router.post(
    "/query-letters/{letter_id}/confirm",
    response_model=QueryLetterDetail,
    dependencies=[Depends(require_permission(Permission.RESOLVE_REVIEWS))],
)
def confirm_query_letter(
    letter_id: uuid.UUID,
    body: LetterConfirmation,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
):
    """Staff confirm a letter-level concern (e.g. identity mismatch) has been checked."""
    letter = query_service.get_accessible_letter(db, actor, letter_id, request=request)
    return _detail(
        matching.confirm_letter(db, actor=actor, letter=letter, note=body.note, request=request)
    )


@router.post(
    "/query-letters/{letter_id}/reprocess",
    response_model=ReprocessOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_query_letter(
    letter_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    queued = query_service.reprocess_query_letter(
        db, actor=actor, letter_id=letter_id, request=request
    )
    return ReprocessOut(queued=queued)
