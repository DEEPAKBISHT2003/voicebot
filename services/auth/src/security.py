import os
import re
import datetime
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import HTTPException, status
from loguru import logger

# Initialize Argon2id password hasher
# Default Argon2id parameters provide strong collision and side-channel resistance
_ph = PasswordHasher(time_cost=2, memory_cost=102400, parallelism=8)

# Password policy regex requirements:
# - Minimum 8 characters
# - At least 1 uppercase letter
# - At least 1 lowercase letter
# - At least 1 digit
PASSWORD_REGEX = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$')


def validate_password_strength(password: str) -> None:
    """
    Validates that a password satisfies the platform security policy:
      - Minimum 8 characters
      - At least 1 uppercase letter
      - At least 1 lowercase letter
      - At least 1 digit
    Raises HTTPException(422) if invalid.
    """
    if not password or not PASSWORD_REGEX.match(password):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Password does not meet platform security requirements. "
                "It must be at least 8 characters long and contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        )


def hash_password(password: str) -> str:
    """Hashes a plaintext password using Argon2id."""
    return _ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against an Argon2id hash."""
    try:
        return _ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception as e:
        logger.error(f"[Auth] Password verification unexpected error: {e}")
        return False


def get_jwt_secret() -> str:
    """
    Retrieves the configured JWT secret key.
    Raises RuntimeError if missing or empty.
    """
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is not configured or is empty. Please set JWT_SECRET_KEY in environment variables."
        )
    return secret


def validate_jwt_secret_configured() -> None:
    """Validates that JWT_SECRET_KEY is present during startup."""
    get_jwt_secret()


def create_access_token(data: dict) -> str:
    """
    Generates a signed JWT Access Token with:
      - sub: user_id
      - email: user email
      - role: 'ADMIN' or 'USER'
      - exp: expiration unix timestamp
    """
    secret = get_jwt_secret()
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    try:
        expiration_minutes = int(os.getenv("JWT_EXPIRATION_MINUTES", "480"))
    except ValueError:
        expiration_minutes = 480

    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=expiration_minutes)
    to_encode.update({"exp": expire})

    token = jwt.encode(to_encode, secret, algorithm=algorithm)
    return token


def decode_access_token(token: str) -> dict:
    """
    Decodes and validates a JWT token.
    Raises jwt.PyJWTError on invalid, expired, or tampered tokens.
    """
    secret = get_jwt_secret()
    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    return jwt.decode(token, secret, algorithms=[algorithm])
