import os
import re
import uuid
import datetime
from typing import Optional, Dict, Any, List
import jwt
from jwt import PyJWKClient, PyJWKClientError, InvalidTokenError, ExpiredSignatureError
from fastapi import HTTPException, status
from tortoise.transactions import in_transaction
from loguru import logger

from services.auth.src.models.user import UserModel, UserSessionModel, UserExternalIdentityModel
from services.auth.src.security import create_access_token


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Global JWKClient cache per JWKS URL
_jwks_clients: Dict[str, PyJWKClient] = {}


def is_valid_uuid(val: str) -> bool:
    """Verifies that a string is a valid canonical or hex UUID."""
    if not val:
        return False
    try:
        uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False


def get_microsoft_config() -> Dict[str, str]:
    """
    Returns Microsoft SSO configuration settings from environment variables.
    """
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    api_audience = os.getenv("MICROSOFT_API_AUDIENCE", "").strip() or client_id
    authority = os.getenv(
        "MICROSOFT_AUTHORITY",
        f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else "",
    ).strip().rstrip("/")
    jwks_url = os.getenv(
        "MICROSOFT_JWKS_URL",
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys" if tenant_id else "",
    ).strip()
    allowed_domains = os.getenv("MICROSOFT_ALLOWED_DOMAINS") or os.getenv("MICROSOFT_ALLOWED_DOMAIN", "appzlogic.com")

    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "api_audience": api_audience,
        "authority": authority,
        "jwks_url": jwks_url,
        "allowed_domains": allowed_domains.strip(),
    }


def validate_microsoft_sso_config(fail_on_missing: bool = False) -> None:
    """
    Validates Microsoft Entra SSO environment configuration.
    Fails closed if configuration is partial, malformed, or insecure.
    """
    config = get_microsoft_config()
    tenant_id = config["tenant_id"]
    client_id = config["client_id"]
    authority = config["authority"]

    if not tenant_id and not client_id:
        if fail_on_missing:
            raise RuntimeError(
                "Microsoft SSO configuration is mandatory in production, but MICROSOFT_TENANT_ID "
                "and MICROSOFT_CLIENT_ID are not set."
            )
        return

    if not tenant_id or not client_id:
        missing = "MICROSOFT_TENANT_ID" if not tenant_id else "MICROSOFT_CLIENT_ID"
        raise RuntimeError(
            f"Incomplete Microsoft SSO configuration: {missing} is missing. "
            "Both MICROSOFT_TENANT_ID and MICROSOFT_CLIENT_ID are required for single-tenant enterprise SSO."
        )

    if not is_valid_uuid(tenant_id):
        raise RuntimeError(
            f"Invalid MICROSOFT_TENANT_ID format: '{tenant_id}' is not a valid UUID."
        )
    if not is_valid_uuid(client_id):
        raise RuntimeError(
            f"Invalid MICROSOFT_CLIENT_ID format: '{client_id}' is not a valid UUID."
        )

    if authority:
        disallowed_segments = ["/common", "/organizations", "/consumers"]
        if any(authority.lower().endswith(seg) for seg in disallowed_segments):
            raise RuntimeError(
                f"Insecure MICROSOFT_AUTHORITY: '{authority}'. Multi-tenant endpoints (/common, /organizations) "
                "are prohibited for single-tenant Appzlogic authentication. Use single-tenant authority."
            )


def is_microsoft_sso_configured() -> bool:
    """
    Verifies whether Microsoft SSO has valid and complete environment configuration.
    Returns False if unconfigured or malformed.
    """
    config = get_microsoft_config()
    tenant_id = config["tenant_id"]
    client_id = config["client_id"]
    if not (tenant_id and client_id):
        return False
    return is_valid_uuid(tenant_id) and is_valid_uuid(client_id)


def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """
    Retrieves or creates a cached PyJWKClient for the given JWKS URL.
    PyJWKClient handles HTTP retrieval, in-memory caching, and key rotation.
    """
    if jwks_url not in _jwks_clients:
        _jwks_clients[jwks_url] = PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=16)
    return _jwks_clients[jwks_url]


