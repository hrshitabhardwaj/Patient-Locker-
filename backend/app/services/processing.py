"""Document intelligence pipeline, run by the background worker.

1. document.extract  text layer + OCR, labelled fields, rule classification   (always)
2. document.profile  vision-model profile, verified, combined with the rules  (if an LLM is on)

Each step is idempotent, so a retried job simply redoes its work.
"""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.document_profile import PROMPT_VERSION, profile_document
from app.ai.llm import LLMResponseInvalid, get_llm_client
from app.core.clock import utcnow
from app.core.config import get_settings
from app.models import (
    ClassificationMethod,
    Document,
    DocumentCategory,
    DocumentPage,
    DocumentProfile,
    ProcessingStatus,
    TextMethod,
)
from app.pipeline.jobs import enqueue
from app.processing.classifier import classify_text
from app.processing.extraction import (
    MIN_READABLE_CHARS,
    ExtractedPage,
    ExtractionError,
    extract_pages,
    render_page_images,
)
from app.processing.fields import extract_fields
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.storage import StorageIntegrityError, get_file_store

EXTRACT_JOB = "document.extract"
PROFILE_JOB = "document.profile"


def queue_processing(db: Session, document_id: uuid.UUID) -> bool:
    """Queue extraction in the caller's transaction. False if already pending."""
    return enqueue(
        db, EXTRACT_JOB, {"document_id": str(document_id)}, dedupe_key=f"extract:{document_id}"
    )


def _load(db: Session, payload: dict[str, Any]) -> Document | None:
    document = db.get(Document, uuid.UUID(payload["document_id"]))
    return None if document is None or document.deleted_at is not None else document


def _mark_failed(db: Session, document: Document, code: str) -> None:
    document.processing_status = ProcessingStatus.FAILED
    document.processing_error = code
    document.processed_at = utcnow()
    record_audit(
        db,
        action=AuditAction.DOCUMENT_PROCESSING_FAILED,
        outcome=AuditOutcome.FAILURE,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={"error": code},
    )


def extract_document_text(db: Session, document: Document) -> list[ExtractedPage] | None:
    """Extract and store the text of every page, and set the document's processing status.

    Returns the pages, or None if the file could not be read (the document is then FAILED).
    Commits.
    """
    document.processing_status = ProcessingStatus.PROCESSING
    document.processing_error = None
    db.commit()

    try:
        data = get_file_store().get(document.storage_key, associated_data=document.id.bytes)
        pages = extract_pages(data, document.mime_type)
    except StorageIntegrityError:
        _mark_failed(db, document, "file_integrity_error")
        db.commit()
        return None
    except ExtractionError as exc:
        _mark_failed(db, document, exc.code)
        db.commit()
        return None

    db.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    db.add_all(
        DocumentPage(
            document_id=document.id,
            page_number=page.page_number,
            text=page.text,
            method=page.method,
            ocr_confidence=page.ocr_confidence,
            char_count=page.char_count,
        )
        for page in pages
    )
    readable = sum(page.char_count for page in pages) >= MIN_READABLE_CHARS
    document.processing_status = (
        ProcessingStatus.PROCESSED if readable else ProcessingStatus.UNREADABLE
    )
    document.processing_error = None if readable else "no_readable_text"
    document.processed_at = utcnow()
    return pages


def run_extract(db: Session, payload: dict[str, Any]) -> None:
    document = _load(db, payload)
    if document is None:
        return
    pages = extract_document_text(db, document)
    if pages is None:
        return

    full_text = "\n\n".join(page.text for page in pages)
    rules = classify_text(pages[0].text if pages else "", full_text)
    extracted = extract_fields([(page.page_number, page.text) for page in pages])
    extracted["rule_classification"] = {
        "category": rules.category.value if rules.category else None,
        "confidence": rules.confidence,
        "matched": list(rules.matched)[:10],
    }

    document.extracted_fields = extracted
    document.detected_category = rules.category
    document.classification_confidence = rules.confidence if rules.category else None
    document.classification_method = ClassificationMethod.RULES if rules.category else None

    ocr_confidences = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
    record_audit(
        db,
        action=AuditAction.DOCUMENT_PROCESSED,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={
            "status": document.processing_status.value,
            "pages": len(pages),
            "ocr_pages": sum(1 for p in pages if p.method != TextMethod.TEXT_LAYER),
            "lowest_ocr_confidence": min(ocr_confidences) if ocr_confidences else None,
            "rule_category": extracted["rule_classification"]["category"],
            "rule_confidence": rules.confidence,
            "fields_found": sorted(extracted["fields"]),
            "line_item_count": extracted["line_item_count"],
        },
    )
    if get_llm_client() is not None:
        enqueue(
            db,
            PROFILE_JOB,
            {"document_id": str(document.id)},
            dedupe_key=f"profile:{document.id}",
            max_attempts=4,
        )
    db.commit()


