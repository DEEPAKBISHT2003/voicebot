import uuid
from typing import Optional, Literal, List
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    session_id: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str
    is_active: Optional[bool] = None
    last_login_at: Optional[str] = None
    last_activity_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserCreateRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    role: Literal["ADMIN", "USER"] = "USER"


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[Literal["ADMIN", "USER"]] = None
    is_active: Optional[bool] = None


# Activity Dashboard Schemas

class UserActivityItem(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    is_online: bool
    last_login_at: Optional[str] = None
    last_activity_at: Optional[str] = None
    active_session_count: int
    created_at: Optional[str] = None


class ActivitySummary(BaseModel):
    total_users: int
    online_users: int
    offline_users: int
    active_sessions: int


class ActivityPagination(BaseModel):
    total_items: int
    total_pages: int
    page: int
    page_size: int


class UserActivityResponse(BaseModel):
    summary: ActivitySummary
    pagination: ActivityPagination
    items: List[UserActivityItem]


class SessionHistoryItem(BaseModel):
    id: str
    session_id: str
    login_at: str
    last_seen_at: str
    logout_at: Optional[str] = None
    is_active: bool
    status: Literal["Active", "Logged Out", "Stale"]


class UserDetailWithSessionsResponse(BaseModel):
    user: UserActivityItem
    sessions: List[SessionHistoryItem]
