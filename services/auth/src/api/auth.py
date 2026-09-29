from fastapi import APIRouter, HTTPException, Depends, status
from loguru import logger

from services.auth.src.models.user import UserModel
from services.auth.src.schemas import (
    LoginRequest,
    TokenResponse,
    UserResponse,
    SignupRequest,
    SignupResponse,
)
from services.auth.src.security import (
    verify_password,
    hash_password,
    validate_password_strength,
    create_access_token,
)
from services.auth.src.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest):
    """
    Public Sign-up Endpoint.
    - Requires name, numeric employee_id, @appzlogic.com email, and matching passwords.
    - User is created with is_active=False until activated by an Admin.
    """
    normalized_email = req.email.strip().lower()

    # Check for existing email
    existing_user = await UserModel.get_or_none(email=normalized_email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{normalized_email}' already exists.",
        )

    # Check for existing employee ID
    existing_emp = await UserModel.get_or_none(employee_id=req.employee_id)
    if existing_emp:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with Employee ID '{req.employee_id}' already exists.",
        )

    # Validate password strength
    validate_password_strength(req.password)

    password_hash = hash_password(req.password)
    new_user = await UserModel.create(
        name=req.name,
        employee_id=req.employee_id,
        email=normalized_email,
        password_hash=password_hash,
        role="USER",
        is_active=False,
    )

    logger.info(f"[Auth] New user registration: {new_user.email} (Emp ID: {new_user.employee_id}) - Pending activation")

    return {
        "message": "Account created successfully! Your account is pending administrator activation before you can log in.",
        "user": {
            "id": str(new_user.id),
            "name": new_user.name,
            "employee_id": new_user.employee_id,
            "email": new_user.email,
            "role": new_user.role,
            "is_active": new_user.is_active,
            "created_at": new_user.created_at.isoformat() if new_user.created_at else None,
            "updated_at": new_user.updated_at.isoformat() if new_user.updated_at else None,
        },
    }


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """
    Public Login Endpoint.
    1. Normalize email to lowercase.
    2. Query user by email.
    3. Verify Argon2id password hash.
    4. Check if user is active (return 403 Forbidden if inactive).
    5. Issue JWT access token with role.
    """
    normalized_email = req.email.strip().lower()

    user = await UserModel.get_or_none(email=normalized_email)

    if not user or not verify_password(req.password, user.password_hash):
        # Generic error message to prevent account enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or pending administrator approval. Inactive users cannot log in.",
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
    Returns user identity with name and employee_id.
    """
    return {
        "id": str(current_user.id),
        "name": current_user.name,
        "employee_id": current_user.employee_id,
        "email": current_user.email,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "updated_at": current_user.updated_at.isoformat() if current_user.updated_at else None,
    }
