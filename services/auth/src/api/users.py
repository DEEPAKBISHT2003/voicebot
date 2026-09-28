from typing import List
from fastapi import APIRouter, HTTPException, Depends, status
from loguru import logger

from services.auth.src.models.user import UserModel
from services.auth.src.schemas import UserResponse, UserCreateRequest, UserUpdateRequest
from services.auth.src.security import hash_password, validate_password_strength
from services.auth.src.deps import require_admin

router = APIRouter(prefix="/api/users", tags=["User Management"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    req: UserCreateRequest,
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: Create a new user.
    - Normalizes email to lowercase.
    - Validates password strength policy.
    - Enforces unique email.
    """
    normalized_email = req.email.strip().lower()

    # Check for existing user with normalized email
    existing_user = await UserModel.get_or_none(email=normalized_email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{normalized_email}' already exists.",
        )

    # Validate password against security policy
    validate_password_strength(req.password)

    password_hash = hash_password(req.password)
    new_user = await UserModel.create(
        email=normalized_email,
        password_hash=password_hash,
        role=req.role,
        is_active=True,
    )

    logger.info(f"[Auth] Admin {current_admin.email} created user: {new_user.email} (Role: {new_user.role})")
    return {
        "id": str(new_user.id),
        "email": new_user.email,
        "role": new_user.role,
        "is_active": new_user.is_active,
        "created_at": new_user.created_at.isoformat() if new_user.created_at else None,
        "updated_at": new_user.updated_at.isoformat() if new_user.updated_at else None,
    }


@router.get("", response_model=List[UserResponse])
async def list_users(
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: List all users in the system.
    Never exposes password hashes.
    """
    users = await UserModel.all().order_by("-created_at")
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        for u in users
    ]


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    req: UserUpdateRequest,
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: Update user role or active status.
    Guards:
      - Prevents disabling the last active ADMIN.
      - Prevents changing role of the last active ADMIN to USER.
    """
    target_user = await UserModel.get_or_none(id=user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    # Check if target is an active admin
    is_target_active_admin = (target_user.role == "ADMIN" and target_user.is_active is True)

    if is_target_active_admin:
        active_admin_count = await UserModel.filter(role="ADMIN", is_active=True).count()

        # Prevent disabling the last active admin
        if req.is_active is False and active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot disable the last active administrator.",
            )

        # Prevent demoting the last active admin
        if req.role == "USER" and active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change role of the last active administrator.",
            )

    if req.role is not None:
        target_user.role = req.role

    if req.is_active is not None:
        target_user.is_active = req.is_active

    await target_user.save()
    logger.info(f"[Auth] Admin {current_admin.email} updated user {target_user.email} (Role: {target_user.role}, Active: {target_user.is_active})")

    return {
        "id": str(target_user.id),
        "email": target_user.email,
        "role": target_user.role,
        "is_active": target_user.is_active,
        "created_at": target_user.created_at.isoformat() if target_user.created_at else None,
        "updated_at": target_user.updated_at.isoformat() if target_user.updated_at else None,
    }


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: Delete a user record.
    Guards:
      - Self-delete protection: Administrator cannot delete their own account.
      - Last admin protection: Cannot delete the last administrator.
    """
    target_user = await UserModel.get_or_none(id=user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    # Self-delete protection
    if str(target_user.id) == str(current_admin.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot delete their own account.",
        )

    # Last admin protection
    if target_user.role == "ADMIN":
        total_admin_count = await UserModel.filter(role="ADMIN").count()
        if total_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last administrator.",
            )

    deleted_email = target_user.email
    await target_user.delete()
    logger.info(f"[Auth] Admin {current_admin.email} deleted user {deleted_email}")

    return {"message": "User deleted successfully", "id": user_id, "email": deleted_email}
