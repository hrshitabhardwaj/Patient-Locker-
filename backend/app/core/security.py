"""Password hashing, password policy and session tokens."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

SESSION_COOKIE = "pl_session"
CSRF_COOKIE = "pl_csrf"
CSRF_HEADER = "X-CSRF-Token"

MIN_PASSWORD_LENGTH = 12
# Upper bound so a huge password can't be used to burn CPU in the hasher.
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()
# Checked against when the account doesn't exist, so response time doesn't reveal which emails do.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, email: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if password.isalpha() or password.isdigit():
        problems.append("Password must mix letters with digits or symbols.")
    local_part = email.split("@", 1)[0].lower()
    if len(local_part) >= 4 and local_part in password.lower():
        problems.append("Password must not contain your email name.")
    return problems


def generate_temporary_password(email: str) -> str:
    while True:
        candidate = secrets.token_urlsafe(12)
        if not password_problems(candidate, email=email):
            return candidate


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored hashed, so a database leak doesn't hand out live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()
