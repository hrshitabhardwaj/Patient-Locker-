from app.core.permissions import ROLE_PERMISSIONS, Permission, Role
from app.core.security import (
    generate_temporary_password,
    hash_password,
    password_problems,
    verify_password,
)

EMAIL = "asha.verma@clinic.example.com"


def test_hash_and_verify():
    hashed = hash_password("correct-horse-42")
    assert hashed != "correct-horse-42"
    assert verify_password(hashed, "correct-horse-42")
    assert not verify_password(hashed, "wrong-horse-42")
    assert not verify_password(None, "correct-horse-42")
    assert not verify_password("not-a-hash", "correct-horse-42")


def test_password_policy():
    assert password_problems("correct-horse-42", email=EMAIL) == []
    assert any("12" in p for p in password_problems("short-1", email=EMAIL))
    assert password_problems("onlylettersheretoo", email=EMAIL)
    assert password_problems("asha.verma-2026!", email=EMAIL)
    assert password_problems("x1" * 70, email=EMAIL)


def test_temporary_passwords_meet_policy_and_differ():
    first, second = generate_temporary_password(EMAIL), generate_temporary_password(EMAIL)
    assert first != second
    assert password_problems(first, email=EMAIL) == []


def test_only_admin_manages_users_and_reads_audit():
    for role in Role:
        allowed = ROLE_PERMISSIONS[role]
        expected = role is Role.ADMIN
        assert (Permission.MANAGE_USERS in allowed) is expected
        assert (Permission.READ_AUDIT_LOG in allowed) is expected
    assert Permission.ACCESS_ALL_PATIENTS not in ROLE_PERMISSIONS[Role.STAFF]
