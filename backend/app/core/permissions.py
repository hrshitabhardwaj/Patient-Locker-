"""Roles and what each role may do. Patient-level access is enforced separately per patient."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    CASE_MANAGER = "case_manager"
    STAFF = "staff"


class Permission(StrEnum):
    MANAGE_USERS = "users:manage"
    READ_AUDIT_LOG = "audit:read"
    # Without this, a user only sees patients assigned to them.
    ACCESS_ALL_PATIENTS = "patients:access_all"
    ASSIGN_PATIENTS = "patients:assign"
    # Removing a document can change matching results, so it is limited to senior roles.
    DELETE_DOCUMENTS = "documents:delete"
    RESOLVE_REVIEWS = "reviews:resolve"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.CASE_MANAGER: frozenset(
        {
            Permission.ACCESS_ALL_PATIENTS,
            Permission.ASSIGN_PATIENTS,
            Permission.DELETE_DOCUMENTS,
            Permission.RESOLVE_REVIEWS,
        }
    ),
    Role.STAFF: frozenset({Permission.RESOLVE_REVIEWS}),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
