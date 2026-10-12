import os
import time
import uuid
import datetime
import asyncio
import pytest
import pytest_asyncio
import jwt
from httpx import AsyncClient, ASGITransport
from tortoise import Tortoise
from cryptography.hazmat.primitives.asymmetric import rsa

# Configure environment for tests
TEST_TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
TEST_CLIENT_ID = "11111111-2222-3333-4444-555555555555"
TEST_AUDIENCE = f"api://{TEST_CLIENT_ID}"

os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-microsoft-auth-unit-tests-12345"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["MICROSOFT_TENANT_ID"] = TEST_TENANT_ID
os.environ["MICROSOFT_CLIENT_ID"] = TEST_CLIENT_ID
os.environ["MICROSOFT_API_AUDIENCE"] = TEST_AUDIENCE

from services.main import app
os.environ["MICROSOFT_TENANT_ID"] = TEST_TENANT_ID
os.environ["MICROSOFT_CLIENT_ID"] = TEST_CLIENT_ID
os.environ["MICROSOFT_API_AUDIENCE"] = TEST_AUDIENCE
from services.auth.src.models.user import UserModel, UserSessionModel, UserExternalIdentityModel
from services.auth.src.security import hash_password, decode_access_token
from services.auth.src.microsoft import process_microsoft_login, verify_microsoft_token
from services.auth.src.middleware import clear_activity_throttle_cache
from services.auth.src.api.auth import reset_auth_rate_limits


# Generate RSA keypair for RS256 cryptographic tests
_rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_rsa_public_key = _rsa_private_key.public_key()

_other_rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def create_mock_ms_token(
    sub="test-ms-oid-123",
    oid="test-ms-oid-123",
    tid=TEST_TENANT_ID,
    email="alice@appzlogic.com",
    name="Alice Engineer",
    aud=TEST_AUDIENCE,
    iss=f"https://login.microsoftonline.com/{TEST_TENANT_ID}/v2.0",
    exp_delta=datetime.timedelta(hours=1),
    signing_key=_rsa_private_key,
    headers=None,
    extra_claims=None,
):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": sub,
        "oid": oid,
        "tid": tid,
        "email": email,
        "name": name,
        "aud": aud,
        "iss": iss,
        "nbf": int(now.timestamp()) - 10,
        "iat": int(now.timestamp()) - 10,
        "exp": int((now + exp_delta).timestamp()),
        "scp": "access_as_user",
        "azp": TEST_CLIENT_ID,
    }
    if extra_claims:
        payload.update(extra_claims)

    token_headers = {"kid": "mock-test-key-id", "alg": "RS256"}
    if headers:
        token_headers.update(headers)

    return jwt.encode(payload, signing_key, algorithm="RS256", headers=token_headers)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "services.auth.src.models.user",
                "services.copilot.src.models.copilot",
                "services.interview.src.models.interview",
            ]
        },
    )
    await Tortoise.generate_schemas()
    clear_activity_throttle_cache()
    reset_auth_rate_limits()
    yield
    await Tortoise.close_connections()


@pytest.fixture(autouse=True)
def mock_jwks_key(monkeypatch):
    """Bypasses external network calls to Microsoft JWKS by providing local test public key."""
    from services.auth.src import microsoft

    monkeypatch.setenv("MICROSOFT_TENANT_ID", TEST_TENANT_ID)
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", TEST_CLIENT_ID)
    monkeypatch.setenv("MICROSOFT_API_AUDIENCE", TEST_AUDIENCE)

    class MockSigningKey:
        key = _rsa_public_key

    class MockJWKClient:
        def get_signing_key_from_jwt(self, token):
            return MockSigningKey()

    monkeypatch.setattr(microsoft, "get_jwks_client", lambda url: MockJWKClient())


