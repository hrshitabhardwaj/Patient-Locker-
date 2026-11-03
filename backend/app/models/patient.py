import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Sequence, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Source of the readable Patient ID (PL-000123). Created in the migration.
PATIENT_CODE_SEQUENCE = Sequence("patient_code_seq", metadata=Base.metadata)


class Patient(Base):
    """A patient profile. Patients are never deleted; their records are part of claim history."""

    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    patient_code: Mapped[str] = mapped_column(String(16), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone_e164: Mapped[str] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(320))
    date_of_birth: Mapped[date | None]
    policy_number: Mapped[str | None] = mapped_column(String(64))
    claim_number: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PatientAssignment(Base):
    """Which staff members may work on which patients (roles with all-patient access skip this)."""

    __tablename__ = "patient_assignments"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    assigned_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
