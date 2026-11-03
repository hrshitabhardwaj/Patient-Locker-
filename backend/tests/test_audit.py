import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.permissions import Role
from app.services.audit import AuditAction, record_audit
from tests.helpers import audit_entries, login


def test_audit_log_rejects_update_delete_and_truncate(db):
    record_audit(db, action=AuditAction.LOGOUT)
    db.commit()

    for statement in (
        "UPDATE audit_logs SET action = 'tampered'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ):
        with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
            db.execute(text(statement))


def test_audit_details_refuse_personal_data(db):
    with pytest.raises(ValueError, match="personal data"):
        record_audit(db, action=AuditAction.USER_CREATED, details={"email": "a@b.example.com"})


def test_admin_reads_audit_log_and_the_read_is_recorded(client, db, make_user):
    admin = make_user(role=Role.ADMIN)
    login(client, admin.email)

    response = client.get("/api/v1/audit-logs", params={"action": "auth.login_succeeded"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["actor_user_id"] == str(admin.id)
    [viewed] = audit_entries(db, "audit.viewed")
    assert viewed.details == {"filters": {"action": "auth.login_succeeded"}}


def test_case_manager_cannot_read_audit_log(client, make_user):
    manager = make_user(role=Role.CASE_MANAGER)
    login(client, manager.email)

    assert client.get("/api/v1/audit-logs").status_code == 403
