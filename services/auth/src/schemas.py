from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserResponse(BaseModel):
    id: str
    name: Optional[str] = None
    employee_id: Optional[int] = None
    email: str
    role: str
    is_active: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserCreateRequest(BaseModel):
    name: Optional[str] = None
    employee_id: Optional[int] = None
    email: str
    password: str
    role: Literal["ADMIN", "USER"] = "USER"
    is_active: Optional[bool] = True

    @field_validator("email")
    @classmethod
    def validate_appzlogic_domain(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Email is required.")
        normalized = v.strip().lower()
        parts = normalized.split("@")
        if len(parts) != 2 or not parts[0] or parts[1] != "appzlogic.com":
            raise ValueError("Only @appzlogic.com email addresses are allowed.")
        return normalized


class SignupRequest(BaseModel):
    name: str
    employee_id: int
    email: str
    password: str
    confirm_password: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if len(trimmed) < 2:
            raise ValueError("Full Name must be at least 2 characters.")
        return trimmed

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Employee ID must be a positive number.")
        return v

    @field_validator("email")
    @classmethod
    def validate_appzlogic_domain(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Email is required.")
        normalized = v.strip().lower()
        parts = normalized.split("@")
        if len(parts) != 2 or not parts[0] or parts[1] != "appzlogic.com":
            raise ValueError("Only @appzlogic.com email addresses are allowed.")
        return normalized

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match.")
        return v


class SignupResponse(BaseModel):
    message: str
    user: UserResponse


class UserUpdateRequest(BaseModel):
    role: Optional[Literal["ADMIN", "USER"]] = None
    is_active: Optional[bool] = None
