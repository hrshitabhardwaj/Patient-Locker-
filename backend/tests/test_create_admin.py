"""The first-administrator command (scripts/create_admin.py)."""

import io
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.permissions import Permission, Role
from app.db import schema
from app.models import AuditLog, User
from scripts import create_admin

PASSWORD = "Harbour-lantern-42"


def run(db: Session, monkeypatch, *args: str, password: str = PASSWORD) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return create_admin.main(
        ["--password-stdin", "--yes", *args], session_factory=lambda: nullcontext(db)
    )


def admin_args(email: str = "First.Admin@Clinic.example.com") -> list[str]:
    return ["--email", email, "--name", "  Asha Verma "]


def test_creates_first_admin_with_hashed_password(db, monkeypatch, capsys):
    assert run(db, monkeypatch, *admin_args()) == 0

    user = db.scalar(select(User).where(User.email == "first.admin@clinic.example.com"))
    assert user.role == Role.ADMIN
    assert user.full_name == "Asha Verma"
    assert user.is_active and not user.must_change_password
    assert user.password_hash.startswith("$argon2")
    assert security.verify_password(user.password_hash, PASSWORD)
    audit = db.scalar(select(AuditLog).where(AuditLog.entity_id == str(user.id)))
    assert audit.action == "user.created"
    output = capsys.readouterr()
    assert "first.admin@clinic.example.com created" in output.out
    assert PASSWORD not in output.out + output.err


def test_created_admin_can_sign_in_with_admin_permissions(db, monkeypatch, client: TestClient):
    assert run(db, monkeypatch, *admin_args()) == 0

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "first.admin@clinic.example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "admin"
    assert set(me["permissions"]) == {p.value for p in Permission}
    # The admin can reach admin-only routes, such as the staff list.
    assert client.get("/api/v1/users").status_code == 200


def test_prompts_for_email_and_name(db, monkeypatch):
    answers = iter(["prompted@clinic.example.com", "Ravi Menon"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    passwords = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(passwords))

    assert create_admin.main(["--yes"], session_factory=lambda: nullcontext(db)) == 0
    assert db.scalar(select(User.full_name).where(User.email == "prompted@clinic.example.com"))


def test_refuses_when_an_admin_already_exists(db, monkeypatch, make_user, capsys):
    existing = make_user(role=Role.ADMIN, email="boss@clinic.example.com")
    original_hash = existing.password_hash

    assert run(db, monkeypatch, *admin_args()) == 1

    assert "already exists" in capsys.readouterr().err
    assert db.scalar(select(User).where(User.email == "first.admin@clinic.example.com")) is None
    db.refresh(existing)
    assert existing.password_hash == original_hash


def test_inactive_admin_does_not_block_recovery(db, monkeypatch, make_user):
    make_user(role=Role.ADMIN, is_active=False)

    assert run(db, monkeypatch, *admin_args()) == 0


def test_refuses_an_email_already_in_use(db, monkeypatch, make_user, capsys):
    staff = make_user(role=Role.STAFF, email="first.admin@clinic.example.com")
    original_hash = staff.password_hash

    assert run(db, monkeypatch, *admin_args()) == 1

    assert "already exists" in capsys.readouterr().err
    db.refresh(staff)
    assert staff.role == Role.STAFF and staff.password_hash == original_hash


@pytest.mark.parametrize("email", ["not-an-email", "admin@", "admin@example.invalid", ""])
def test_rejects_invalid_email(db, monkeypatch, capsys, email):
    assert run(db, monkeypatch, "--email", email, "--name", "Asha Verma") == 1

    assert "email" in capsys.readouterr().err
    assert db.scalar(select(User.id).where(User.role == Role.ADMIN)) is None


def test_rejects_blank_name(db, monkeypatch, capsys):
    assert run(db, monkeypatch, "--email", "a.verma@clinic.example.com", "--name", "  ") == 1

    assert "full_name" in capsys.readouterr().err


def test_rejects_weak_password(db, monkeypatch, capsys):
    assert run(db, monkeypatch, *admin_args(), password="short") == 1

    assert "at least 12 characters" in capsys.readouterr().err
    assert db.scalar(select(User.id).where(User.role == Role.ADMIN)) is None


def test_mismatched_password_confirmation(db, monkeypatch, capsys):
    passwords = iter([PASSWORD, PASSWORD + "x"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(passwords))

    result = create_admin.main(["--yes", *admin_args()], session_factory=lambda: nullcontext(db))

    assert result == 1
    assert "do not match" in capsys.readouterr().err


def test_cancelled_confirmation_changes_nothing(db, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    result = create_admin.main(admin_args(), session_factory=lambda: nullcontext(db))

    assert result == 1
    assert "Cancelled" in capsys.readouterr().err
    assert db.scalar(select(User.id).where(User.role == Role.ADMIN)) is None


def test_refuses_a_database_without_current_migrations(db, monkeypatch, capsys):
    monkeypatch.setattr(schema, "latest_migration", lambda: "not-yet-applied")

    assert run(db, monkeypatch, *admin_args()) == 1

    assert "missing or out of date" in capsys.readouterr().err


def test_missing_database_url_never_falls_back_to_dotenv(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert create_admin.main(["--yes", *admin_args()]) == 1

    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_invalid_database_url(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "not a url")

    assert create_admin.main(["--yes", *admin_args()]) == 1

    assert "not a valid database URL" in capsys.readouterr().err


def test_connection_failure_is_reported_without_the_password(monkeypatch, capsys):
    # Port 1 on this machine refuses connections.
    monkeypatch.setenv("DATABASE_URL", "postgresql://admin:Sup3r-secret-pw@127.0.0.1:1/locker")

    assert create_admin.main(["--yes", "--password-stdin", *admin_args()]) == 1

    output = capsys.readouterr()
    assert "Could not connect to database 'locker' on 127.0.0.1:1" in output.err
    assert "Sup3r-secret-pw" not in output.out + output.err
