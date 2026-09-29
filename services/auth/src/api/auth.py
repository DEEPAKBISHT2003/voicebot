from fastapi import APIRouter, HTTPException, Depends, status
from loguru import logger

from services.auth.src.models.user import UserModel
from services.auth.src.schemas import LoginRequest, TokenResponse, UserResponse
from services.auth.src.security import verify_password, create_access_token
from services.auth.src.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """
    Public Login Endpoint.
    1. Normalize email to lowercase.
    2. Query active user (UserModel.get_or_none(email=normalized_email, is_active=True)).
    3. Verify Argon2id password hash.
    4. Return generic 401 error on any failure to prevent user/status enumeration.
    5. Issue JWT access token with role.
    """
    normalized_email = req.email.strip().lower()
    
    # Query only active users
    user = await UserModel.get_or_none(email=normalized_email, is_active=True)
    
    if not user or not verify_password(req.password, user.password_hash):
        # Generic error message to prevent account enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Issue JWT access token
    token_payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
    }
    access_token = create_access_token(token_payload)

    logger.info(f"[Auth] Successful login for user: {user.email} (Role: {user.role})")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
    }


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserModel = Depends(get_current_user)):
    """
    Authenticated Current User Profile Endpoint.
    Returns minimal user identity (id, email, role).
    """
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None,
    }