# ==============================================================================
# 1. POSITIVE AUTHENTICATION & PROVISIONING TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_first_time_microsoft_user_provisioning():
    """First-time valid Microsoft sign-in provisions a standard USER and issues platform JWT."""
    token = create_mock_ms_token(
        oid="ms-user-oid-001",
        email="newuser@appzlogic.com",
        name="New Employee",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res.status_code == 200, res.text
        data = res.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == "USER"
        assert data["session_id"] is not None

        # Verify platform JWT payload
        claims = decode_access_token(data["access_token"])
        assert claims["email"] == "newuser@appzlogic.com"
        assert claims["role"] == "USER"
        assert claims["session_id"] == data["session_id"]

        # Verify local database user record
        user = await UserModel.get(email="newuser@appzlogic.com")
        assert user.role == "USER"
        assert user.is_active is True
        assert user.password_hash is None
        assert user.last_login_at is not None

        # Verify external identity linkage
        ext_ident = await UserExternalIdentityModel.get(
            provider="microsoft",
            tenant_id=TEST_TENANT_ID,
            provider_user_id="ms-user-oid-001",
        )
        assert ext_ident.user_id == user.id
        assert ext_ident.email == "newuser@appzlogic.com"


@pytest.mark.asyncio
async def test_repeat_microsoft_login_resolves_same_user():
    """Subsequent logins by the same Microsoft user resolve the existing account and create new session."""
    token = create_mock_ms_token(
        oid="ms-user-oid-repeat",
        email="repeat@appzlogic.com",
        name="Repeat User",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First login
        res1 = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res1.status_code == 200
        user1 = await UserModel.get(email="repeat@appzlogic.com")

        # Second login
        res2 = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res2.status_code == 200
        data2 = res2.json()

        user2 = await UserModel.get(email="repeat@appzlogic.com")
        assert user1.id == user2.id

        # Verify separate sessions recorded
        sessions = await UserSessionModel.filter(user_id=user1.id).all()
        assert len(sessions) == 2


@pytest.mark.asyncio
async def test_admin_role_escalation_prevention():
    """Ensure newly provisioned accounts can NEVER be given ADMIN role via token claims or inputs."""
    # Attacker crafts token claiming roles=["ADMIN", "GlobalAdmin"] in Microsoft directory
    token = create_mock_ms_token(
        oid="ms-user-oid-attacker",
        email="hacker@appzlogic.com",
        extra_claims={"roles": ["ADMIN", "GlobalAdmin"], "wids": ["62e90394-69f5-4237-9190-012177145e10"]},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res.status_code == 200
        assert res.json()["role"] == "USER"

        user = await UserModel.get(email="hacker@appzlogic.com")
        assert user.role == "USER"


# ==============================================================================
# 2. SECURITY VALIDATION & TAMPERING TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_reject_wrong_signature():
    """Token signed by an untrusted or different RSA private key must be rejected with 401."""
    tampered_token = create_mock_ms_token(
        signing_key=_other_rsa_private_key,  # Wrong key
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": tampered_token})
        assert res.status_code == 401
        assert "Invalid Microsoft token" in res.json()["detail"]


@pytest.mark.asyncio
async def test_reject_expired_token():
    """Expired Microsoft tokens must be rejected with 401."""
    expired_token = create_mock_ms_token(
        exp_delta=datetime.timedelta(minutes=-10),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": expired_token})
        assert res.status_code == 401
        assert "expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_wrong_audience():
    """Token intended for a different application or Graph must be rejected with 401."""
    wrong_aud_token = create_mock_ms_token(
        aud="https://graph.microsoft.com",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": wrong_aud_token})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_reject_wrong_tenant():
    """Token issued for an unauthorized Microsoft tenant must be rejected with 403."""
    foreign_tenant_id = "00000000-0000-0000-0000-foreign00001"
    wrong_tenant_token = create_mock_ms_token(
        tid=foreign_tenant_id,
        iss=f"https://login.microsoftonline.com/{foreign_tenant_id}/v2.0",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": wrong_tenant_token})
        assert res.status_code in [401, 403]
        assert "tenant" in res.json()["detail"].lower() or "issuer" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_wrong_issuer():
    """Token with untrusted issuer domain must be rejected with 401."""
    fake_iss_token = create_mock_ms_token(
        iss="https://evil-attacker-site.com/auth",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": fake_iss_token})
        assert res.status_code == 401
        assert "issuer" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_missing_required_claims():
    """Token missing oid or valid email must be rejected."""
    # Missing oid
    no_oid_token = create_mock_ms_token(oid="")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": no_oid_token})
        assert res.status_code == 401
        assert "subject identity" in res.json()["detail"].lower() or "oid" in res.json()["detail"].lower()

    # Missing email
    no_email_token = create_mock_ms_token(email="", extra_claims={"preferred_username": "", "upn": ""})
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": no_email_token})
        assert res.status_code == 401
        assert "email" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_malformed_token():
    """Malformed token string must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": "not-a-valid-jwt-token-string"})
        assert res.status_code in [401, 422]


# ==============================================================================
# 3. ACCOUNT INTEGRITY & UNLINKED ACCOUNT PROTECTION
# ==============================================================================

@pytest.mark.asyncio
async def test_prevent_automatic_takeover_of_unlinked_existing_account():
    """
    CRITICAL SECURITY CHECK:
    If a local account already exists with password auth, an incoming Microsoft login with the
    same email that is NOT already federated must be REJECTED (409 Conflict) to prevent account takeover.
    """
    # Pre-existing local password-based account
    existing_user = await UserModel.create(
        email="bob@appzlogic.com",
        name="Bob Regular",
        password_hash=hash_password("BobSecretPass123!"),
        role="USER",
        is_active=True,
    )

    # External Microsoft account presenting the same email but never linked
    token = create_mock_ms_token(
        oid="ms-external-bob-oid",
        email="bob@appzlogic.com",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res.status_code == 409
        assert "already exists" in res.json()["detail"]

        # Ensure no linkage was forged
        ident = await UserExternalIdentityModel.get_or_none(provider_user_id="ms-external-bob-oid")
        assert ident is None


@pytest.mark.asyncio
async def test_reject_disabled_local_account():
    """Disabled accounts must be rejected with 403 Forbidden even if Microsoft token is valid."""
    user = await UserModel.create(
        email="inactive@appzlogic.com",
        name="Inactive Person",
        password_hash=None,
        role="USER",
        is_active=False,  # Disabled
    )
    await UserExternalIdentityModel.create(
        user_id=user.id,
        provider="microsoft",
        tenant_id=TEST_TENANT_ID,
        provider_user_id="inactive-oid-999",
        email="inactive@appzlogic.com",
    )

    token = create_mock_ms_token(
        oid="inactive-oid-999",
        email="inactive@appzlogic.com",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/microsoft", json={"access_token": token})
        assert res.status_code == 403
        assert "disabled" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_concurrent_provisioning_duplicate_prevention():
    """Concurrent login requests for a new user must resolve safely without creating duplicate users."""
    token = create_mock_ms_token(
        oid="concurrent-oid-123",
        email="concurrent@appzlogic.com",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Run 3 concurrent requests with the same token
        res1, res2, res3 = await asyncio.gather(
            client.post("/api/auth/microsoft", json={"access_token": token}),
            client.post("/api/auth/microsoft", json={"access_token": token}),
            client.post("/api/auth/microsoft", json={"access_token": token}),
        )

        # All requests succeed
        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res3.status_code == 200

        # Only 1 user record created
        users = await UserModel.filter(email="concurrent@appzlogic.com").all()
        assert len(users) == 1

        # Only 1 external identity linkage
        identities = await UserExternalIdentityModel.filter(provider_user_id="concurrent-oid-123").all()
        assert len(identities) == 1


# ==============================================================================
# 4. REGRESSION & CONFIGURATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_email_password_login_continues_working():
    """Existing password login remains functional and regression-free."""
    await UserModel.create(
        email="passuser@appzlogic.com",
        name="Password User",
        password_hash=hash_password("StrongPassword123!"),
        role="USER",
        is_active=True,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Correct password
        res = await client.post(
            "/api/auth/login",
            json={"email": "passuser@appzlogic.com", "password": "StrongPassword123!"},
        )
        assert res.status_code == 200
        assert "access_token" in res.json()

        # Wrong password
        bad_res = await client.post(
            "/api/auth/login",
            json={"email": "passuser@appzlogic.com", "password": "WrongPassword123!"},
        )
        assert bad_res.status_code == 401


@pytest.mark.asyncio
async def test_missing_microsoft_configuration_handled_safely(monkeypatch):
    """When Microsoft configuration is missing, /microsoft returns 503 while password login works."""
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "")
    monkeypatch.setenv("MICROSOFT_API_AUDIENCE", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Status endpoint reports unconfigured
        status_res = await client.get("/api/auth/microsoft/status")
        assert status_res.status_code == 200
        assert status_res.json()["configured"] is False

        # Attempting Microsoft login returns 503
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": "any-token-string-value"},
        )
        assert res.status_code == 503


@pytest.mark.asyncio
async def test_reject_id_token_lacking_scopes_or_roles():
    """Security Audit: Pure ID tokens (missing scp and roles) are strictly rejected as API access tokens."""
    token = create_mock_ms_token(extra_claims={"scp": None, "roles": None})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token},
        )
        assert res.status_code == 401
        assert "delegated user access token" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_app_only_token_lacking_delegated_scope():
    """Security Audit: App-only daemon tokens (roles only, missing scp) are strictly rejected as employee SSO tokens."""
    token = create_mock_ms_token(extra_claims={"scp": None, "roles": ["Reports.Read.All"]})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token},
        )
        assert res.status_code == 401
        assert "delegated user access token" in res.json()["detail"].lower()



@pytest.mark.asyncio
async def test_reject_unauthorized_client_application_azp():
    """Security Audit: Tokens requested by an unapproved client application (mismatched azp) are rejected."""
    unauthorized_client_id = "99999999-8888-7777-6666-555555555555"
    token = create_mock_ms_token(extra_claims={"azp": unauthorized_client_id})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token},
        )
        assert res.status_code == 401
        assert "unauthorized client" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_token_missing_required_scope():
    """Security Audit: Tokens with wrong delegated scopes (e.g. only User.Read) are rejected."""
    token = create_mock_ms_token(extra_claims={"scp": "User.Read Files.Read"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token},
        )
        assert res.status_code == 403
        assert "access_as_user" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_auth_rate_limiting_blocks_brute_force(monkeypatch):
    """Security Audit (FINDING-04): Repeated authentication requests exceeding threshold return 429 Too Many Requests."""
    reset_auth_rate_limits()
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "5")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First 5 attempts execute (return 401 for wrong credentials)
        for _ in range(5):
            res = await client.post(
                "/api/auth/login",
                json={"email": "nonexistent@appzlogic.com", "password": "WrongPassword1!"},
            )
            assert res.status_code == 401

        # 6th attempt exceeds threshold -> MUST return 429 Too Many Requests
        rate_limited_res = await client.post(
            "/api/auth/login",
            json={"email": "nonexistent@appzlogic.com", "password": "WrongPassword1!"},
        )
        assert rate_limited_res.status_code == 429
        assert "Too many authentication attempts" in rate_limited_res.json()["detail"]

    reset_auth_rate_limits()


@pytest.mark.asyncio
async def test_reject_token_omitting_both_azp_and_appid():
    """Security Audit: Ensure authorized-client restriction cannot be bypassed by omitting azp and appid."""
    # Create token without azp or appid
    token = create_mock_ms_token(extra_claims={"azp": None, "appid": None})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token},
        )
        assert res.status_code == 401
        assert "authorized client application" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rate_limiter_trusted_proxy_cidr_and_spoofing(monkeypatch):
    """Security Audit: Verify CIDR proxy parsing and spoofing rejection for untrusted clients."""
    from services.auth.src.rate_limiter import is_trusted_proxy, extract_client_ip
    from starlette.requests import Request

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8,192.168.1.50")
    
    # 1. CIDR address matching
    assert is_trusted_proxy("10.1.2.3") is True
    assert is_trusted_proxy("192.168.1.50") is True
    assert is_trusted_proxy("192.168.1.51") is False
    assert is_trusted_proxy("203.0.113.195") is False

    # 2. Direct client attempting to spoof X-Real-IP is ignored
    class DummyClient:
        host = "203.0.113.195"

    scope = {
        "type": "http",
        "client": (DummyClient.host, 12345),
        "headers": [(b"x-real-ip", b"1.1.1.1"), (b"x-forwarded-for", b"2.2.2.2")],
    }
    req = Request(scope)
    assert extract_client_ip(req) == "203.0.113.195"

    # 3. Trusted proxy X-Real-IP is accepted
    trusted_scope = {
        "type": "http",
        "client": ("10.5.5.5", 12345),
        "headers": [(b"x-real-ip", b"203.0.113.50")],
    }
    trusted_req = Request(trusted_scope)
    assert extract_client_ip(trusted_req) == "203.0.113.50"


def test_cors_production_origin_validation(monkeypatch):
    """Security Audit: Verify production CORS rejects wildcards, localhost, paths, queries."""
    from services.main import get_cors_origins

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FRONTEND_URL", raising=False)

    # 1. Wildcard rejected
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="Wildcard"):
        get_cors_origins()

    # 2. Localhost in production rejected
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    with pytest.raises(ValueError, match="Localhost/loopback"):
        get_cors_origins()

    # 3. Path in origin rejected
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://interview.appzlogic.com/path")
    with pytest.raises(ValueError, match="paths are not allowed"):
        get_cors_origins()

    # 4. Valid production domain accepted
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://interview.appzlogic.com")
    origins = get_cors_origins()
    assert origins == ["https://interview.appzlogic.com"]


@pytest.mark.asyncio
async def test_rollback_sso_migration_disposable_db(tmp_path, monkeypatch):
    """Security Audit: Run rollback on disposable DB with password and SSO users."""
    from scripts.rollback_sso_migration import run_rollback
    from services.auth.src.security import verify_password, hash_password

    disposable_db = str(tmp_path / "rollback_test.sqlite")
    disposable_url = f"sqlite:///{disposable_db}"
    monkeypatch.setenv("DATABASE_URL", disposable_url)

    # Initialize disposable DB
    await Tortoise.init(
        db_url=disposable_url,
        modules={"models": ["services.auth.src.models.user"]},
    )
    await Tortoise.generate_schemas()

    # Seed 1 password user and 1 SSO user (password_hash is None)
    pwd_user = await UserModel.create(
        email="password_user@appzlogic.com",
        password_hash=hash_password("ValidPassword123!"),
        role="USER",
        is_active=True,
    )
    sso_user = await UserModel.create(
        email="sso_user@appzlogic.com",
        password_hash=None,
        role="USER",
        is_active=True,
    )

    await Tortoise.close_connections()

    # 1. Test Dry Run (makes no changes)
    dry_run_success = await run_rollback(dry_run=True, db_url=disposable_url)
    assert dry_run_success is True

    await Tortoise.init(
        db_url=disposable_url,
        modules={"models": ["services.auth.src.models.user"]},
    )
    # Add related records to verify relational integrity preservation
    await UserExternalIdentityModel.create(
        user_id=sso_user.id,
        provider="microsoft",
        tenant_id=TEST_TENANT_ID,
        provider_user_id="oid-12345",
        email=sso_user.email,
    )
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    await UserSessionModel.create(
        session_id=str(uuid.uuid4()),
        user_id=sso_user.id,
        login_at=now_utc,
        last_seen_at=now_utc,
        is_active=True,
    )

    reloaded_sso = await UserModel.get(id=sso_user.id)
    assert reloaded_sso.password_hash is None
    await Tortoise.close_connections()

    # 2. Test Execution
    rollback_success = await run_rollback(dry_run=False, db_url=disposable_url)
    assert rollback_success is True

    # 3. Verify changes in DB
    await Tortoise.init(
        db_url=disposable_url,
        modules={"models": ["services.auth.src.models.user"]},
    )
    reloaded_sso = await UserModel.get(id=sso_user.id)
    reloaded_pwd = await UserModel.get(id=pwd_user.id)

    # SSO user has locked shadow hash
    assert reloaded_sso.password_hash == "!LOCKED_SSO_ROLLBACK!"
    # Password verification CANNOT match locked shadow hash with any password
    assert verify_password("ValidPassword123!", reloaded_sso.password_hash) is False
    assert verify_password("!LOCKED_SSO_ROLLBACK!", reloaded_sso.password_hash) is False
    assert verify_password("", reloaded_sso.password_hash) is False

    # Related records remain intact and linked
    related_identities = await UserExternalIdentityModel.filter(user_id=sso_user.id)
    assert len(related_identities) == 1
    related_sessions = await UserSessionModel.filter(user_id=sso_user.id)
    assert len(related_sessions) == 1

    # Password user remains intact and can still authenticate
    assert verify_password("ValidPassword123!", reloaded_pwd.password_hash) is True

    await Tortoise.close_connections()

    # 4. Idempotency test (safe to rerun)
    rerun_success = await run_rollback(dry_run=False, db_url=disposable_url)
    assert rerun_success is True


def test_jwt_secret_cryptographic_strength_policy(monkeypatch):
    """Phase 3 Verification: Verify cryptographic strength policy for JWT secrets in production."""
    from services.auth.src.security import validate_jwt_secret_configured

    monkeypatch.setenv("ENVIRONMENT", "production")

    # 1. Known placeholder fails
    monkeypatch.setenv("JWT_SECRET_KEY", "voicebot_super_secret_jwt_key_2026_change_in_production")
    with pytest.raises(RuntimeError, match="well-known placeholder"):
        validate_jwt_secret_configured()

    # 2. Short secret fails (< 32 chars)
    monkeypatch.setenv("JWT_SECRET_KEY", "short_secret_12345")
    with pytest.raises(RuntimeError, match="below the recommended 32 characters"):
        validate_jwt_secret_configured()

    # 3. Low entropy repetitive secret fails (32 chars of 'a')
    monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
    with pytest.raises(RuntimeError, match="cryptographic strength policy"):
        validate_jwt_secret_configured()

    # 4. All digits fails
    monkeypatch.setenv("JWT_SECRET_KEY", "12345678901234567890123456789012")
    with pytest.raises(RuntimeError, match="cryptographic strength policy"):
        validate_jwt_secret_configured()

    # 5. Strong cryptographic secret succeeds (64 chars hex)
    monkeypatch.setenv("JWT_SECRET_KEY", "d8f8a1e3b5c749a2e6f014829374618290374619283746182937461928374612")
    validate_jwt_secret_configured()  # Should not raise


@pytest.mark.asyncio
async def test_rate_limiter_multiple_instances_and_pruning():
    """Phase 2 Verification: Verify isolated limiter instances, pruning, and IPv6 address support."""
    from services.auth.src.rate_limiter import SlidingWindowLimiter
    from fastapi import HTTPException

    limiter_a = SlidingWindowLimiter(max_stored_ips=2)
    limiter_b = SlidingWindowLimiter(max_stored_ips=2)

    # Limiter A threshold exceeded
    for _ in range(3):
        await limiter_a.check(client_ip="::1", scope="login", max_attempts=3, window_seconds=60.0)

    with pytest.raises(HTTPException) as exc:
        await limiter_a.check(client_ip="::1", scope="login", max_attempts=3, window_seconds=60.0)
    assert exc.value.status_code == 429

    # Limiter B on same IP is completely unaffected (independent instances)
    await limiter_b.check(client_ip="::1", scope="login", max_attempts=3, window_seconds=60.0)

    # Pruning: add 3 IPs to limiter with max_stored_ips=2, old expired entries get pruned
    limiter_a.buckets["login"]["::1"] = [time.time() - 100]  # Expired
    await limiter_a.check(client_ip="192.168.1.1", scope="login", max_attempts=5, window_seconds=60.0)
    await limiter_a.check(client_ip="192.168.1.2", scope="login", max_attempts=5, window_seconds=60.0)
    await limiter_a.check(client_ip="192.168.1.3", scope="login", max_attempts=5, window_seconds=60.0)
    # Stale entry for ::1 was pruned
    assert "::1" not in limiter_a.buckets["login"]


@pytest.mark.asyncio
async def test_rate_limiter_fail_closed_in_production(monkeypatch):
    """Phase 2 Verification: Verify rate limiter fails closed (503) if an unexpected error occurs in production."""
    from services.auth.src.rate_limiter import check_sliding_window_rate_limit, _default_limiter
    from starlette.requests import Request
    from fastapi import HTTPException

    monkeypatch.setenv("ENVIRONMENT", "production")

    scope = {
        "type": "http",
        "client": ("127.0.0.1", 12345),
        "headers": [],
    }
    req = Request(scope)

    # Simulate an unexpected internal error during rate checking
    async def broken_check(*args, **kwargs):
        raise RuntimeError("Unexpected internal limiter memory fault")

    monkeypatch.setattr(_default_limiter, "check", broken_check)

    with pytest.raises(HTTPException) as exc:
        await check_sliding_window_rate_limit(req, scope="login")
    assert exc.value.status_code == 503
    assert "temporarily unavailable" in exc.value.detail


def test_trusted_proxies_validation(monkeypatch):
    """Phase 3 Verification: Verify TRUSTED_PROXIES format validator fails startup on malformed entries in production."""
    from services.auth.src.rate_limiter import validate_trusted_proxies_config

    monkeypatch.setenv("ENVIRONMENT", "production")

    # Invalid CIDR format fails
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1,invalid-ip-format/99")
    with pytest.raises(RuntimeError, match="Invalid IP/CIDR in TRUSTED_PROXIES"):
        validate_trusted_proxies_config()

    # Valid IPv4, IPv6, and CIDR passes
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12")
    validate_trusted_proxies_config()  # Should not raise


@pytest.mark.asyncio
async def test_separate_api_audience_and_client_id(monkeypatch):
    """Phase 4 Verification: Verify distinct API audience and frontend client ID architecture."""
    distinct_api_aud = "api://separate-api-resource-guid-9999"
    monkeypatch.setenv("MICROSOFT_API_AUDIENCE", distinct_api_aud)
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", TEST_CLIENT_ID)

    # 1. Token targeted at frontend client_id (instead of API audience) is REJECTED
    token_wrong_aud = create_mock_ms_token(aud=TEST_CLIENT_ID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token_wrong_aud},
        )
        assert res.status_code == 401
        assert "invalid microsoft token" in res.json()["detail"].lower()

    # 2. Token targeted at distinct API audience is ACCEPTED
    token_correct_aud = create_mock_ms_token(aud=distinct_api_aud)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": token_correct_aud},
        )
        assert res.status_code == 200
        assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_key_rotation_handling():
    """Phase 4 Verification: Verify token validation succeeds across rotated signing keys."""
    # Token 1 signed with Key 1
    token_key1 = create_mock_ms_token(signing_key=_rsa_private_key)
    # Token 2 signed with Key 2 (simulating a rotated Microsoft signing key)
    token_key2 = create_mock_ms_token(signing_key=_other_rsa_private_key)

    # Verify Token 1 with Key 1 public key
    claims1 = verify_microsoft_token(token_key1, custom_signing_key=_rsa_public_key)
    assert claims1["email"] == "alice@appzlogic.com"

    # Verify Token 2 with rotated Key 2 public key
    claims2 = verify_microsoft_token(token_key2, custom_signing_key=_other_rsa_private_key.public_key())
    assert claims2["email"] == "alice@appzlogic.com"


@pytest.mark.asyncio
async def test_guest_and_external_domain_rejection(monkeypatch):
    """
    Security Audit: External Guest / Non-Employee Domain Rejection.
    Ensures that an invited B2B guest or external account (e.g. contractor@external.com,
    guest@gmail.com) present in the authorized tenant is strictly rejected if their email
    domain does not match the configured MICROSOFT_ALLOWED_DOMAINS (default: appzlogic.com).
    """
    monkeypatch.setenv("MICROSOFT_ALLOWED_DOMAINS", "appzlogic.com")
    transport = ASGITransport(app=app)

    # 1. External guest user in the tenant with unauthorized domain (@external.com)
    guest_token = create_mock_ms_token(
        email="contractor@external.com",
        sub="external-guest-oid-999",
        oid="external-guest-oid-999",
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": guest_token},
        )
        assert res.status_code == 403
        assert "access restricted" in res.json()["detail"].lower()
        assert "@appzlogic.com" in res.json()["detail"].lower()

    # 2. Legitimate employee account with @appzlogic.com succeeds
    employee_token = create_mock_ms_token(
        email="developer@appzlogic.com",
        sub="employee-oid-100",
        oid="employee-oid-100",
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": employee_token},
        )
        assert res.status_code == 200
        assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_domain_restriction_wildcard_bypass(monkeypatch):
    """
    Security Audit: When MICROSOFT_ALLOWED_DOMAINS is explicitly set to '*',
    all valid accounts in the single tenant (including invited guests) are permitted.
    """
    monkeypatch.setenv("MICROSOFT_ALLOWED_DOMAINS", "*")
    transport = ASGITransport(app=app)

    guest_token = create_mock_ms_token(
        email="partner@consulting.com",
        sub="partner-guest-oid-888",
        oid="partner-guest-oid-888",
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/microsoft",
            json={"access_token": guest_token},
        )
        assert res.status_code == 200
        assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_get_microsoft_config(monkeypatch):
    """
    Verify public GET /api/auth/microsoft/config returns public client IDs
    and authority for SPA initialization.
    """
    monkeypatch.setenv("MICROSOFT_TENANT_ID", TEST_TENANT_ID)
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", TEST_CLIENT_ID)
    monkeypatch.delenv("MICROSOFT_AUTHORITY", raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/auth/microsoft/config")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert data["client_id"] == TEST_CLIENT_ID
        assert data["tenant_id"] == TEST_TENANT_ID
        assert data["api_scope"] == f"api://{TEST_CLIENT_ID}/access_as_user"
        assert TEST_TENANT_ID in data["authority"]