def extract_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    document = _load(db, payload)
    if document is not None and document.processing_status == ProcessingStatus.PROCESSING:
        _mark_failed(db, document, "processing_failed")


def combine_classification(
    rules: dict[str, Any], llm_category: DocumentCategory
) -> tuple[DocumentCategory, float, ClassificationMethod]:
    """Agreement raises confidence; disagreement is flagged and keeps a strong rule result."""
    rule_category = DocumentCategory(rules["category"]) if rules.get("category") else None
    rule_confidence = float(rules.get("confidence") or 0)
    if rule_category is None:
        return llm_category, 0.6, ClassificationMethod.LLM
    if rule_category == llm_category:
        return rule_category, max(rule_confidence, 0.85), ClassificationMethod.RULES_AND_LLM
    chosen = rule_category if rule_confidence >= 0.9 else llm_category
    return chosen, 0.5, ClassificationMethod.CONFLICT


def _profile_row(db: Session, document: Document) -> DocumentProfile:
    profile = db.get(DocumentProfile, document.id)
    if profile is None:
        profile = DocumentProfile(document_id=document.id)
        db.add(profile)
    return profile


def _record_profile_failure(db: Session, document: Document, model: str, error: str) -> None:
    profile = _profile_row(db, document)
    profile.status = "failed"
    profile.model = model
    profile.prompt_version = PROMPT_VERSION
    profile.error = error[:200]
    record_audit(
        db,
        action=AuditAction.DOCUMENT_PROFILE_FAILED,
        outcome=AuditOutcome.FAILURE,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={"model": model, "error": error[:200]},
    )


def run_profile(db: Session, payload: dict[str, Any]) -> None:
    document = _load(db, payload)
    if document is None or document.processing_status not in (
        ProcessingStatus.PROCESSED,
        ProcessingStatus.UNREADABLE,
    ):
        return
    client = get_llm_client()
    if client is None:
        return
    settings = get_settings()

    data = get_file_store().get(document.storage_key, associated_data=document.id.bytes)
    images = render_page_images(
        data,
        document.mime_type,
        max_pages=settings.llm_profile_max_pages,
        max_px=settings.llm_image_max_px,
    )
    pages = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == document.id)
        .order_by(DocumentPage.page_number)
    ).all()
    full_text = "\n\n".join(page.text for page in pages)

    try:
        # LLMUnavailable is not caught: the job is retried later with backoff.
        outcome = profile_document(
            client, full_text=full_text, page_count=document.page_count, images=images
        )
    except LLMResponseInvalid as exc:
        _record_profile_failure(db, document, client.model_name, str(exc))
        db.commit()
        return

    facts = outcome.facts
    rules = document.extracted_fields.get("rule_classification", {})
    category, confidence, method = combine_classification(rules, outcome.document_type)
    facts["classification"] = {
        "rules": rules.get("category"),
        "rules_confidence": rules.get("confidence"),
        "llm": outcome.document_type.value,
    }

    profile = _profile_row(db, document)
    profile.status = "succeeded"
    profile.model = outcome.model
    profile.prompt_version = PROMPT_VERSION
    profile.category = outcome.document_type
    profile.facts = facts
    profile.error = None
    profile.duration_ms = outcome.duration_ms

    document.detected_category = category
    document.classification_confidence = confidence
    document.classification_method = method

    record_audit(
        db,
        action=AuditAction.DOCUMENT_PROFILED,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={
            "model": outcome.model,
            "prompt_version": PROMPT_VERSION,
            "llm_category": outcome.document_type.value,
            "final_category": category.value,
            "method": method.value,
            "confidence": confidence,
            "signature_pages": [s["page"] for s in facts["signatures"]],
            "stamp_pages": [s["page"] for s in facts["stamps"]],
            "pages_unchecked_for_marks": facts["pages_unchecked_for_marks"],
            "unverified_fields": sorted(
                name for name, field in facts["fields"].items() if not field["verified"]
            ),
            "duration_ms": outcome.duration_ms,
        },
    )
    db.commit()


def profile_dead(db: Session, payload: dict[str, Any], error: str) -> None:
    document = _load(db, payload)
    client = get_llm_client()
    if document is not None:
        model = client.model_name if client else "unknown"
        _record_profile_failure(db, document, model, f"llm_unavailable: {error}")
