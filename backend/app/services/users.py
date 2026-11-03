"""Staff account management (admin only)."""

import uuid

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ServiceError
from app.core.permissions import Role
from app.core.security import generate_temporary_password, hash_password
from app.models import User
from app.services.audit import AuditAction, record_audit
from app.services.auth import normalize_email, revoke_all_sessions


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


def list_users(
    db: Session,
    *,
    q: str | None,
    role: Role | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[User], int]:
    statement = select(User)
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))
    if role is not None:
        statement = statement.where(User.role == role)
    if is_active is not None:
        statement = statement.where(User.is_active == is_active)

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.order_by(User.full_name).limit(limit).offset(offset)).all()
    return list(items), total


def create_user(
    db: Session, *, actor: User, email: str, full_name: str, role: Role, request: Request
) -> tuple[User, str]:
    """Create an account with a one-time temporary password the user must change on first login."""
    email = normalize_email(email)
    temporary_password = generate_temporary_password(email)
    user = User(
        email=email,
        full_name=full_name,
        role=role,
        password_hash=hash_password(temporary_password),
        must_change_password=True,
        created_by_id=actor.id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError("A user with this email already exists.") from None

    record_audit(
        db,
        action=AuditAction.USER_CREATED,
        actor_id=actor.id,
        request=request,
        entity_type="user",
        entity_id=user.id,
        details={"role": role.value},
    )
    db.commit()
    return user, temporary_password


def update_user(
    db: Session,
    *,
    actor: User,
    user_id: uuid.UUID,
    full_name: str | None,
    role: Role | None,
    is_active: bool | None,
    request: Request,
) -> User:
    user = get_user(db, user_id)
    if user.id == actor.id and (is_active is False or (role is not None and role != user.role)):
        raise ServiceError("You cannot deactivate your own account or change your own role.")

    changed = []
    for field, value in (("full_name", full_name), ("role", role), ("is_active", is_active)):
        if value is not None and value != getattr(user, field):
            setattr(user, field, value)
            changed.append(field)
    if not changed:
        return user

    if "is_active" in changed and not user.is_active:
        revoke_all_sessions(db, user.id)

    record_audit(
        db,
        action=AuditAction.USER_UPDATED,
        actor_id=actor.id,
        request=request,
        entity_type="user",
        entity_id=user.id,
        details={"changed": changed, "role": user.role.value, "is_active": user.is_active},
    )
    db.commit()
    return user


def reset_password(db: Session, *, actor: User, user_id: uuid.UUID, request: Request) -> str:
    user = get_user(db, user_id)
    temporary_password = generate_temporary_password(user.email)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    revoke_all_sessions(db, user.id)
    record_audit(
        db,
        action=AuditAction.USER_PASSWORD_RESET,
        actor_id=actor.id,
        request=request,
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    return temporary_password
