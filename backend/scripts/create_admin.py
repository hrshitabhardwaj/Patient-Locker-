"""Create the first administrator account (there is no public sign-up).

The target database must be named explicitly: DATABASE_URL from the environment, or
--local for the database in the project's .env. The .env file is never used by accident.

Usage:
    DATABASE_URL='<Render external database URL>' python -m scripts.create_admin
    python -m scripts.create_admin --local --email admin@clinic.example --name "Asha Verma"
    echo "$PASSWORD" | python -m scripts.create_admin ... --password-stdin --yes

Refuses to run if an active administrator already exists: further accounts are added by
an administrator from the portal's Staff page.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager

from pydantic import BaseModel, EmailStr, ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings, psycopg_url
from app.core.permissions import Role
from app.core.security import hash_password, password_problems
from app.db import schema
from app.models import User
from app.schemas.user import FullName
from app.services.audit import AuditAction, record_audit
from app.services.auth import normalize_email

SessionFactory = Callable[[], AbstractContextManager[Session]]


class AdminDetails(BaseModel):
    """Same email and name rules as accounts created from the portal."""

    email: EmailStr
    full_name: FullName


class Abort(Exception):
    """Stops the command with a message for the operator; nothing has been written."""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", help="prompted for if omitted")
    parser.add_argument("--name", help="full name; prompted for if omitted")
    parser.add_argument(
        "--local", action="store_true", help="use DATABASE_URL from the project's .env file"
    )
    parser.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return parser.parse_args(argv)


def _database_url(args: argparse.Namespace) -> str:
    if args.local:
        return get_settings().database_url
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise Abort(
            "DATABASE_URL is not set. Set it to the database to create the administrator in\n"
            "(for Render: the database's External Database URL), or pass --local for the\n"
            "database in your .env file."
        )
    return psycopg_url(url)


def _describe(url: str) -> str:
    """Where the URL points, without the password."""
    parsed = make_url(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"database '{parsed.database}' on {parsed.host or 'localhost'}{port}"


def _check_database(db: Session, target: str, url: str | None) -> None:
    try:
        db.execute(text("SELECT 1"))
        up_to_date = schema.is_up_to_date(db)
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        password = make_url(url).password if url else None
        if password:
            reason = reason.replace(str(password), "****")
        raise Abort(f"Could not connect to {target}: {reason}") from None
    if not up_to_date:
        raise Abort(
            f"The tables in {target} are missing or out of date. Deploy the API (its start\n"
            "command runs the migrations) or run `alembic upgrade head`, then try again."
        )


def _confirm(args: argparse.Namespace) -> None:
    if args.yes:
        return
    if input("Create the first administrator here? [y/N] ").strip().lower() not in ("y", "yes"):
        raise Abort("Cancelled. Nothing was changed.")


def _read_details(args: argparse.Namespace) -> AdminDetails:
    email = args.email if args.email is not None else input("Administrator email: ")
    name = args.name if args.name is not None else input("Full name: ")
    try:
        details = AdminDetails(email=email.strip(), full_name=name)
    except ValidationError as exc:
        problems = (f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
        raise Abort("\n".join(problems)) from None
    details.email = normalize_email(details.email)
    return details


def _read_password(args: argparse.Namespace, email: str) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password (not shown): ")
        if getpass.getpass("Repeat password: ") != password:
            raise Abort("Passwords do not match.")
    problems = password_problems(password, email=email)
    if problems:
        raise Abort("\n".join(problems))
    return password


def _create_admin(db: Session, args: argparse.Namespace, target: str) -> str:
    active_admins = select(func.count(User.id)).where(
        User.role == Role.ADMIN, User.is_active.is_(True)
    )
    if db.scalar(active_admins):
        raise Abort(
            f"An administrator already exists in {target}. Sign in as that administrator and\n"
            "add accounts from the Staff page. Nothing was changed."
        )

    details = _read_details(args)
    if db.scalar(select(User.id).where(User.email == details.email)):
        raise Abort("A user with this email already exists. Nothing was changed.")
    password = _read_password(args, details.email)

    user = User(
        email=details.email,
        full_name=details.full_name,
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
    return details.email


def main(argv: list[str] | None = None, session_factory: SessionFactory | None = None) -> int:
    """session_factory replaces the connection built from DATABASE_URL (used by tests)."""
    args = _parse_args(argv)
    engine = None
    try:
        url = None
        target = "the configured database"
        if session_factory is None:
            url = _database_url(args)
            try:
                target = _describe(url)
                engine = create_engine(url, pool_pre_ping=True)
            except ArgumentError:
                raise Abort("DATABASE_URL is not a valid database URL.") from None
            session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        print(f"Target: {target}")
        _confirm(args)
        with session_factory() as db:
            _check_database(db, target, url)
            email = _create_admin(db, args, target)
    except Abort as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(f"Administrator {email} created in {target}. Sign in to the portal with this email.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
