"""The patient document locker: upload, list, view, download, replace and delete."""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, ServiceError, UnprocessableError
from app.models import (
    AuditLog,
    Document,
    DocumentCategory,
    DocumentKind,
    DocumentPage,
    DocumentProfile,
    Patient,
    ProcessingStatus,
    User,
)
from app.processing.file_validation import UploadRejected, clean_filename, validate_upload
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.patients import get_accessible_patient
from app.services.processing import queue_processing
from app.services.storage import get_file_store

_DOCUMENT_NOT_FOUND = "Document not found."


def get_accessible_document(
    db: Session,
    user: User,
    document_id: uuid.UUID,
    *,
    request: Request,
    include_deleted: bool = False,
) -> Document:
    document = db.get(Document, document_id)
    if document is None or (document.deleted_at is not None and not include_deleted):
        raise NotFoundError(_DOCUMENT_NOT_FOUND)
    try:
        get_accessible_patient(db, user, document.patient_id, request=request)
    except NotFoundError:
        raise NotFoundError(_DOCUMENT_NOT_FOUND) from None
    return document


def list_documents(
    db: Session,
    *,
    actor: User,
    patient_id: uuid.UUID,
    include_history: bool,
    request: Request,
) -> list[Document]:
    get_accessible_patient(db, actor, patient_id, request=request)
    statement = select(Document).where(Document.patient_id == patient_id)
    if not include_history:
        statement = statement.where(
            Document.deleted_at.is_(None), Document.superseded_at.is_(None)
        )
    return list(db.scalars(statement.order_by(Document.uploaded_at.desc(), Document.id)))


