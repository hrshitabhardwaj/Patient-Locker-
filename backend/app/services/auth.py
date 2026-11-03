"""Login, sessions and password changes."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import ServiceError
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.models import AuditLog, User, UserSession
from app.services.audit import AuditAction, AuditOutcome, record_audit

# last_seen_at is refreshed at most this often, to avoid a write on every request.
_LAST_SEEN_RESOLUTION = timedelta(minutes=1)


class LoginError(Exception):
    """Raised for every failed login. The public message never says which check failed."""


class TooManyLoginAttempts(Exception):
    """Too many failed sign-ins from one network address; it must wait."""

    def __init__(self, retry_after_seconds: int):
        super().__init__("too many attempts")
        self.retry_after_seconds = retry_after_seconds


def _recent_failures_from(db: Session, ip_address: str, since) -> int:
    """Failed sign-ins from this address in the window, read from the audit log."""
    return (
        db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == AuditAction.LOGIN_FAILED.value,
                AuditLog.ip_address == ip_address,
                AuditLog.occurred_at >= since,
            )
        )
        or 0
    )


@dataclass
class IssuedSession:
    session: UserSession
    token: str
    csrf_token: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def authenticate(db: Session, *, email: str, password: str, request: Request) -> IssuedSession:
    settings = get_settings()
    now = utcnow()

    # Per-address limit: stops password spraying across many accounts, which the
    # per-account lockout below cannot see.
    ip_address = request.client.host if request.client else None
    window = timedelta(minutes=settings.login_ip_window_minutes)
    if ip_address and _recent_failures_from(db, ip_address, now - window) >= (
        settings.login_ip_max_failures
    ):
        record_audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            outcome=AuditOutcome.DENIED,
            request=request,
            details={"reason": "address_throttled"},
        )
        db.commit()
        raise TooManyLoginAttempts(int(window.total_seconds()))
    user = db.scalar(select(User).where(User.email == normalize_email(email)).with_for_update())
    # Always run the hash check, even for unknown accounts, so timing reveals nothing.
    password_ok = verify_password(user.password_hash if user else None, password)

    def fail(reason: str, **details) -> LoginError:
        record_audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            outcome=AuditOutcome.FAILURE,
            actor_id=user.id if user else None,
            request=request,
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            details={"reason": reason, **details},
        )
        db.commit()
        return LoginError()

    if user is None:
        raise fail("unknown_account")
    if not user.is_active:
        raise fail("inactive")
    if user.locked_until and user.locked_until > now:
        raise fail("locked")
    if not password_ok:
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
            raise fail("bad_password", locked=True)
        raise fail("bad_password", attempts=user.failed_login_count)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    issued = _create_session(db, user, request)
    record_audit(
        db,
        action=AuditAction.LOGIN_SUCCEEDED,
        actor_id=user.id,
        request=request,
        entity_type="session",
        entity_id=issued.session.id,
    )
    db.commit()
    return issued


def _create_session(db: Session, user: User, request: Request) -> IssuedSession:
    settings = get_settings()
    now = utcnow()
    token, csrf_token = new_token(), new_token()
    session = UserSession(
        id=uuid.uuid4(),
        user=user,
        token_hash=hash_token(token),
        csrf_token_hash=hash_token(csrf_token),
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
    )
    db.add(session)
    return IssuedSession(session=session, token=token, csrf_token=csrf_token)


def resolve_session(db: Session, token: str) -> UserSession | None:
    """Return the live session for a cookie token, or None if it is unknown, ended or idle."""
    settings = get_settings()
    now = utcnow()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now
        or not session.user.is_active
    ):
        return None
    if now - session.last_seen_at >= _LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        db.commit()
    return session


def revoke_all_sessions(
    db: Session, user_id: uuid.UUID, *, except_session_id: uuid.UUID | None = None
) -> None:
    statement = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_session_id is not None:
        statement = statement.where(UserSession.id != except_session_id)
    db.execute(statement)


def logout(db: Session, session: UserSession, request: Request) -> None:
    session.revoked_at = utcnow()
    record_audit(
        db,
        action=AuditAction.LOGOUT,
        actor_id=session.user_id,
        request=request,
        entity_type="session",
        entity_id=session.id,
    )
    db.commit()


def change_password(
    db: Session,
    *,
    session: UserSession,
    current_password: str,
    new_password: str,
    request: Request,
) -> None:
    user = session.user
    if not verify_password(user.password_hash, current_password):
        record_audit(
            db,
            action=AuditAction.PASSWORD_CHANGE_FAILED,
            outcome=AuditOutcome.FAILURE,
            actor_id=user.id,
            request=request,
            entity_type="user",
            entity_id=user.id,
            details={"reason": "bad_current_password"},
        )
        db.commit()
        raise ServiceError("Current password is incorrect.")

    problems = password_problems(new_password, email=user.email)
    if verify_password(user.password_hash, new_password):
        problems.append("New password must be different from the current one.")
    if problems:
        raise ServiceError(" ".join(problems))

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    # Any other signed-in browser must sign in again with the new password.
    revoke_all_sessions(db, user.id, except_session_id=session.id)
    record_audit(
        db,
        action=AuditAction.PASSWORD_CHANGED,
        actor_id=user.id,
        request=request,
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
