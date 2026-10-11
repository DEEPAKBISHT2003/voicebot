from typing import Optional, Literal, List, Any
from pydantic import BaseModel, EmailStr, Field, model_validator


class LoginRequest(BaseModel):
    email: str
    password: str


class MicrosoftAuthRequest(BaseModel):
    access_token: str = Field(..., min_length=10, description="Microsoft Entra ID access token for the platform API")

    @model_validator(mode="before")
    @classmethod
    def resolve_token(cls, data: Any) -> Any:
        if isinstance(data, str):
            cleaned = data.strip().strip('"').strip("'")
            return {"access_token": cleaned}

        if isinstance(data, dict):
            # Accept standard access token fields (access_token, accessToken, token, or nested token)
            token_candidates = [
                data.get("access_token"),
                data.get("accessToken"),
                data.get("token"),
            ]
            for candidate in token_candidates:
                if isinstance(candidate, str) and len(candidate.strip()) >= 10:
                    data["access_token"] = candidate.strip()
                    return data
                elif isinstance(candidate, dict):
                    inner_token = (
                        candidate.get("token")
                        or candidate.get("secret")
                        or candidate.get("value")
                    )
                    if isinstance(inner_token, str) and len(inner_token.strip()) >= 10:
                        data["access_token"] = inner_token.strip()
                        return data
        return data


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
