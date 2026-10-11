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


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verifies a plaintext password against an Argon2id hash. Returns False if hash is missing, invalid, or locked."""
    if not hashed_password or not plain_password or hashed_password.startswith("!"):
        return False
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


import math
from collections import Counter


def calculate_shannon_entropy(s: str) -> float:
    """Calculates Shannon entropy (in bits per character) of a string."""
    if not s:
        return 0.0
    length = len(s)
    counts = Counter(s)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def validate_jwt_secret_configured() -> None:
    """
    Validates that JWT_SECRET_KEY is present and satisfies security requirements.
    Cryptographic strength policy:
      - Rejects known placeholder defaults and trivial patterns.
      - Enforces minimum length >= 32 characters (256 bits).
      - Enforces character class diversity (>= 2 distinct character categories).
      - Enforces minimum unique character count (>= 10 distinct characters).
      - Enforces minimum Shannon entropy (>= 3.0 bits/char) to prevent repeating strings.
    In production environments, raises RuntimeError on policy violation.
    """
    secret = get_jwt_secret()
    is_production = os.getenv("ENVIRONMENT", "").strip().lower() in ("production", "prod")

    insecure_keys = {
        "voicebot_super_secret_jwt_key_2026_change_in_production",
        "secret",
        "changeme",
        "default_secret",
        "admin",
        "password",
    }

    if secret.lower() in insecure_keys:
        msg = "JWT_SECRET_KEY is set to a well-known placeholder default."
        if is_production:
            raise RuntimeError(f"FATAL: {msg} A strong, unique secret key is required in production.")
        logger.warning(f"[Auth-Security] {msg} Update JWT_SECRET_KEY before deploying to production.")

    if len(secret) < 32:
        msg = f"JWT_SECRET_KEY length ({len(secret)} chars) is below the recommended 32 characters (256 bits)."
        if is_production:
            raise RuntimeError(f"FATAL: {msg} HS256 requires a cryptographically strong secret >= 32 characters in production.")
        logger.warning(f"[Auth-Security] {msg}")

    # Cryptographic strength checks (diversity and entropy)
    unique_chars = len(set(secret))
    entropy = calculate_shannon_entropy(secret)
    has_upper = any(c.isupper() for c in secret)
    has_lower = any(c.islower() for c in secret)
    has_digit = any(c.isdigit() for c in secret)
    has_punct = any(not c.isalnum() for c in secret)
    char_classes = sum([has_upper, has_lower, has_digit, has_punct])

    if char_classes < 2 or unique_chars < 10 or entropy < 3.0:
        msg = (
            f"JWT_SECRET_KEY fails cryptographic strength policy "
            f"(classes: {char_classes}/4, unique: {unique_chars}, entropy: {entropy:.2f} bits/char). "
            f"A high-entropy secret (e.g. generated via 'openssl rand -hex 32') is required."
        )
        if is_production:
            raise RuntimeError(f"FATAL: {msg}")
        logger.warning(f"[Auth-Security] {msg}")


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
