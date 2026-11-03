"""Match every query point of a letter against the patient's locker, and staff review.

Matching runs automatically after extraction. It waits while any of the patient's documents
are still being processed, so decisions are never made on half-read evidence.
"""

import re
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.ai.llm import LLMUnavailable, get_llm_client
from app.ai.match_check import second_opinion
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import NotFoundError, ServiceError
from app.matching.engine import (
    Decision,
    LetterContext,
    LockerDocument,
    PointToMatch,
    decide,
    letter_outcome,
    parse_date,
)
from app.models import (
    Document,
    DocumentKind,
    DocumentPage,
    DocumentProfile,
    Job,
    JobStatus,
    LetterOutcome,
    MatchResult,
    MatchStatus,
    Patient,
    ProcessingStatus,
    QueryLetter,
    QueryLetterStatus,
    QueryPoint,
    User,
)
from app.pipeline.jobs import DeferJob, enqueue
from app.services import processing
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.messages import queue_compose

MATCH_JOB = "query_letter.match"
# Matching re-checks every 15 s while documents are processed; this allows about 20 minutes.
MATCH_MAX_ATTEMPTS = 80
# Letter-level reasons that hold the letter until staff confirm it.
_LETTER_LEVEL_REASONS = {"identity_mismatch", "no_requirements_found", "letter_unreadable"}

_STATUS_FOR_OUTCOME = {
    LetterOutcome.HOLD_FOR_STAFF_REVIEW: QueryLetterStatus.AWAITING_REVIEW,
    LetterOutcome.SEND_MISSING_ITEMS_MESSAGE: QueryLetterStatus.READY_TO_NOTIFY,
    LetterOutcome.NO_ADDITIONAL_DOCUMENTS_REQUIRED: QueryLetterStatus.NO_ACTION_REQUIRED,
}
_ADMISSION = re.compile(
    r"admi(?:ssion|tted)(?:\s*(?:/|and)\s*discharge)?(?:\s+date)?\s*[:\-]?\s*"
    r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
    r"(?:\s*(?:to|-|–)\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}))?",
    re.IGNORECASE,
)


def queue_matching(db: Session, letter_id: uuid.UUID) -> bool:
    return enqueue(
        db,
        MATCH_JOB,
        {"query_letter_id": str(letter_id)},
        dedupe_key=f"match:{letter_id}",
        max_attempts=MATCH_MAX_ATTEMPTS,
    )


# ------------------------------------------------------------------ inputs


def _documents_still_processing(db: Session, patient_id: uuid.UUID) -> bool:
    ids = [
        str(i)
        for i in db.scalars(
            select(Document.id).where(
                Document.patient_id == patient_id,
                Document.kind == DocumentKind.SUPPORTING,
                Document.deleted_at.is_(None),
            )
        )
    ]
    if not ids:
        return False
    unfinished = db.scalar(
        select(
            exists().where(
                Document.id.in_([uuid.UUID(i) for i in ids]),
                Document.processing_status.in_(
                    [ProcessingStatus.UPLOADED, ProcessingStatus.PROCESSING]
                ),
            )
        )
    )
    pending_jobs = db.scalar(
        select(
            exists().where(
                Job.kind.in_([processing.EXTRACT_JOB, processing.PROFILE_JOB]),
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                Job.payload["document_id"].astext.in_(ids),
            )
        )
    )
    return bool(unfinished or pending_jobs)


def load_locker(db: Session, patient_id: uuid.UUID) -> list[LockerDocument]:
    documents = db.scalars(
        select(Document)
        .where(
            Document.patient_id == patient_id,
            Document.kind == DocumentKind.SUPPORTING,
            Document.deleted_at.is_(None),
            Document.superseded_at.is_(None),
        )
        .order_by(Document.uploaded_at)
    ).all()
    locker = []
    for document in documents:
        pages = db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        ).all()
        profile = db.get(DocumentProfile, document.id)
        extracted = document.extracted_fields or {}
        locker.append(
            LockerDocument(
                id=document.id,
                filename=document.original_filename,
                status=document.processing_status,
                declared_category=document.declared_category,
                detected_category=document.detected_category,
                classification_method=document.classification_method,
                pages=[(p.page_number, p.text, p.ocr_confidence) for p in pages],
                fields=extracted.get("fields", {}),
                line_item_count=extracted.get("line_item_count", 0),
                profile=profile.facts if profile and profile.status == "succeeded" else None,
            )
        )
    return locker


