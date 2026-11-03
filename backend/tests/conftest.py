"""Test setup: a migrated test database, and each test wrapped in a transaction that rolls back."""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from dotenv import dotenv_values

_ROOT = Path(__file__).resolve().parents[2]
_env = {**dotenv_values(_ROOT / ".env"), **os.environ}

if not _env.get("TEST_DATABASE_URL"):
    raise RuntimeError("TEST_DATABASE_URL is not set; run `make env` and `make db-create`.")

# Must happen before any application module reads settings.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = _env["TEST_DATABASE_URL"]
# Uploaded test files go to a throwaway folder, never the real locker.
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="pl-test-storage-")
# Tests never call a real model unless they opt in (see tests marked "ollama").
os.environ["LLM_PROVIDER"] = "none"
# Fictional company details for the message templates.
os.environ["COMPANY_NAME"] = "Northfield Claims Desk"
os.environ["SUPPORT_EMAIL"] = "claims@northfield.example"
os.environ["SUPPORT_PHONE"] = "1800-000-0141"
os.environ["PATIENT_RESPONSE_DAYS"] = "7"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.core.permissions import Role  # noqa: E402
from app.db.session import get_db, get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Patient, PatientAssignment, User  # noqa: E402
from app.models.patient import PATIENT_CODE_SEQUENCE  # noqa: E402
from tests.helpers import DEFAULT_PASSWORD  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def fast_password_hashing() -> Iterator[None]:
    """Production Argon2 settings are deliberately slow; tests use cheap ones."""
    original = security._hasher
    security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    yield
    security._hasher = original


@pytest.fixture(scope="session")
def migrated_engine() -> Engine:
    """Rebuild the schema from scratch, which also proves every downgrade works."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return get_engine()


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Session]:
    connection = migrated_engine.connect()
    transaction = connection.begin()
    # Application commits become savepoints; everything is rolled back after the test.
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def app(db: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    counter = iter(range(1, 10_000))

    def _make_user(
        *,
        role: Role = Role.STAFF,
        password: str = DEFAULT_PASSWORD,
        email: str | None = None,
        must_change_password: bool = False,
        is_active: bool = True,
    ) -> User:
        user = User(
            email=email or f"user{next(counter)}@clinic.example.com",
            full_name="Test User",
            role=role,
            password_hash=security.hash_password(password),
            must_change_password=must_change_password,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        return user

    return _make_user


@pytest.fixture
def make_patient(db: Session) -> Callable[..., Patient]:
    def _make_patient(*, assigned_to: tuple[User, ...] = (), **fields) -> Patient:
        number = db.scalar(PATIENT_CODE_SEQUENCE.next_value().select())
        patient = Patient(
            patient_code=f"PL-{number:06d}",
            full_name=fields.pop("full_name", "Asha Verma"),
            phone_e164=fields.pop("phone_e164", "+919876543210"),
            email=fields.pop("email", "asha@example.com"),
            **fields,
        )
        db.add(patient)
        db.flush()
        for user in assigned_to:
            db.add(PatientAssignment(patient_id=patient.id, user_id=user.id))
        db.commit()
        return patient

    return _make_patient
