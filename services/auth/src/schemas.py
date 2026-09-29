import uuid
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserCreateRequest(BaseModel):
    email: str
    password: str
    role: Literal["ADMIN", "USER"] = "USER"


class UserUpdateRequest(BaseModel):
    role: Optional[Literal["ADMIN", "USER"]] = None
    is_active: Optional[bool] = None
