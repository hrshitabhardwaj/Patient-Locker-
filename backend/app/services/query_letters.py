"""Insurer query letters: upload, automatic extraction of query points, and viewing.

Uploading a letter queues its extraction in the same transaction, so every saved letter is
processed without anyone pressing a button.
"""

import re
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.llm import get_llm_client
from app.ai.query_extraction import PROMPT_VERSION, extract_points
from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.models import (
    Document,
    DocumentKind,
    IdentityStatus,
    Patient,
    ProcessingStatus,
    QueryLetter,
    QueryLetterStatus,
    QueryPoint,
    User,
)
from app.pipeline.jobs import enqueue
from app.processing.fields import extract_fields
from app.processing.letter_header import response_days, sender_name
from app.processing.query_segments import general_conditions, segment_letter
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.documents import upload_document
from app.services.patients import get_accessible_patient
from app.services.processing import extract_document_text

EXTRACT_QUERY_JOB = "query_letter.extract"
_NOT_FOUND = "Query letter not found."


# ---------------------------------------------------------------- identity check

_CLAIM_REF = re.compile(
    r"\bclaim\s*(?:no\.?|number|ref(?:erence)?(?:\s+no\.?)?|id)?\s*[:.#-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-]{3,39})",
    re.IGNORECASE,
)
_POLICY_REF = re.compile(
    r"\bpolicy\s*(?:no\.?|number)\s*[:.#-]?\s*([A-Z0-9][A-Z0-9/\-]{3,39})", re.IGNORECASE
)
_NAME_LINE = re.compile(
    r"^\s*(?:patient(?:'s)?\s+name|name\s+of\s+(?:the\s+)?patient|insured(?:\s+(?:name|person))?"
    r"|patient)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "master", "kumari", "sri"}


def _reference_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", name.lower()) if t not in _TITLES}


def check_identity(patient: Patient, letter_text: str) -> tuple[IdentityStatus, dict[str, Any]]:
    """Compare the claim number, policy number and name in the letter with the profile."""
    details: dict[str, Any] = {}

    def compare(field: str, on_record: str | None, in_letter: list[str]) -> None:
        in_letter = [v for v in dict.fromkeys(in_letter) if re.search(r"\d", v)]
        if not in_letter:
            return
        if not on_record:
            details[field] = {"result": "not_on_record"}
            return
        matched = _reference_key(on_record) in {_reference_key(v) for v in in_letter}
        details[field] = {"result": "match" if matched else "mismatch"}

    compare("claim_number", patient.claim_number, _CLAIM_REF.findall(letter_text))
    compare("policy_number", patient.policy_number, _POLICY_REF.findall(letter_text))

    names = [_name_tokens(n) for n in _NAME_LINE.findall(letter_text)]
    names = [n for n in names if n]
    if names:
        on_record = _name_tokens(patient.full_name)
        matched = any(n <= on_record or on_record <= n for n in names)
        details["patient_name"] = {"result": "match" if matched else "mismatch"}

    # Details record only match / mismatch, never the values themselves.
    results = {entry["result"] for entry in details.values()}
    if "mismatch" in results:
        return IdentityStatus.MISMATCH, details
    if "match" in results:
        return IdentityStatus.MATCH, details
    return IdentityStatus.UNKNOWN, details


# ---------------------------------------------------------------- staff actions


def upload_query_letter(
    db: Session,
    *,
    actor: User,
    patient: Patient,
    data: bytes,
    filename: str | None,
    request: Request,
) -> QueryLetter:
    letter: QueryLetter | None = None

    def create_letter(document: Document) -> None:
        nonlocal letter
        letter = QueryLetter(
            id=uuid.uuid4(),
            patient_id=patient.id,
            document_id=document.id,
            uploaded_by_id=actor.id,
        )
        db.add(letter)
        db.flush()
        enqueue(
            db,
            EXTRACT_QUERY_JOB,
            {"query_letter_id": str(letter.id)},
            dedupe_key=f"query:{letter.id}",
        )
        record_audit(
            db,
            action=AuditAction.QUERY_LETTER_UPLOADED,
            actor_id=actor.id,
            request=request,
            entity_type="query_letter",
            entity_id=letter.id,
            patient_id=patient.id,
            details={"document_id": str(document.id)},
        )

    upload_document(
        db,
        actor=actor,
        patient=patient,
        data=data,
        filename=filename,
        category=None,
        replaces_document_id=None,
        request=request,
        kind=DocumentKind.QUERY_LETTER,
        before_commit=create_letter,
    )
    assert letter is not None
    return letter


def get_accessible_letter(
    db: Session, user: User, letter_id: uuid.UUID, *, request: Request
) -> QueryLetter:
    letter = db.get(QueryLetter, letter_id)
    if letter is None:
        raise NotFoundError(_NOT_FOUND)
    try:
        get_accessible_patient(db, user, letter.patient_id, request=request)
    except NotFoundError:
        raise NotFoundError(_NOT_FOUND) from None
    return letter


def list_query_letters(
    db: Session, *, actor: User, patient_id: uuid.UUID, request: Request
) -> list[QueryLetter]:
    get_accessible_patient(db, actor, patient_id, request=request)
    return list(
        db.scalars(
            select(QueryLetter)
            .where(QueryLetter.patient_id == patient_id)
            .order_by(QueryLetter.created_at.desc())
        )
    )


def view_query_letter(
    db: Session, *, actor: User, letter_id: uuid.UUID, request: Request
) -> QueryLetter:
    letter = get_accessible_letter(db, actor, letter_id, request=request)
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_VIEWED,
        actor_id=actor.id,
        request=request,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
    )
    db.commit()
    return letter