def letter_context(db: Session, letter: QueryLetter, patient: Patient) -> LetterContext:
    text = "\n".join(
        db.scalars(
            select(DocumentPage.text)
            .where(DocumentPage.document_id == letter.document_id)
            .order_by(DocumentPage.page_number)
        )
    )
    admission = discharge = None
    found = _ADMISSION.search(text)
    if found:
        admission = parse_date(found.group(1))
        discharge = parse_date(found.group(2)) if found.group(2) else None
    return LetterContext(patient_name=patient.full_name, admission=admission, discharge=discharge)


def _point_input(point: QueryPoint) -> PointToMatch:
    return PointToMatch(
        requirement=point.requirement,
        requirement_type=point.requirement_type,
        conditions=point.conditions,
        source_text=point.source_text,
        needs_review=point.needs_review,
        review_reason=point.review_reason,
    )


# ------------------------------------------------------------------ outcome


def _letter_level_reasons(letter: QueryLetter) -> list[str]:
    return [r for r in letter.review_reasons if r in _LETTER_LEVEL_REASONS]


def refresh_outcome(letter: QueryLetter) -> LetterOutcome:
    """Recompute the letter's outcome from the current decision on each live point."""
    statuses = []
    for point in letter.points:
        if point.dismissed_at is not None:
            continue
        match = point.current_match
        statuses.append(match.status if match else MatchStatus.NEEDS_REVIEW)
    outcome = LetterOutcome(letter_outcome(statuses, bool(_letter_level_reasons(letter))))
    letter.outcome = outcome
    letter.status = _STATUS_FOR_OUTCOME[outcome]
    return outcome


def _supersede(point: QueryPoint) -> None:
    now = utcnow()
    for match in point.matches:
        if match.superseded_at is None:
            match.superseded_at = now


# ------------------------------------------------------------------ background job


def run_match(db: Session, payload: dict[str, Any]) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is None or letter.status in (
        QueryLetterStatus.RECEIVED,
        QueryLetterStatus.EXTRACTING,
        QueryLetterStatus.FAILED,
    ):
        return
    if _documents_still_processing(db, letter.patient_id):
        raise DeferJob("waiting for the patient's documents to finish processing")

    started = utcnow()
    letter.status = QueryLetterStatus.MATCHING
    db.commit()

    patient = db.get(Patient, letter.patient_id)
    locker = load_locker(db, letter.patient_id)
    context = letter_context(db, letter, patient)
    client = get_llm_client() if get_settings().match_model_check else None
    by_id = {str(d.id): d for d in locker}

    summary = []
    for point in letter.points:
        if point.dismissed_at is not None:
            continue
        current = point.current_match
        if current is not None and current.decided_by == "staff":
            summary.append({"seq": point.seq, "status": current.status.value, "by": "staff"})
            continue

        decision: Decision = decide(_point_input(point), locker, context)
        opinion = None
        if decision.status == MatchStatus.COVERED and client is not None:
            document_ids = {e["document_id"] for e in decision.evidence}
            text = "\n\n".join(by_id[i].text for i in document_ids if i in by_id)
            try:
                opinion = second_opinion(client, requirement=point.requirement, document_text=text)
            except LLMUnavailable:
                client = None  # carry on with rules only for the rest of the letter
            if opinion and opinion["satisfies"] == "no":
                decision = Decision(
                    MatchStatus.NEEDS_REVIEW,
                    decision.explanation
                    + f" However, the AI check disagreed: {opinion['reason']}",
                    decision.evidence,
                    decision.checks,
                )

        _supersede(point)
        db.add(
            MatchResult(
                query_point=point,
                status=decision.status,
                explanation=decision.explanation,
                evidence=decision.evidence,
                condition_checks=decision.checks,
                model_opinion=opinion,
                decided_by="system",
            )
        )
        summary.append(
            {
                "seq": point.seq,
                "status": decision.status.value,
                "documents": sorted({e["document_id"] for e in decision.evidence}),
                "model": opinion["satisfies"] if opinion else None,
            }
        )

    db.flush()
    db.refresh(letter)
    outcome = refresh_outcome(letter)
    letter.matched_at = utcnow()
    queue_compose(db, letter)
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_MATCHED,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={
            "outcome": outcome.value,
            "points": summary,
            "locker_documents": len(locker),
            "letter_level_reasons": _letter_level_reasons(letter),
            "seconds": round((utcnow() - started).total_seconds(), 1),
        },
    )
    db.commit()


