import os
from loguru import logger
from services.auth.src.models.user import UserModel
from services.auth.src.security import hash_password


async def seed_admin_user() -> None:
    """
    Bootstrap administrator account on application startup.
    Rules:
      1. Check whether ANY active or inactive admin exists in the database.
      2. If an admin already exists, do nothing (never overwrite or reset).
      3. If no admin exists:
         - Read ADMIN_EMAIL and ADMIN_PASSWORD from environment variables.
         - If either is missing or empty, log a warning and skip creation (no hardcoded credentials).
         - Normalize email to lowercase and hash password with Argon2id.
         - Insert the initial ADMIN account into the database.
    """
    try:
        admin_exists = await UserModel.filter(role="ADMIN").exists()
        if admin_exists:
            logger.info("[Auth] Administrator account already exists. Skipping bootstrap.")
            return

        admin_email = os.getenv("ADMIN_EMAIL", "").strip()
        admin_password = os.getenv("ADMIN_PASSWORD", "").strip()

        if not admin_email or not admin_password:
            logger.warning(
                "[Auth] ADMIN_EMAIL or ADMIN_PASSWORD environment variable is missing or empty. "
                "Skipping initial administrator account bootstrap. Configure both variables to initialize admin."
            )
            return

        normalized_email = admin_email.lower()
        password_hash = hash_password(admin_password)

        admin_user = await UserModel.create(
            email=normalized_email,
            password_hash=password_hash,
            role="ADMIN",
            is_active=True,
        )
        logger.info(f"[Auth] Successfully bootstrapped initial administrator account: {admin_user.email}")
    except Exception as e:
        logger.error(f"[Auth] Failed to seed initial administrator: {e}")
