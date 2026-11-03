import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, model_validator

from app.core.permissions import Role
from app.core.security import MAX_PASSWORD_LENGTH

FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class CurrentUserOut(UserOut):
    permissions: list[str]


class LoginRequest(BaseModel):
    email: EmailStr
    password: Password


class LoginResponse(BaseModel):
    user: CurrentUserOut
    csrf_token: str


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: Password


class UserCreate(BaseModel):
    email: EmailStr
    full_name: FullName
    role: Role


class UserUpdate(BaseModel):
    full_name: FullName | None = None
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "UserUpdate":
        if self.full_name is None and self.role is None and self.is_active is None:
            raise ValueError("Provide at least one field to change.")
        return self


class UserCreatedResponse(BaseModel):
    user: UserOut
    temporary_password: str


class PasswordResetResponse(BaseModel):
    temporary_password: str
