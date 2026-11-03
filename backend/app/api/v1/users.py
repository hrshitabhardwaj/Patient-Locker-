import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, require_permission
from app.core.permissions import Permission, Role
from app.db.session import DbSession
from app.models import User
from app.schemas.common import Page
from app.schemas.patient import StaffDirectoryEntry
from app.schemas.user import (
    PasswordResetResponse,
    UserCreate,
    UserCreatedResponse,
    UserOut,
    UserUpdate,
)
from app.services import users as user_service

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)

# Names and roles of active staff, for whoever assigns patients. No emails or login details.
directory_router = APIRouter(
    prefix="/staff-directory",
    tags=["users"],
    dependencies=[Depends(require_permission(Permission.ASSIGN_PATIENTS))],
)


@directory_router.get("", response_model=list[StaffDirectoryEntry])
def staff_directory(db: DbSession):
    return db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)).all()


@router.get("", response_model=Page[UserOut])
def list_users(
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Role | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items, total = user_service.list_users(
        db, q=q, role=role, is_active=is_active, limit=limit, offset=offset
    )
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in items], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=UserCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, actor: CurrentUser, request: Request, db: DbSession):
    user, temporary_password = user_service.create_user(
        db,
        actor=actor,
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        request=request,
    )
    return UserCreatedResponse(
        user=UserOut.model_validate(user), temporary_password=temporary_password
    )


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: DbSession):
    return user_service.get_user(db, user_id)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID, body: UserUpdate, actor: CurrentUser, request: Request, db: DbSession
):
    return user_service.update_user(
        db,
        actor=actor,
        user_id=user_id,
        full_name=body.full_name,
        role=body.role,
        is_active=body.is_active,
        request=request,
    )


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(user_id: uuid.UUID, actor: CurrentUser, request: Request, db: DbSession):
    temporary_password = user_service.reset_password(
        db, actor=actor, user_id=user_id, request=request
    )
    return PasswordResetResponse(temporary_password=temporary_password)
