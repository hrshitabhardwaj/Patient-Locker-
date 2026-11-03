import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, require_permission
from app.core.permissions import Permission
from app.db.session import DbSession
from app.schemas.document import (
    DocumentAnalysisOut,
    DocumentCategoryUpdate,
    DocumentEvent,
    DocumentHistoryOut,
    DocumentOut,
    PageTextOut,
    ProfileOut,
    ReprocessOut,
)
from app.services import documents as document_service

router = APIRouter(prefix="/documents", tags=["documents"])


def _content_disposition(disposition: str, filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "replace").decode().replace('"', "'")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    return document_service.get_accessible_document(db, actor, document_id, request=request)


@router.get("/{document_id}/content")
def get_document_content(
    document_id: uuid.UUID,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
    download: bool = False,
) -> Response:
    content = document_service.read_document_content(
        db, actor=actor, document_id=document_id, download=download, request=request
    )
    document = content.document
    headers = {
        "Content-Disposition": _content_disposition(
            "attachment" if download else "inline", document.original_filename
        ),
    }
    # Images open in a sandbox so nothing in the file can run. Chromium's built-in PDF
    # viewer can refuse to render under a sandbox policy, so PDFs rely on nosniff and the
    # browser viewer's own isolation instead.
    if document.mime_type.startswith("image/"):
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
    return Response(content=content.data, media_type=document.mime_type, headers=headers)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: uuid.UUID,
    body: DocumentCategoryUpdate,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
):
    return document_service.update_document_category(
        db,
        actor=actor,
        document_id=document_id,
        category=body.declared_category,
        request=request,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.DELETE_DOCUMENTS))],
)
def delete_document(
    document_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
) -> None:
    document_service.delete_document(db, actor=actor, document_id=document_id, request=request)


@router.get("/{document_id}/analysis", response_model=DocumentAnalysisOut)
def get_document_analysis(
    document_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    analysis = document_service.view_analysis(
        db, actor=actor, document_id=document_id, request=request
    )
    return DocumentAnalysisOut(
        document=DocumentOut.model_validate(analysis.document),
        extracted_fields=analysis.document.extracted_fields,
        pages=[PageTextOut.model_validate(page) for page in analysis.pages],
        profile=ProfileOut.model_validate(analysis.profile) if analysis.profile else None,
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=ReprocessOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    document_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    queued = document_service.reprocess_document(
        db, actor=actor, document_id=document_id, request=request
    )
    return ReprocessOut(queued=queued)


@router.get("/{document_id}/history", response_model=DocumentHistoryOut)
def get_document_history(
    document_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession
):
    history = document_service.document_history(
        db, actor=actor, document_id=document_id, request=request
    )
    return DocumentHistoryOut(
        versions=[DocumentOut.model_validate(v) for v in history.versions],
        events=[
            DocumentEvent(
                occurred_at=entry.occurred_at,
                action=entry.action,
                outcome=entry.outcome,
                document_id=uuid.UUID(entry.entity_id),
                actor_user_id=entry.actor_user_id,
                actor_name=actor_name,
                details=entry.details,
            )
            for entry, actor_name in history.events
        ],
    )
