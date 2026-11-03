from app.core.permissions import Role
from tests.helpers import DEFAULT_PASSWORD, audit_entries, login

USERS = "/api/v1/users"


def test_staff_and_case_managers_cannot_manage_users(client, db, make_user):
    for role in (Role.STAFF, Role.CASE_MANAGER):
        user = make_user(role=role)
        login(client, user.email)

        assert client.get(USERS).status_code == 403
        assert client.post(
            USERS, json={"email": "x@clinic.example.com", "full_name": "X", "role": "admin"}
        ).status_code == 403

    denied = audit_entries(db, "access.denied")
    assert len(denied) == 4
    assert {d.details["permission"] for d in denied} == {"users:manage"}


def test_admin_creates_user_who_must_change_temporary_password(app, client, db, make_user):
    from fastapi.testclient import TestClient

    admin = make_user(role=Role.ADMIN)
    login(client, admin.email)

    response = client.post(
        USERS,
        json={
            "email": "New.Staff@Clinic.example.com",
            "full_name": " Ravi Kumar ",
            "role": "staff",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "new.staff@clinic.example.com"
    assert body["user"]["full_name"] == "Ravi Kumar"
    assert body["user"]["must_change_password"] is True

    new_user_browser = TestClient(app)
    login(new_user_browser, "new.staff@clinic.example.com", body["temporary_password"])
    assert new_user_browser.get("/api/v1/audit-logs").status_code == 403

    [entry] = audit_entries(db, "user.created")
    assert entry.actor_user_id == admin.id
    assert entry.details == {"role": "staff"}


def test_duplicate_email_is_rejected(client, make_user):
    admin = make_user(role=Role.ADMIN)
    existing = make_user()
    login(client, admin.email)

    response = client.post(
        USERS, json={"email": existing.email, "full_name": "Dup", "role": "staff"}
    )

    assert response.status_code == 409


def test_deactivating_a_user_ends_their_sessions(app, client, db, make_user):
    from fastapi.testclient import TestClient

    admin = make_user(role=Role.ADMIN)
    staff = make_user()
    staff_browser = TestClient(app)
    login(staff_browser, staff.email)
    login(client, admin.email)

    response = client.patch(f"{USERS}/{staff.id}", json={"is_active": False})

    assert response.status_code == 200
    assert staff_browser.get("/api/v1/auth/me").status_code == 401
    [entry] = audit_entries(db, "user.updated")
    assert entry.details["changed"] == ["is_active"]


def test_admin_cannot_lock_themselves_out(client, make_user):
    admin = make_user(role=Role.ADMIN)
    login(client, admin.email)

    assert client.patch(f"{USERS}/{admin.id}", json={"is_active": False}).status_code == 400
    assert client.patch(f"{USERS}/{admin.id}", json={"role": "staff"}).status_code == 400


def test_password_reset_replaces_old_password(app, client, db, make_user):
    from fastapi.testclient import TestClient

    admin = make_user(role=Role.ADMIN)
    staff = make_user()
    login(client, admin.email)

    response = client.post(f"{USERS}/{staff.id}/reset-password")

    assert response.status_code == 200
    temporary = response.json()["temporary_password"]
    fresh = TestClient(app)
    old = fresh.post(
        "/api/v1/auth/login", json={"email": staff.email, "password": DEFAULT_PASSWORD}
    )
    assert old.status_code == 401
    login(fresh, staff.email, temporary)
    assert audit_entries(db, "user.password_reset")[0].entity_id == str(staff.id)


def test_list_users_filters_by_role(client, make_user):
    admin = make_user(role=Role.ADMIN)
    make_user(role=Role.STAFF)
    make_user(role=Role.CASE_MANAGER)
    login(client, admin.email)

    response = client.get(USERS, params={"role": "case_manager"})

    assert response.status_code == 200
    assert [u["role"] for u in response.json()["items"]] == ["case_manager"]
