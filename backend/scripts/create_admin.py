"""Create the first admin account from the command line (there is no public sign-up).

Usage:
    python -m scripts.create_admin --email admin@clinic.example --name "Asha Verma"
    echo "$PASSWORD" | python -m scripts.create_admin --email ... --name ... --password-stdin
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.permissions import Role
from app.core.security import hash_password, password_problems
from app.db.session import get_sessionmaker
from app.models import User
from app.services.audit import AuditAction, record_audit
from app.services.auth import normalize_email


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords do not match.")
            return 1

    problems = password_problems(password, email=email)
    if problems:
        print("\n".join(problems))
        return 1

    with get_sessionmaker()() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print("A user with this email already exists.")
            return 1
        user = User(
            email=email,
            full_name=args.name.strip(),
            role=Role.ADMIN,
            password_hash=hash_password(password),
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        record_audit(
            db,
            action=AuditAction.USER_CREATED,
            entity_type="user",
            entity_id=user.id,
            details={"role": Role.ADMIN.value, "via": "cli"},
        )
        db.commit()
    print("Admin account created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