def upload_document(
    db: Session,
    *,
    actor: User,
    patient: Patient,
    data: bytes,
    filename: str | None,
    category: DocumentCategory | None,
    replaces_document_id: uuid.UUID | None,
    request: Request,
    kind: DocumentKind = DocumentKind.SUPPORTING,
    before_commit: Callable[[Document], None] | None = None,
) -> Document:
    """Validate, encrypt and store a document for a patient the actor can access.

    Supporting documents are queued for processing. Other kinds pass `before_commit` to add
    their own records and jobs in the same transaction.
    """
    try:
        validated = validate_upload(data)
    except UploadRejected as rejection:
        record_audit(
            db,
            action=AuditAction.DOCUMENT_UPLOAD_REJECTED,
            outcome=AuditOutcome.FAILURE,
            actor_id=actor.id,
            request=request,
            entity_type="patient",
            entity_id=patient.id,
            patient_id=patient.id,
            details={"reason": rejection.code, "size_bytes": len(data)},
        )
        db.commit()
        raise UnprocessableError(rejection.message) from None

    sha256 = hashlib.sha256(data).hexdigest()
    duplicate = db.scalar(
        select(Document).where(
            Document.patient_id == patient.id,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise ConflictError(
            f"This exact file is already in the locker as “{duplicate.original_filename}”."
        )

    replaced = None
    if replaces_document_id is not None:
        replaced = db.get(Document, replaces_document_id)
        if (
            replaced is None
            or replaced.patient_id != patient.id
            or replaced.deleted_at is not None
            or replaced.kind != DocumentKind.SUPPORTING
        ):
            raise ServiceError("The document being replaced is not in this patient's locker.")
        if replaced.superseded_at is not None:
            raise ServiceError("That document has already been replaced by a newer version.")
        replaced.superseded_at = utcnow()

    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        patient_id=patient.id,
        kind=kind,
        declared_category=category,
        original_filename=clean_filename(filename, validated.mime_type),
        mime_type=validated.mime_type,
        size_bytes=len(data),
        sha256=sha256,
        page_count=validated.page_count,
        storage_key=document_id.hex,
        processing_status=ProcessingStatus.UPLOADED,
        supersedes_id=replaced.id if replaced else None,
        uploaded_by_id=actor.id,
    )

    store = get_file_store()
    store.put(document.storage_key, data, associated_data=document_id.bytes)
    try:
        db.add(document)
        db.flush()
        # Same transaction as the upload: if the upload is saved, processing will happen.
        if kind == DocumentKind.SUPPORTING:
            queue_processing(db, document.id)
        if before_commit is not None:
            before_commit(document)
        record_audit(
            db,
            action=AuditAction.DOCUMENT_UPLOADED,
            actor_id=actor.id,
            request=request,
            entity_type="document",
            entity_id=document.id,
            patient_id=patient.id,
            details={
                "kind": kind.value,
                "category": category.value if category else None,
                "mime_type": validated.mime_type,
                "size_bytes": len(data),
                "page_count": validated.page_count,
                "replaces": str(replaced.id) if replaced else None,
            },
        )
        db.commit()
    except IntegrityError:
        # Two identical uploads raced; the other one won.
        db.rollback()
        store.delete(document.storage_key)
        raise ConflictError("This exact file is already in the locker.") from None
    except Exception:
        db.rollback()
        store.delete(document.storage_key)
        raise
    return document


@dataclass
class DocumentContent:
    document: Document
    data: bytes


def read_document_content(
    db: Session, *, actor: User, document_id: uuid.UUID, download: bool, request: Request
) -> DocumentContent:
    document = get_accessible_document(db, actor, document_id, request=request)
    data = get_file_store().get(document.storage_key, associated_data=document.id.bytes)
    record_audit(
        db,
        action=AuditAction.DOCUMENT_DOWNLOADED if download else AuditAction.DOCUMENT_VIEWED,
        actor_id=actor.id,
        request=request,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
    )
    db.commit()
    return DocumentContent(document=document, data=data)


def update_document_category(
    db: Session,
    *,
    actor: User,
    document_id: uuid.UUID,
    category: DocumentCategory | None,
    request: Request,
) -> Document:
    document = get_accessible_document(db, actor, document_id, request=request)
    if document.declared_category == category:
        return document
    previous = document.declared_category
    document.declared_category = category
    record_audit(
        db,
        action=AuditAction.DOCUMENT_UPDATED,
        actor_id=actor.id,
        request=request,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={
            "category_from": previous.value if previous else None,
            "category_to": category.value if category else None,
        },
    )
    db.commit()
    return document


def delete_document(
    db: Session, *, actor: User, document_id: uuid.UUID, request: Request
) -> None:
    """Soft delete: the record and encrypted file are kept for the audit trail."""
    document = get_accessible_document(db, actor, document_id, request=request)
    document.deleted_at = utcnow()
    document.deleted_by_id = actor.id
    record_audit(
        db,
        action=AuditAction.DOCUMENT_DELETED,
        actor_id=actor.id,
        request=request,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
    )
    db.commit()


@dataclass
class DocumentAnalysis:
    document: Document
    pages: list[DocumentPage]
    profile: DocumentProfile | None


def view_analysis(
    db: Session, *, actor: User, document_id: uuid.UUID, request: Request
) -> DocumentAnalysis:
    """Extracted text, rule results and the AI profile. Viewing the text is audited."""
    document = get_accessible_document(db, actor, document_id, request=request)
    pages = list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        )
    )
    record_audit(
        db,
        action=AuditAction.DOCUMENT_ANALYSIS_VIEWED,
        actor_id=actor.id,
        request=request,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
    )
    db.commit()
    return DocumentAnalysis(document, pages, db.get(DocumentProfile, document.id))


def reprocess_document(
    db: Session, *, actor: User, document_id: uuid.UUID, request: Request
) -> bool:
    document = get_accessible_document(db, actor, document_id, request=request)
    queued = queue_processing(db, document.id)
    if queued:
        document.processing_status = ProcessingStatus.UPLOADED
        document.processing_error = None
    record_audit(
        db,
        action=AuditAction.DOCUMENT_REPROCESS_REQUESTED,
        actor_id=actor.id,
        request=request,
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={"queued": queued},
    )
    db.commit()
    return queued


@dataclass
class DocumentHistory:
    versions: list[Document]
    events: list[tuple[AuditLog, str | None]]


def document_history(
    db: Session, *, actor: User, document_id: uuid.UUID, request: Request
) -> DocumentHistory:
    """Every version of this document (oldest first) and everything that happened to them."""
    document = get_accessible_document(
        db, actor, document_id, request=request, include_deleted=True
    )

    oldest = document
    while oldest.supersedes_id is not None:
        oldest = db.get(Document, oldest.supersedes_id)
    versions = [oldest]
    while True:
        newer = db.scalar(select(Document).where(Document.supersedes_id == versions[-1].id))
        if newer is None:
            break
        versions.append(newer)

    events = db.execute(
        select(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(
            AuditLog.entity_type == "document",
            AuditLog.entity_id.in_([str(v.id) for v in versions]),
        )
        .order_by(AuditLog.id)
    ).all()
    return DocumentHistory(versions=versions, events=[(entry, name) for entry, name in events])
