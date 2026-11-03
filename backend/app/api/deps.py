"""Request guards: who is signed in, CSRF, and role permissions."""

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.core.permissions import Permission, has_permission
from app.core.security import CSRF_HEADER, SESSION_COOKIE, hash_token
from app.db.session import DbSession
from app.models import User, UserSession
from app.services.audit import AuditAction, AuditOutcome, record_audit
from app.services.auth import resolve_session

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Declared as a security scheme so the interactive API docs offer an "Authorize" box for it.
_csrf_header = APIKeyHeader(
    name=CSRF_HEADER,
    auto_error=False,
    description="Paste the csrf_token returned by POST /auth/login.",
)


def get_current_session(
    request: Request,
    db: DbSession,
    csrf_token: Annotated[str | None, Security(_csrf_header)] = None,
) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    session = resolve_session(db, token) if token else None
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")

    if request.method not in _SAFE_METHODS:
        csrf_token = csrf_token or ""
        if not csrf_token or not hmac.compare_digest(
            hash_token(csrf_token), session.csrf_token_hash
        ):
            record_audit(
                db,
                action=AuditAction.CSRF_REJECTED,
                outcome=AuditOutcome.DENIED,
                actor_id=session.user_id,
                request=request,
                details={"method": request.method, "path": request.url.path},
            )
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")

    request.state.user_id = session.user_id
    return session


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


def get_current_user(session: CurrentSession) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_password_current(user: CurrentUser) -> User:
    """Blocks everything except the auth routes until a temporary password is replaced."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required.")
    return user


def require_permission(permission: Permission) -> Callable[..., User]:
    def check(user: CurrentUser, request: Request, db: DbSession) -> User:
        if not has_permission(user.role, permission):
            record_audit(
                db,
                action=AuditAction.ACCESS_DENIED,
                outcome=AuditOutcome.DENIED,
                actor_id=user.id,
                request=request,
                details={
                    "permission": permission.value,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this.")
        return user

    return check
