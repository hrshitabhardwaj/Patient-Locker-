from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentSession, CurrentUser
from app.core.config import get_settings
from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import CSRF_COOKIE, SESSION_COOKIE
from app.db.session import DbSession
from app.models import User
from app.schemas.user import (
    ChangePasswordRequest,
    CurrentUserOut,
    LoginRequest,
    LoginResponse,
    UserOut,
)
from app.services import auth as auth_service

# Login is the only auth route reachable without a session.
public_router = APIRouter(prefix="/auth", tags=["auth"])
# Reachable while signed in, even before a temporary password has been changed.
router = APIRouter(prefix="/auth", tags=["auth"])


def _current_user_out(user: User) -> CurrentUserOut:
    return CurrentUserOut(
        **UserOut.model_validate(user).model_dump(),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[user.role]),
    )


def _set_session_cookies(response: Response, token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_absolute_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api",
    )
    # Readable by the portal's script, which echoes it in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api")
    response.delete_cookie(CSRF_COOKIE, path="/")


@public_router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: DbSession):
    try:
        issued = auth_service.authenticate(
            db, email=body.email, password=body.password, request=request
        )
    except auth_service.LoginError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.") from None
    except auth_service.TooManyLoginAttempts as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-ins from your network. Please wait and try again.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    _set_session_cookies(response, issued.token, issued.csrf_token)
    return LoginResponse(user=_current_user_out(issued.session.user), csrf_token=issued.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(session: CurrentSession, request: Request, response: Response, db: DbSession) -> None:
    auth_service.logout(db, session, request)
    _clear_session_cookies(response)


@router.get("/me", response_model=CurrentUserOut)
def me(user: CurrentUser):
    return _current_user_out(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest, session: CurrentSession, request: Request, db: DbSession
) -> None:
    auth_service.change_password(
        db,
        session=session,
        current_password=body.current_password,
        new_password=body.new_password,
        request=request,
    )