def match_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is None:
        return
    letter.review_reasons = sorted({*letter.review_reasons, "matching_failed"})
    letter.outcome = LetterOutcome.HOLD_FOR_STAFF_REVIEW
    letter.status = QueryLetterStatus.AWAITING_REVIEW
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_MATCH_FAILED,
        outcome=AuditOutcome.FAILURE,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={"error": error[:200]},
    )


# ------------------------------------------------------------------ staff review

REVIEW_DECISIONS = ("covered", "needs_from_patient", "not_a_requirement")


def review_point(
    db: Session,
    *,
    actor: User,
    point: QueryPoint,
    decision: str,
    document_id: uuid.UUID | None,
    note: str,
    request: Request,
) -> QueryLetter:
    letter = point.query_letter
    if letter.status in (QueryLetterStatus.RECEIVED, QueryLetterStatus.EXTRACTING):
        raise ServiceError("This letter is still being read; try again shortly.")

    evidence = []
    if decision == "covered":
        document = db.get(Document, document_id) if document_id else None
        if (
            document is None
            or document.patient_id != letter.patient_id
            or document.kind != DocumentKind.SUPPORTING
            or document.deleted_at is not None
        ):
            raise ServiceError("Choose the locker document that covers this requirement.")
        evidence = [
            {
                "document_id": str(document.id),
                "filename": document.original_filename,
                "source": "staff",
            }
        ]

    _supersede(point)
    if decision == "not_a_requirement":
        point.dismissed_at = utcnow()
        point.dismissed_by_id = actor.id
    else:
        point.dismissed_at = None
        db.add(
            MatchResult(
                query_point=point,
                status=MatchStatus(decision),
                explanation=f"Decided by staff: {note}",
                evidence=evidence,
                decided_by="staff",
                reviewer_id=actor.id,
                review_note=note,
            )
        )
    db.flush()
    db.refresh(letter)
    outcome = refresh_outcome(letter)
    queue_compose(db, letter)
    record_audit(
        db,
        action=AuditAction.QUERY_POINT_REVIEWED,
        actor_id=actor.id,
        request=request,
        entity_type="query_point",
        entity_id=point.id,
        patient_id=letter.patient_id,
        details={
            "decision": decision,
            "document_id": str(document_id) if decision == "covered" else None,
            "letter_outcome": outcome.value,
        },
    )
    db.commit()
    return letter


def confirm_letter(
    db: Session, *, actor: User, letter: QueryLetter, note: str, request: Request
) -> QueryLetter:
    """Staff confirm letter-level concerns (e.g. identity mismatch) were checked and are fine."""
    cleared = _letter_level_reasons(letter)
    if not cleared:
        raise ServiceError("This letter has no letter-level issue to confirm.")
    letter.review_reasons = [r for r in letter.review_reasons if r not in _LETTER_LEVEL_REASONS]
    letter.needs_review = bool(letter.review_reasons)
    outcome = refresh_outcome(letter)
    queue_compose(db, letter)
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_CONFIRMED,
        actor_id=actor.id,
        request=request,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={"cleared": cleared, "letter_outcome": outcome.value},
    )
    db.commit()
    return letter


def get_point(db: Session, point_id: uuid.UUID) -> QueryPoint:
    point = db.get(QueryPoint, point_id)
    if point is None:
        raise NotFoundError("Query point not found.")
    return point
