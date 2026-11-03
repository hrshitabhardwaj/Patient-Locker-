from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utcnow
from app.core.permissions import Role
from app.core.security import CSRF_HEADER, SESSION_COOKIE
from app.models import UserSession
from tests.helpers import DEFAULT_PASSWORD, audit_entries, login

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"
CHANGE_PASSWORD = "/api/v1/auth/change-password"


def test_login_sets_httponly_session_cookie_and_audits(client, db, make_user):
    user = make_user(role=Role.CASE_MANAGER)

    response = client.post(LOGIN, json={"email": user.email.upper(), "password": DEFAULT_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == str(user.id)
    assert "patients:access_all" in body["user"]["permissions"]
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session_cookie
    assert "samesite=strict" in session_cookie.lower()

    [entry] = audit_entries(db, "auth.login_succeeded")
    assert entry.actor_user_id == user.id
    assert entry.request_id


def test_session_token_is_stored_only_as_hash(client, db, make_user):
    user = make_user()
    client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    raw_token = client.cookies.get(SESSION_COOKIE)

    stored = db.scalar(select(UserSession).where(UserSession.user_id == user.id))

    assert raw_token and stored.token_hash != raw_token and len(stored.token_hash) == 64


def test_wrong_password_and_unknown_email_get_the_same_answer(client, db, make_user):
    user = make_user()

    wrong = client.post(LOGIN, json={"email": user.email, "password": "not-the-password-1"})
    unknown = client.post(
        LOGIN, json={"email": "nobody@clinic.example.com", "password": "whatever-123"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password."}
    reasons = [e.details["reason"] for e in audit_entries(db, "auth.login_failed")]
    assert reasons == ["bad_password", "unknown_account"]


def test_account_locks_after_repeated_failures(client, db, make_user):
    user = make_user()
    for _ in range(5):
        client.post(LOGIN, json={"email": user.email, "password": "wrong-password-9"})

    # Even the right password is refused while locked.
    response = client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})

    assert response.status_code == 401
    db.refresh(user)
    assert user.locked_until is not None
    assert audit_entries(db, "auth.login_failed")[-1].details["reason"] == "locked"


def test_inactive_user_cannot_sign_in(client, make_user):
    user = make_user(is_active=False)

    response = client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})

    assert response.status_code == 401


def test_logout_ends_the_session(client, db, make_user):
    user = make_user()
    login(client, user.email)

    assert client.post(LOGOUT).status_code == 204
    assert audit_entries(db, "auth.logout")[0].actor_user_id == user.id

    # Replaying the old cookie must not work.
    stored = db.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert stored.revoked_at is not None


def test_revoked_cookie_is_refused(client, db, make_user):
    user = make_user()
    login(client, user.email)
    raw_token = client.cookies.get(SESSION_COOKIE)
    client.post(LOGOUT)

    client.cookies.set(SESSION_COOKIE, raw_token, path="/api")
    assert client.get(ME).status_code == 401


def test_unsafe_request_without_csrf_token_is_refused(client, db, make_user):
    user = make_user()
    login(client, user.email)
    del client.headers[CSRF_HEADER]

    response = client.post(LOGOUT)

    assert response.status_code == 403
    assert audit_entries(db, "access.csrf_rejected")[0].actor_user_id == user.id


def test_idle_session_expires(client, db, make_user):
    user = make_user()
    login(client, user.email)
    stored = db.scalar(select(UserSession).where(UserSession.user_id == user.id))
    stored.last_seen_at = utcnow() - timedelta(minutes=31)
    db.commit()

    assert client.get(ME).status_code == 401


def test_temporary_password_must_be_changed_before_other_access(client, db, make_user):
    user = make_user(role=Role.ADMIN, must_change_password=True)
    login(client, user.email)

    assert client.get(ME).status_code == 200
    assert client.get("/api/v1/users").status_code == 403

    response = client.post(
        CHANGE_PASSWORD,
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-brand-new-pass-7"},
    )

    assert response.status_code == 204
    assert client.get("/api/v1/users").status_code == 200
    assert audit_entries(db, "auth.password_changed")[0].actor_user_id == user.id


def test_password_change_signs_out_other_sessions(app, client, db, make_user):
    from fastapi.testclient import TestClient

    user = make_user()
    other_browser = TestClient(app)
    login(other_browser, user.email)
    login(client, user.email)

    client.post(
        CHANGE_PASSWORD,
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-brand-new-pass-7"},
    )

    assert client.get(ME).status_code == 200
    assert other_browser.get(ME).status_code == 401


def test_password_change_rejects_wrong_current_and_weak_new(client, db, make_user):
    user = make_user()
    login(client, user.email)

    wrong = client.post(
        CHANGE_PASSWORD,
        json={"current_password": "not-it-at-all-1", "new_password": "a-brand-new-pass-7"},
    )
    weak = client.post(
        CHANGE_PASSWORD, json={"current_password": DEFAULT_PASSWORD, "new_password": "short1"}
    )

    assert wrong.status_code == 400
    assert weak.status_code == 400
    assert "at least 12" in weak.json()["detail"]
    assert audit_entries(db, "auth.password_change_failed")[0].outcome == "failure"
