import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status

from app.api.deps import CurrentUser, require_permission
from app.core.config import get_settings
from app.core.errors import UnprocessableError
from app.core.permissions import Permission
from app.db.session import DbSession
from app.models import DocumentCategory
from app.schemas.common import Page
from app.schemas.document import DocumentOut
from app.schemas.patient import (
    AssignmentOut,
    AssignmentsUpdate,
    PatientCreate,
    PatientListItem,
    PatientOut,
    PatientUpdate,
)
from app.services import documents as document_service
from app.services import patients as patient_service

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=Page[PatientListItem])
def list_patients(
    actor: CurrentUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = patient_service.list_patients(db, actor, q=q, limit=limit, offset=offset)
    items = [
        PatientListItem(
            **PatientOut.model_validate(patient).model_dump(),
            document_count=count,
            latest_letter_status=status,
        )
        for patient, count, status in rows
    ]
    return Page[PatientListItem](items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
def create_patient(body: PatientCreate, actor: CurrentUser, request: Request, db: DbSession):
    return patient_service.create_patient(
        db, actor=actor, fields=body.to_fields(), request=request
    )


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    return patient_service.view_patient(db, actor=actor, patient_id=patient_id, request=request)


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(
    patient_id: uuid.UUID,
    body: PatientUpdate,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
):
    return patient_service.update_patient(
        db, actor=actor, patient_id=patient_id, changes=body.to_changes(), request=request
    )


@router.get("/{patient_id}/assignments", response_model=list[AssignmentOut])
def get_assignments(patient_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    rows = patient_service.get_assignments(
        db, actor=actor, patient_id=patient_id, request=request
    )
    return [
        AssignmentOut(
            user_id=user.id,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            assigned_at=assignment.assigned_at,
        )
        for assignment, user in rows
    ]


@router.put(
    "/{patient_id}/assignments",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.ASSIGN_PATIENTS))],
)
def set_assignments(
    patient_id: uuid.UUID,
    body: AssignmentsUpdate,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
) -> None:
    patient_service.set_assignments(
        db, actor=actor, patient_id=patient_id, user_ids=body.user_ids, request=request
    )


@router.get("/{patient_id}/documents", response_model=list[DocumentOut])
def list_documents(
    patient_id: uuid.UUID,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
    include_history: bool = False,
):
    return document_service.list_documents(
        db, actor=actor, patient_id=patient_id, include_history=include_history, request=request
    )


@router.post(
    "/{patient_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
def upload_document(
    patient_id: uuid.UUID,
    actor: CurrentUser,
    request: Request,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    category: Annotated[DocumentCategory | None, Form()] = None,
    replaces_document_id: Annotated[uuid.UUID | None, Form()] = None,
):
    # Check access before reading the file, so refused uploads cost nothing.
    patient = patient_service.get_accessible_patient(db, actor, patient_id, request=request)

    limit = get_settings().max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise UnprocessableError(
            f"The file is larger than the {get_settings().max_upload_mb} MB limit."
        )
    return document_service.upload_document(
        db,
        actor=actor,
        patient=patient,
        data=data,
        filename=file.filename,
        category=category,
        replaces_document_id=replaces_document_id,
        request=request,
    )
