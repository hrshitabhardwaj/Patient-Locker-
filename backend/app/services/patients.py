"""Patient profiles and patient-level access control.

Access rule: a user may see a patient if their role has ACCESS_ALL_PATIENTS, or if they are
assigned to that patient. Anyone else gets "not found", so patient existence is not revealed.
"""

import re
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ServiceError
from app.core.permissions import Permission, has_permission
from app.models import Document, DocumentKind, Patient, PatientAssignment, QueryLetter, User
from app.models.patient import PATIENT_CODE_SEQUENCE
from app.services.audit import AuditAction, AuditOutcome, record_audit

_PATIENT_NOT_FOUND = "Patient not found."


def can_access_patient(db: Session, user: User, patient_id: uuid.UUID) -> bool:
    if has_permission(user.role, Permission.ACCESS_ALL_PATIENTS):
        return True
    return bool(
        db.scalar(
            select(
                exists().where(
                    PatientAssignment.patient_id == patient_id,
                    PatientAssignment.user_id == user.id,
                )
            )
        )
    )


def get_accessible_patient(
    db: Session, user: User, patient_id: uuid.UUID, *, request: Request
) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise NotFoundError(_PATIENT_NOT_FOUND)
    if not can_access_patient(db, user, patient.id):
        record_audit(
            db,
            action=AuditAction.ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            actor_id=user.id,
            request=request,
            entity_type="patient",
            entity_id=patient.id,
            patient_id=patient.id,
            details={"reason": "not_assigned", "path": request.url.path},
        )
        db.commit()
        raise NotFoundError(_PATIENT_NOT_FOUND)
    return patient


def _accessible_patients(user: User) -> Select:
    statement = select(Patient)
    if not has_permission(user.role, Permission.ACCESS_ALL_PATIENTS):
        statement = statement.join(
            PatientAssignment,
            (PatientAssignment.patient_id == Patient.id) & (PatientAssignment.user_id == user.id),
        )
    return statement


def list_patients(
    db: Session, user: User, *, q: str | None, limit: int, offset: int
) -> tuple[list[tuple[Patient, int, str | None]], int]:
    """Accessible patients with their document count and latest query letter status."""
    statement = _accessible_patients(user)
    if q and q.strip():
        term = q.strip()
        pattern = f"%{term}%"
        conditions = [
            Patient.full_name.ilike(pattern),
            Patient.patient_code.ilike(pattern),
            Patient.email.ilike(pattern),
            Patient.policy_number.ilike(pattern),
            Patient.claim_number.ilike(pattern),
        ]
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 4:
            conditions.append(Patient.phone_e164.contains(digits))
        statement = statement.where(or_(*conditions))

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0

    document_count = (
        select(func.count(Document.id))
        .where(
            Document.patient_id == Patient.id,
            Document.kind == DocumentKind.SUPPORTING,
            Document.deleted_at.is_(None),
            Document.superseded_at.is_(None),
        )
        .correlate(Patient)
        .scalar_subquery()
    )
    latest_letter = (
        select(QueryLetter.status)
        .where(QueryLetter.patient_id == Patient.id)
        .order_by(QueryLetter.created_at.desc())
        .limit(1)
        .correlate(Patient)
        .scalar_subquery()
    )
    rows = db.execute(
        statement.add_columns(document_count, latest_letter)
        .order_by(Patient.created_at.desc(), Patient.patient_code.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [(patient, count, status) for patient, count, status in rows], total


def create_patient(
    db: Session, *, actor: User, fields: dict[str, Any], request: Request
) -> Patient:
    number = db.scalar(select(PATIENT_CODE_SEQUENCE.next_value()))
    patient = Patient(patient_code=f"PL-{number:06d}", created_by_id=actor.id, **fields)
    db.add(patient)
    db.flush()

    # Staff who create a patient are assigned to them, or they could not open the profile.
    if not has_permission(actor.role, Permission.ACCESS_ALL_PATIENTS):
        db.add(PatientAssignment(patient_id=patient.id, user_id=actor.id, assigned_by_id=actor.id))

    record_audit(
        db,
        action=AuditAction.PATIENT_CREATED,
        actor_id=actor.id,
        request=request,
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"patient_code": patient.patient_code},
    )
    db.commit()
    return patient


def view_patient(db: Session, *, actor: User, patient_id: uuid.UUID, request: Request) -> Patient:
    patient = get_accessible_patient(db, actor, patient_id, request=request)
    record_audit(
        db,
        action=AuditAction.PATIENT_VIEWED,
        actor_id=actor.id,
        request=request,
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
    )
    db.commit()
    return patient


def update_patient(
    db: Session, *, actor: User, patient_id: uuid.UUID, changes: dict[str, Any], request: Request
) -> Patient:
    patient = get_accessible_patient(db, actor, patient_id, request=request)
    changed = sorted(field for field, value in changes.items() if getattr(patient, field) != value)
    if not changed:
        return patient
    for field in changed:
        setattr(patient, field, changes[field])

    # Field names only: the old and new values are personal data.
    record_audit(
        db,
        action=AuditAction.PATIENT_UPDATED,
        actor_id=actor.id,
        request=request,
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"changed": changed},
    )
    db.commit()
    return patient


def get_assignments(
    db: Session, *, actor: User, patient_id: uuid.UUID, request: Request
) -> list[tuple[PatientAssignment, User]]:
    get_accessible_patient(db, actor, patient_id, request=request)
    rows = db.execute(
        select(PatientAssignment, User)
        .join(User, User.id == PatientAssignment.user_id)
        .where(PatientAssignment.patient_id == patient_id)
        .order_by(User.full_name)
    ).all()
    return [(assignment, user) for assignment, user in rows]


def set_assignments(
    db: Session,
    *,
    actor: User,
    patient_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    request: Request,
) -> None:
    get_accessible_patient(db, actor, patient_id, request=request)
    wanted = set(user_ids)
    if wanted:
        active = set(
            db.scalars(select(User.id).where(User.id.in_(wanted), User.is_active.is_(True)))
        )
        if active != wanted:
            raise ServiceError("Every assigned user must be an existing, active staff member.")

    current = set(
        db.scalars(
            select(PatientAssignment.user_id).where(PatientAssignment.patient_id == patient_id)
        )
    )
    added, removed = wanted - current, current - wanted
    if not added and not removed:
        return

    for user_id in removed:
        db.delete(db.get(PatientAssignment, (patient_id, user_id)))
    for user_id in added:
        db.add(
            PatientAssignment(patient_id=patient_id, user_id=user_id, assigned_by_id=actor.id)
        )
    record_audit(
        db,
        action=AuditAction.PATIENT_ASSIGNMENTS_CHANGED,
        actor_id=actor.id,
        request=request,
        entity_type="patient",
        entity_id=patient_id,
        patient_id=patient_id,
        details={"added": sorted(map(str, added)), "removed": sorted(map(str, removed))},
    )
    db.commit()