def reprocess_query_letter(
    db: Session, *, actor: User, letter_id: uuid.UUID, request: Request
) -> bool:
    letter = get_accessible_letter(db, actor, letter_id, request=request)
    queued = enqueue(
        db,
        EXTRACT_QUERY_JOB,
        {"query_letter_id": str(letter.id)},
        dedupe_key=f"query:{letter.id}",
    )
    if queued:
        letter.status = QueryLetterStatus.RECEIVED
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_REPROCESS_REQUESTED,
        actor_id=actor.id,
        request=request,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={"queued": queued},
    )
    db.commit()
    return queued


# ---------------------------------------------------------------- background job


def _fail(db: Session, letter: QueryLetter, code: str) -> None:
    letter.status = QueryLetterStatus.FAILED
    letter.error = code
    letter.needs_review = True
    letter.review_reasons = [code]
    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_FAILED,
        outcome=AuditOutcome.FAILURE,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={"error": code},
    )


def run_query_extract(db: Session, payload: dict[str, Any]) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is None or letter.document.deleted_at is not None:
        return
    started = utcnow()
    letter.status = QueryLetterStatus.EXTRACTING
    letter.error = None
    db.commit()

    pages = extract_document_text(db, letter.document)
    if pages is None or letter.document.processing_status != ProcessingStatus.PROCESSED:
        _fail(db, letter, "letter_unreadable")
        db.commit()
        return

    text = "\n\n".join(page.text for page in pages)
    segments = segment_letter(text)
    client = get_llm_client()
    points, warnings, method = extract_points(
        [(s.index, s.text) for s in segments if s.used], client
    )
    identity, identity_details = check_identity(db.get(Patient, letter.patient_id), text)
    fields = extract_fields([(page.page_number, page.text) for page in pages])["fields"]

    db.execute(delete(QueryPoint).where(QueryPoint.query_letter_id == letter.id))
    db.add_all(
        QueryPoint(
            query_letter_id=letter.id,
            seq=seq,
            requirement=point.requirement[:300],
            requirement_type=point.requirement_type,
            conditions=point.conditions,
            source_text=point.source_text,
            segment_index=point.segment_index,
            source=point.source,
            needs_review=point.needs_review,
            review_reason=point.review_reason,
        )
        for seq, point in enumerate(points, start=1)
    )

    reasons = []
    if identity == IdentityStatus.MISMATCH:
        reasons.append("identity_mismatch")
    if not points:
        reasons.append("no_requirements_found")
    if any(point.needs_review for point in points):
        reasons.append("points_need_review")

    letter.status = QueryLetterStatus.EXTRACTED
    letter.identity_status = identity
    letter.identity_details = identity_details
    letter.letter_date = (fields.get("document_date") or {}).get("value")
    claim_refs = [ref for ref in _CLAIM_REF.findall(text) if re.search(r"\d", ref)]
    letter.claim_reference = claim_refs[0] if claim_refs else None
    letter.insurer_name = sender_name(pages[0].text) if pages else None
    letter.response_days = response_days(text)
    letter.needs_review = bool(reasons)
    letter.review_reasons = reasons
    letter.extracted_at = utcnow()
    letter.extraction_details = {
        "method": method,
        "model": client.model_name if client and method != "rules" else None,
        "prompt_version": PROMPT_VERSION,
        "segments": [s.as_dict() for s in segments],
        "general_conditions": general_conditions(segments),
        "warnings": warnings,
    }

    # Matching follows automatically, in the same transaction.
    if points:
        from app.services.matching import queue_matching

        queue_matching(db, letter.id)

    record_audit(
        db,
        action=AuditAction.QUERY_LETTER_EXTRACTED,
        entity_type="query_letter",
        entity_id=letter.id,
        patient_id=letter.patient_id,
        details={
            "points": len(points),
            "requirement_types": [p.requirement_type.value for p in points],
            "points_needing_review": sum(1 for p in points if p.needs_review),
            "identity": identity.value,
            "identity_checks": {k: v["result"] for k, v in identity_details.items()},
            "review_reasons": reasons,
            "method": method,
            "segments_used": sum(1 for s in segments if s.used),
            "segments_skipped": sum(1 for s in segments if not s.used),
            "seconds": round((utcnow() - started).total_seconds(), 1),
        },
    )
    db.commit()


def query_extract_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    letter = db.get(QueryLetter, uuid.UUID(payload["query_letter_id"]))
    if letter is not None and letter.status != QueryLetterStatus.EXTRACTED:
        _fail(db, letter, "extraction_failed")