def verify_microsoft_token(token: str, custom_signing_key=None) -> Dict[str, Any]:
    """
    Validates a Microsoft Entra ID access token server-side.
    
    Performs:
    1. Check SSO configuration.
    2. Extract signing key (kid) via JWKS client (with key rotation support).
    3. Verify cryptographic RS256 signature against trusted Microsoft public keys.
    4. Validate expiration (exp), not-before (nbf), audience (aud), and issuer (iss).
    5. Enforce single-tenant restriction: token tenant ID (tid) must match configured MICROSOFT_TENANT_ID.
    6. Ensure required identity claims (oid, tid, and email/preferred_username) are present.
    
    Returns decoded token claims dictionary.
    Raises HTTPException with generic error message on any failure.
    """
    config = get_microsoft_config()
    if not is_microsoft_sso_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microsoft SSO is not configured on this server.",
        )

    expected_tenant_id = config["tenant_id"]
    api_aud = config["api_audience"]
    client_id = config["client_id"]

    if api_aud and api_aud != client_id and api_aud != f"api://{client_id}":
        # Separate App Registrations: API audience is strictly distinct from frontend client ID
        expected_audiences = [api_aud]
        if not api_aud.startswith("api://"):
            expected_audiences.append(f"api://{api_aud}")
    else:
        # Unified App Registration: client ID and API audience share same application registration
        expected_audiences = list(filter(None, [
            api_aud,
            client_id,
            f"api://{client_id}" if client_id else "",
            f"api://{api_aud}" if api_aud else "",
        ]))
    
    # Valid Entra issuers for the specific single tenant
    allowed_issuers = [
        f"https://login.microsoftonline.com/{expected_tenant_id}/v2.0",
        f"https://sts.windows.net/{expected_tenant_id}/",
        f"https://login.microsoftonline.com/{expected_tenant_id}",
    ]

    try:
        # 1. Unverified header inspection to check algorithm and kid
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg")
        if alg != "RS256":
            logger.warning(f"[Auth-Microsoft] Invalid token algorithm: {alg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: unsupported signing algorithm.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Retrieve signing key
        if custom_signing_key is not None:
            signing_key = custom_signing_key
        else:
            jwks_client = get_jwks_client(config["jwks_url"])
            signing_key = jwks_client.get_signing_key_from_jwt(token).key

        # 3. Decode and verify signature, expiration, audience
        # Notice: audience can match any of the configured acceptable audience formats
        claims = None
        decode_error = None
        for audience in expected_audiences:
            try:
                claims = jwt.decode(
                    token,
                    signing_key,
                    algorithms=["RS256"],
                    audience=audience,
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": True,
                        "require": ["exp", "iss"],
                    },
                )
                break
            except InvalidTokenError as e:
                decode_error = e

        if claims is None:
            if isinstance(decode_error, ExpiredSignatureError):
                logger.info("[Auth-Microsoft] Microsoft token has expired.")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Microsoft token has expired. Please sign in again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            logger.warning(f"[Auth-Microsoft] Token validation failed: {decode_error}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Microsoft token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 4. Validate Issuer against allowed issuers for this tenant
        issuer = claims.get("iss", "").rstrip("/")
        if not any(issuer == allowed.rstrip("/") for allowed in allowed_issuers):
            logger.warning(f"[Auth-Microsoft] Untrusted token issuer: {issuer}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token issuer.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 5. Enforce single-tenant restriction (tid claim)
        token_tid = claims.get("tid")
        if not token_tid or token_tid != expected_tenant_id:
            logger.warning(f"[Auth-Microsoft] Tenant ID mismatch: received {token_tid}, expected {expected_tenant_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted: account belongs to an unauthorized tenant.",
            )

        # 6. Reject ID tokens and app-only tokens:
        # Employee Single Sign-On requires a delegated user access token with the 'scp' claim.
        # ID tokens (no scp/roles) and app-only daemon tokens (roles only, no scp) are strictly rejected.
        scopes = claims.get("scp")
        if not scopes:
            logger.warning("[Auth-Microsoft] Token missing 'scp' claim (ID token or app-only token rejected).")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: a delegated user access token with authorized permissions is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 7. Validate Authorized Party (azp in v2.0 or appid in v1.0)
        # When client validation is configured, authorized client cannot be bypassed by omitting both azp and appid
        azp = claims.get("azp") or claims.get("appid")
        expected_client_id = config.get("client_id")
        if expected_client_id:
            if not azp:
                logger.warning("[Auth-Microsoft] Token missing authorized party ('azp' or 'appid') claim.")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access restricted: token lacks authorized client application claim.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if azp != expected_client_id:
                logger.warning(f"[Auth-Microsoft] Authorized party mismatch: token azp={azp}, expected={expected_client_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access restricted: token was issued to an unauthorized client application.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        # 8. Validate required delegated scope
        required_scope = os.getenv("MICROSOFT_REQUIRED_SCOPE", "access_as_user").strip()
        if required_scope:
            granted_scopes = scopes.split()
            if required_scope not in granted_scopes:
                logger.warning(f"[Auth-Microsoft] Token lacks required scope '{required_scope}': granted {granted_scopes}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: token lacks required API permission ('{required_scope}').",
                )

        # 9. Validate identity claims (oid is immutable Microsoft object ID)
        oid = claims.get("oid")
        if not oid:
            logger.warning("[Auth-Microsoft] Token missing required 'oid' claim.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject identity claim.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Resolve email claim
        email = (
            claims.get("email")
            or claims.get("preferred_username")
            or claims.get("upn")
            or ""
        ).strip().lower()

        if not email or not EMAIL_REGEX.match(email):
            logger.warning("[Auth-Microsoft] Token missing valid email claim.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing or malformed email claim.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 10. Enforce employee domain restriction (Guest / External account restriction)
        # Prevents invited B2B guests or non-employee accounts in the tenant from accessing the platform
        allowed_domains_cfg = config.get("allowed_domains", "appzlogic.com")
        if allowed_domains_cfg and allowed_domains_cfg != "*":
            allowed_domains = [d.strip().lower().lstrip("@") for d in allowed_domains_cfg.split(",") if d.strip()]
            user_domain = email.split("@")[-1] if "@" in email else ""
            if user_domain not in allowed_domains:
                logger.warning(
                    f"[Auth-Microsoft] Guest/external account rejected: email '{email}' with domain '@{user_domain}' "
                    f"is not in permitted employee domains {allowed_domains}."
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access restricted: only verified organizational employee accounts ({', '.join('@' + d for d in allowed_domains)}) are permitted.",
                )

        # Cleaned verified identity dictionary
        return {
            "oid": oid,
            "tid": token_tid,
            "email": email,
            "name": claims.get("name") or email.split("@")[0],
            "raw_claims": claims,
        }

    except HTTPException:
        raise
    except PyJWKClientError as e:
        logger.error(f"[Auth-Microsoft] JWKS key retrieval error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to verify Microsoft token signing key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"[Auth-Microsoft] Unexpected error verifying Microsoft token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Microsoft authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def provision_or_resolve_microsoft_user(verified_claims: Dict[str, Any]) -> UserModel:
    """
    Resolves or provisions a local UserModel based on verified Microsoft identity claims.
    
    Security Controls:
    1. Looks up existing federated identity in `user_external_identities` by (provider='microsoft', tid, oid).
    2. If linked identity exists:
       - Retrieves local user.
       - Enforces active status: inactive users receive 403 Forbidden.
       - Returns local user.
    3. If NO linked identity exists:
       - Checks whether an unlinked local user already exists with the same email.
       - PREVENTS ACCOUNT TAKEOVER: If unlinked email matches, rejects login with 409 Conflict.
         Unlinked accounts cannot be silently claimed by third-party identities.
       - If no account exists:
         - Provisions a new user account with role='USER' (NEVER 'ADMIN').
         - Creates federated link in `user_external_identities`.
         - Wrapped in atomic transaction to prevent concurrent duplicate race conditions.
    """
    oid = verified_claims["oid"]
    tid = verified_claims["tid"]
    email = verified_claims["email"]
    name = verified_claims.get("name")

    async with in_transaction():
        # 1. Check for existing external identity linkage
        existing_identity = await UserExternalIdentityModel.get_or_none(
            provider="microsoft",
            tenant_id=tid,
            provider_user_id=oid,
        )

        if existing_identity:
            user = await UserModel.get_or_none(id=existing_identity.user_id)
            if not user:
                logger.error(f"[Auth-Microsoft] Orphaned external identity {existing_identity.id} pointing to user {existing_identity.user_id}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User account associated with this Microsoft identity no longer exists.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if not user.is_active:
                logger.warning(f"[Auth-Microsoft] Attempted sign-in for disabled user {user.email}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This account is disabled. Please contact an administrator.",
                )

            # Update name if previously null
            if name and not user.name:
                user.name = name
                await user.save(update_fields=["name", "updated_at"])

            return user

        # 2. Check if a local user already exists with this email address
        existing_email_user = await UserModel.get_or_none(email=email)
        if existing_email_user:
            logger.warning(
                f"[Auth-Microsoft] Unlinked account conflict: Microsoft OID {oid} with email {email} "
                f"matches existing local user {existing_email_user.id}. Automatic linking rejected."
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "An account with this email address already exists. "
                    "For security, automatic linking is disabled. "
                    "Please sign in with your email and password, or contact an administrator to link your Microsoft account."
                ),
            )

        # 3. First-time provisioning: create standard user with role 'USER'
        # SSO-only user has no local password hash. If underlying database enforces a NOT NULL constraint,
        # fallback to an unmatchable locked shadow string "!SSO_MANAGED_IDENTITY!".
        try:
            new_user = await UserModel.create(
                email=email,
                name=name,
                password_hash=None,
                role="USER",
                is_active=True,
            )
        except Exception as e:
            if "password_hash" in str(e).lower() or "not null" in str(e).lower():
                logger.info(f"[Auth-Microsoft] Legacy NOT NULL password_hash detected; applying locked SSO marker for {email}")
                new_user = await UserModel.create(
                    email=email,
                    name=name,
                    password_hash="!SSO_MANAGED_IDENTITY!",
                    role="USER",
                    is_active=True,
                )
            else:
                raise

        await UserExternalIdentityModel.create(
            user_id=new_user.id,
            provider="microsoft",
            tenant_id=tid,
            provider_user_id=oid,
            email=email,
        )

        logger.info(f"[Auth-Microsoft] Successfully provisioned new standard user: {email} (ID: {new_user.id}, OID: {oid})")
        return new_user


async def process_microsoft_login(token: str, custom_signing_key=None) -> Dict[str, Any]:
    """
    Full Microsoft SSO workflow:
    1. Validates Microsoft token.
    2. Resolves or provisions local user.
    3. Updates user login and activity timestamps.
    4. Records new UserSession.
    5. Issues platform JWT token containing session_id.
    """
    verified_claims = verify_microsoft_token(token, custom_signing_key=custom_signing_key)
    user = await provision_or_resolve_microsoft_user(verified_claims)

    now = datetime.datetime.now(datetime.timezone.utc)
    session_id = str(uuid.uuid4())

    user.last_login_at = now
    user.last_activity_at = now
    await user.save(update_fields=["last_login_at", "last_activity_at", "updated_at"])

    await UserSessionModel.create(
        user_id=user.id,
        session_id=session_id,
        login_at=now,
        last_seen_at=now,
        is_active=True,
    )

    token_payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "session_id": session_id,
    }
    access_token = create_access_token(token_payload)

    logger.info(f"[Auth-Microsoft] Successful SSO login for user: {user.email} (Role: {user.role}, Session: {session_id})")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "session_id": session_id,
    }
