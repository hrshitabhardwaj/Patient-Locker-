import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.clock import utcnow
from app.core.permissions import Role
from app.core.phone import normalize_phone
from app.schemas.user import FullName


def _lowercase(value: str) -> str:
    return value.lower()


def _plausible_birth_date(value: date | None) -> date | None:
    if value is not None and not (date(1900, 1, 1) <= value <= utcnow().date()):
        raise ValueError("Date of birth must be between 1900 and today.")
    return value


Phone = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=32), AfterValidator(normalize_phone)
]
Email = Annotated[EmailStr, AfterValidator(_lowercase)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
BirthDate = Annotated[date | None, AfterValidator(_plausible_birth_date)]


class PatientCreate(BaseModel):
    full_name: FullName
    phone: Phone
    email: Email
    date_of_birth: BirthDate = None
    policy_number: Reference | None = None
    claim_number: Reference | None = None

    def to_fields(self) -> dict[str, Any]:
        fields = self.model_dump()
        fields["phone_e164"] = fields.pop("phone")
        return fields


class PatientUpdate(BaseModel):
    """Only the fields sent are changed. Optional fields can be cleared by sending null."""

    full_name: FullName | None = None
    phone: Phone | None = None
    email: Email | None = None
    date_of_birth: BirthDate = None
    policy_number: Reference | None = None
    claim_number: Reference | None = None

    @model_validator(mode="after")
    def _required_fields_not_cleared(self) -> "PatientUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change.")
        for field in ("full_name", "phone", "email"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty.")
        return self

    def to_changes(self) -> dict[str, Any]:
        changes = self.model_dump(exclude_unset=True)
        if "phone" in changes:
            changes["phone_e164"] = changes.pop("phone")
        return changes


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_by_name=True)

    id: uuid.UUID
    patient_code: str
    full_name: str
    phone: str = Field(validation_alias="phone_e164")
    email: str
    date_of_birth: date | None
    policy_number: str | None
    claim_number: str | None
    created_at: datetime
    updated_at: datetime


class PatientListItem(PatientOut):
    document_count: int
    latest_letter_status: str | None


class AssignmentOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    role: Role
    is_active: bool
    assigned_at: datetime


class AssignmentsUpdate(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=50)


class StaffDirectoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    role: Role
