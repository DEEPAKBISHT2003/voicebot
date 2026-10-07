import uuid
import datetime
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPAuthorizationCredentials
from loguru import logger

from services.auth.src.models.user import UserModel, UserSessionModel
from services.auth.src.schemas import LoginRequest, TokenResponse, UserResponse
from services.auth.src.security import verify_password, create_access_token, decode_access_token
from services.auth.src.deps import get_current_user, http_bearer
from services.auth.src.middleware import record_user_activity, _activity_throttle_cache

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """
    Public Login Endpoint.
    1. Normalize email to lowercase.
    2. Query active user.
    3. Verify Argon2id password hash.
    4. Update user.last_login_at and user.last_activity_at.
    5. Create UserSession record.
    6. Issue JWT access token containing session_id.
    """
    normalized_email = req.email.strip().lower()
    
    # Query only active users
    user = await UserModel.get_or_none(email=normalized_email, is_active=True)
    
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    session_id = str(uuid.uuid4())

    # Update user login and activity timestamps
    user.last_login_at = now
    user.last_activity_at = now
    await user.save(update_fields=["last_login_at", "last_activity_at", "updated_at"])

    # Create new session record
    await UserSessionModel.create(
        user_id=user.id,
        session_id=session_id,
        login_at=now,
        last_seen_at=now,
        is_active=True,
    )

    # Issue JWT access token with session_id
    token_payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "session_id": session_id,
    }
    access_token = create_access_token(token_payload)

    logger.info(f"[Auth] Successful login for user: {user.email} (Role: {user.role}, Session: {session_id})")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "session_id": session_id,
    }


@router.post("/heartbeat")
async def heartbeat(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Authenticated Heartbeat Endpoint.
    Sent by frontend every 60s while the browser tab is visible.
    Updates user.last_activity_at and session.last_seen_at.
    """
    session_id = None
    if credentials and credentials.credentials:
        try:
            payload = decode_access_token(credentials.credentials)
            session_id = payload.get("session_id")
        except Exception:
            pass

    await record_user_activity(user_id=str(current_user.id), session_id=session_id, force=True)
    return {"status": "ok"}


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Authenticated Logout Endpoint.
    Marks the specific current session as inactive and records logout timestamp.
    Does not logout all sessions/devices.
    """
    session_id = None
    if credentials and credentials.credentials:
        try:
            payload = decode_access_token(credentials.credentials)
            session_id = payload.get("session_id")
        except Exception:
            pass

    now = datetime.datetime.now(datetime.timezone.utc)
    if session_id:
        await UserSessionModel.filter(session_id=session_id).update(
            is_active=False,
            logout_at=now,
        )
        _activity_throttle_cache.pop(session_id, None)

    logger.info(f"[Auth] User logged out: {current_user.email} (Session: {session_id})")
    return {"status": "ok", "message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserModel = Depends(get_current_user)):
    """
    Authenticated Current User Profile Endpoint.
    Returns user identity.
    """
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
        "last_activity_at": current_user.last_activity_at.isoformat() if current_user.last_activity_at else None,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None,
    }
